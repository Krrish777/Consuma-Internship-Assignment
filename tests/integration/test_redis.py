"""Phase 2 Redis coordination — integration tests (Redis via testcontainers).

One module-scoped real Redis container backs every card here (R1 client, R2
semaphore, X4 init, X5 reaper, R3 cache, H8 stampede, R4inbox fast-path). Each
test flushes the keyspace first so cards don't bleed state into each other.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator

import pytest
from testcontainers.redis import RedisContainer

from core.domain.hash import content_hash
from core.infra import redis as redis_infra
from core.infra.redis import Cache, Semaphore

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture
async def client(redis_url: str) -> AsyncIterator[redis_infra.Redis]:
    c = redis_infra.get_redis(redis_url)
    await c.flushdb()
    try:
        yield c
    finally:
        await c.aclose()


# --- R1: client adapter -------------------------------------------------------


async def test_get_redis_pings(client: redis_infra.Redis) -> None:
    assert await redis_infra.ping(client) is True


async def test_get_redis_keeps_bytes(client: redis_infra.Redis) -> None:
    # decode_responses=False -> GET returns raw bytes (we store hashes/tokens).
    await client.set("k", "v")
    assert await client.get("k") == b"v"


# --- R2: leased N-token semaphore --------------------------------------------


async def test_semaphore_bounds_to_slots(client: redis_infra.Redis) -> None:
    # X4 will seed the pool in production; here we seed it by hand.
    await client.rpush("tts:slots", "0", "1")
    sem = Semaphore(client, slots=2, lease_ttl=30)

    t0 = await sem.acquire("w0")
    t1 = await sem.acquire("w1")
    assert t0 is not None and t1 is not None
    assert {t0, t1} == {"0", "1"}

    # Pool is empty: a 3rd acquire must BLOCK, not hand out a 3rd token.
    assert await sem.acquire("w2", timeout=1) is None

    # Release one -> the 3rd acquire now succeeds with the returned token.
    await sem.release(t0)
    t2 = await sem.acquire("w2", timeout=2)
    assert t2 == t0

    assert t2 is not None
    await sem.release(t1)
    await sem.release(t2)


async def test_acquire_records_lease_with_ttl(client: redis_infra.Redis) -> None:
    await client.rpush("tts:slots", "0")
    sem = Semaphore(client, slots=1, lease_ttl=30)

    token = await sem.acquire("worker-A")
    assert token is not None
    ttl = await client.ttl(f"tts:lease:{token}")
    assert 0 < ttl <= 30
    assert await client.get(f"tts:lease:{token}") == b"worker-A"
    await sem.release(token)


async def test_release_clears_lease_and_returns_token(client: redis_infra.Redis) -> None:
    await client.rpush("tts:slots", "0")
    sem = Semaphore(client, slots=1, lease_ttl=30)

    token = await sem.acquire("w0")
    assert token is not None
    assert await client.llen("tts:slots") == 0  # token is out
    await sem.release(token)
    assert await client.llen("tts:slots") == 1  # token is back
    assert await client.exists(f"tts:lease:{token}") == 0  # lease cleared


async def test_slot_context_manager_releases_on_exception(client: redis_infra.Redis) -> None:
    await client.rpush("tts:slots", "0")
    sem = Semaphore(client, slots=1, lease_ttl=30)

    with pytest.raises(RuntimeError):
        async with sem.slot("w0") as token:
            assert token == "0"
            assert await client.llen("tts:slots") == 0
            raise RuntimeError("boom mid-slot")

    # Even though the body raised, the token must be back in the pool.
    assert await client.llen("tts:slots") == 1
    assert await client.exists("tts:lease:0") == 0


# --- X4: semaphore init idempotency (the 3xN-tokens bug) ----------------------


async def test_ensure_slots_converges_to_exactly_n_under_concurrency(
    client: redis_infra.Redis,
) -> None:
    # 5 workers race to seed the SAME pool concurrently. The naive RPUSH-N-on-boot
    # would leave 5*N tokens; atomic Lua must converge to exactly N.
    workers = [Semaphore(client, slots=3, lease_ttl=30) for _ in range(5)]
    await asyncio.gather(*(w.ensure_slots() for w in workers))

    assert await client.llen("tts:slots") == 3
    tokens = await client.lrange("tts:slots", 0, -1)
    assert sorted(redis_infra._as_str(t) for t in tokens) == ["0", "1", "2"]


async def test_ensure_slots_is_init_once_not_top_up(client: redis_infra.Redis) -> None:
    sem = Semaphore(client, slots=3, lease_ttl=30)
    await sem.ensure_slots()

    token = await sem.acquire("w0")  # consume one -> 2 remain
    assert token is not None

    # A second ensure_slots (e.g. a worker reboot) MUST NOT top the pool back to 3 —
    # that would re-introduce the 3xN over-provisioning bug.
    await sem.ensure_slots()
    assert await client.llen("tts:slots") == 2
    await sem.release(token)


# --- X5: lease reaper (heartbeat renew + atomic reclaim) ----------------------


async def test_reap_reclaims_a_crashed_holders_token(client: redis_infra.Redis) -> None:
    sem = Semaphore(client, slots=3, lease_ttl=30)
    # Manufacture an orphan: token "0" is out of the pool and its lease expired (a
    # crashed holder never renewed it). Tokens "1","2" remain free in the pool.
    await client.rpush("tts:slots", "1", "2")
    await client.set("tts:lease:0", "dead-worker", ex=1)
    await asyncio.sleep(1.5)  # lease "0" expires
    assert await client.exists("tts:lease:0") == 0

    reclaimed = await sem.reap()
    assert reclaimed == 1
    tokens = sorted(redis_infra._as_str(t) for t in await client.lrange("tts:slots", 0, -1))
    assert tokens == ["0", "1", "2"]


async def test_reap_leaves_a_live_heartbeating_holder_alone(
    client: redis_infra.Redis,
) -> None:
    sem = Semaphore(client, slots=3, lease_ttl=3)  # heartbeat interval = 1s
    await sem.ensure_slots()
    token = await sem.acquire("alive-worker")
    assert token is not None

    # Outlive the TTL: a healthy holder's heartbeat must keep renewing the lease.
    await asyncio.sleep(4)
    assert await client.exists(f"tts:lease:{token}") == 1  # still leased

    reclaimed = await sem.reap()
    assert reclaimed == 0  # nothing to reap; the holder is alive
    await sem.release(token)


async def test_two_reapers_reclaim_a_token_at_most_once(
    client: redis_infra.Redis,
) -> None:
    sem_a = Semaphore(client, slots=3, lease_ttl=30)
    sem_b = Semaphore(client, slots=3, lease_ttl=30)
    # Orphan token "0"; "1","2" are free in the pool.
    await client.rpush("tts:slots", "1", "2")
    await client.set("tts:lease:0", "dead", ex=1)
    await asyncio.sleep(1.5)

    results = await asyncio.gather(sem_a.reap(), sem_b.reap())
    assert sum(results) == 1  # exactly one reaper returned the token
    assert await client.llen("tts:slots") == 3  # no double-return


# --- R3: content-hash cache (Constraint B) -----------------------------------


async def test_cache_set_then_get_returns_url(client: redis_infra.Redis) -> None:
    cache = Cache(client, ttl=3600)
    h = content_hash("a block of manuscript text")

    assert await cache.cache_get(h) is None  # cold miss
    await cache.cache_set(h, "minio://audio/tts/abc123.wav")
    assert await cache.cache_get(h) == "minio://audio/tts/abc123.wav"


async def test_cache_miss_returns_none(client: redis_infra.Redis) -> None:
    cache = Cache(client, ttl=3600)
    assert await cache.cache_get(content_hash("never stored")) is None


async def test_cache_key_is_content_hash_with_ttl(client: redis_infra.Redis) -> None:
    cache = Cache(client, ttl=3600)
    h = content_hash("keyed on sha256(text), never task_id")
    await cache.cache_set(h, "minio://x")

    ttl = await client.ttl(f"tts:cache:{h}")
    assert 0 < ttl <= 3600  # TTL'd, not durable truth


async def test_cache_entry_expires_after_ttl(client: redis_infra.Redis) -> None:
    cache = Cache(client, ttl=1)
    h = content_hash("ephemeral")
    await cache.cache_set(h, "minio://x")
    assert await cache.cache_get(h) == "minio://x"

    await asyncio.sleep(1.5)
    assert await cache.cache_get(h) is None  # expired -> rebuildable from MinIO


# --- H8: cache-stampede in-flight lock ---------------------------------------


async def test_only_one_caller_wins_the_inflight_lock(client: redis_infra.Redis) -> None:
    cache = Cache(client, ttl=3600)
    h = content_hash("a very popular block")

    # 10 identical blocks race the in-flight lock simultaneously.
    won = await asyncio.gather(*(cache.acquire_inflight(h, f"w{i}") for i in range(10)))
    assert sum(won) == 1  # exactly one synthesiser


async def test_stampede_yields_one_vendor_call_others_read_cache(
    client: redis_infra.Redis,
) -> None:
    cache = Cache(client, ttl=3600)
    h = content_hash("shared block under stampede")
    url = "minio://audio/tts/shared.wav"

    async def caller(owner: str) -> tuple[str, str | None]:
        if await cache.acquire_inflight(h, owner):
            await asyncio.sleep(0.2)  # simulate the one real vendor synthesis
            await cache.cache_set(h, url)
            await cache.release_inflight(h)
            return ("synth", url)
        # Loser: wait for the winner to populate the cache, then reuse it — no
        # vendor call, and crucially no semaphore slot held while waiting.
        return ("cached", await cache.wait_for_cache(h))

    results = await asyncio.gather(*(caller(f"w{i}") for i in range(5)))

    synths = [r for r in results if r[0] == "synth"]
    assert len(synths) == 1  # exactly one vendor call for the whole burst
    assert all(r[1] == url for r in results)  # everyone ends up with the same url
