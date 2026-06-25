"""Redis adapter — ephemeral coordination.

The last unbuilt infra adapter. Owns three "safe-to-lose / rebuildable" jobs
(state-placement golden rule — durable truth stays in Postgres):
  - the global TTS semaphore (Constraint A: max 3 concurrent across ALL workers)
  - the content-hash TTS cache (Constraint B: cost-idempotency)
  - the idempotency fast-path (task:done SETNX, a non-authoritative optimisation)

Stack note (verified June 2026): redis-py 8.0.1, ``import redis.asyncio as redis``.
``redis.from_url(url)`` is NOT awaited (it returns a client). Close with
``await client.aclose()`` (8.x renamed it from ``close()``). ``aioredis`` is dead —
merged into redis-py; never import it.

Redis keys: tts:slots, tts:lease:<token>, tts:cache:<sha256>, tts:inflight:<sha256>,
task:done:<task_id>.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis, from_url

from core.infra.logging import get_logger

__all__ = ["Cache", "Redis", "Semaphore", "get_redis", "ping", "seen_once"]

_log = get_logger(__name__)

SLOTS_KEY = "tts:slots"
CACHE_PREFIX = "tts:cache:"
INFLIGHT_PREFIX = "tts:inflight:"
TASK_DONE_PREFIX = "task:done:"
DEFAULT_CACHE_TTL = 86_400  # 24h; MUST stay <= the MinIO object lifetime
DEFAULT_INFLIGHT_TTL = 60  # in-flight lock auto-expires if a synthesiser crashes
DEFAULT_SEEN_TTL = 86_400  # how long the fast-path remembers a processed task_id


def _as_str(value: bytes | str) -> str:
    """Normalise a Redis reply to str (decode_responses=False yields bytes)."""
    return value.decode() if isinstance(value, bytes) else value


# Atomic, exactly-once pool seeding. Runs server-side with no interleaving, so
# any number of racing workers converge to exactly N tokens. KEYS[1]=slots list,
# KEYS[2]=init marker, ARGV[1]=N. Init-once (guarded by the marker), never top-up.
_ENSURE_SLOTS_LUA = """
if redis.call('GET', KEYS[2]) then
    return 0
end
redis.call('DEL', KEYS[1])
for i = 0, tonumber(ARGV[1]) - 1 do
    redis.call('RPUSH', KEYS[1], tostring(i))
end
redis.call('SET', KEYS[2], '1')
return 1
"""


# Atomic, owner-safe reclaim of one orphaned token (X5). KEYS[1]=slots list,
# KEYS[2]=lease key, ARGV[1]=token. Returns the token to the pool ONLY if its lease
# is gone (holder dead/expired) AND it is not already in the pool — so two reapers
# racing the same token return it at most once (the second sees it already present).
_RECLAIM_LUA = """
if redis.call('EXISTS', KEYS[2]) == 1 then
    return 0
end
local items = redis.call('LRANGE', KEYS[1], 0, -1)
for _, v in ipairs(items) do
    if v == ARGV[1] then
        return 0
    end
end
redis.call('RPUSH', KEYS[1], ARGV[1])
return 1
"""


def get_redis(url: str) -> Redis:
    """Build the shared async client from REDIS_URL.

    ``decode_responses=False`` keeps everything as bytes: we store and byte-compare
    sha256 hashes and opaque semaphore tokens, where silent str decoding would only
    get in the way. Build the client ONCE and share it (X3 worker bootstrap) — never
    one client per operation. ``from_url`` returns a client immediately; it is NOT
    awaited.
    """
    return from_url(url, decode_responses=False)


async def ping(client: Redis) -> bool:
    """Liveness check — True when the server answers PONG."""
    return bool(await client.ping())


async def seen_once(client: Redis, task_id: str, *, ttl: int = DEFAULT_SEEN_TTL) -> bool:
    """Redis idempotency fast-path helper: True the FIRST time a task_id is seen, else False.

    ``SET task:done:<task_id> 1 NX EX`` — a cheap short-circuit for obviously duplicate
    deliveries. NON-authoritative and currently NOT on the hot path: the pipeline's
    idempotency authority is the atomic state-CAS in ``core.infra.queries`` (durable,
    in the same transaction as the effect it guards). Redis is "safe to lose", so on a
    cold Redis this returns True again — which is exactly why it could never protect the
    fan-in counter. Kept as an optional building block; never let it gate the counter.
    """
    won = await client.set(f"{TASK_DONE_PREFIX}{task_id}", "1", nx=True, ex=ttl)
    return bool(won)


class Semaphore:
    """Distributed leased N-token semaphore — the global TTS limit (Constraint A).

    Bounds TTS concurrency to ``slots`` *across all workers/containers*, which an
    in-process ``asyncio.Semaphore`` cannot do. The pool is a Redis list pre-seeded
    with N opaque tokens (X4 seeds it exactly-once):

      - **acquire** = ``BLPOP`` the slots list. Atomically removes one token or
        *blocks* until one is released — no busy-poll, and the count can never go
        negative (you can't pop an empty list). The popped token is then recorded
        as a TTL **lease** (``tts:lease:<token>``) so a crashed holder's slot can be
        reclaimed by the X5 reaper instead of leaking forever.
      - **release** = delete the lease, then ``RPUSH`` the token back. Order matters:
        clearing the lease *before* returning the token guarantees the next acquirer's
        ``SET ... NX`` on the lease key succeeds.

    Callers MUST check the content cache BEFORE ``acquire`` (W4 order) — a cache hit
    must not burn a token (SPEC §4).

    Soft limit (H6 honesty): this is a *best-effort* global limit, not a perfectly
    hard one — a distributed semaphore cannot be hard without consensus. A live-but-
    slow holder is protected by a heartbeat that renews its lease at ⅓-TTL; a dead
    holder's lease expires and the X5 reaper returns its token. In the rare window
    where a healthy holder stalls past its (heartbeated) TTL, a reap can briefly
    allow slots+1 concurrent — ``reap`` logs such reclaims so breaches are visible.
    """

    def __init__(
        self,
        client: Redis,
        slots: int = 3,
        *,
        lease_ttl: int = 30,
        slots_key: str = SLOTS_KEY,
    ) -> None:
        self._client = client
        self.slots = slots
        self.lease_ttl = lease_ttl
        self.slots_key = slots_key
        self._init_marker = f"{slots_key}:init"
        # token -> its heartbeat task, so release() can stop renewing the lease.
        self._heartbeats: dict[str, asyncio.Task[None]] = {}
        # register_script is sync; it just wraps the source + SHA for later EVALSHA.
        self._ensure_slots_script = client.register_script(_ENSURE_SLOTS_LUA)
        self._reclaim_script = client.register_script(_RECLAIM_LUA)

    def _lease_key(self, token: str) -> str:
        return f"tts:lease:{token}"

    async def ensure_slots(self) -> None:
        """Seed the pool with exactly N tokens, exactly once across ALL workers.

        Idempotent and convergent under concurrency (atomic Lua + init marker): the
        first caller seeds tokens "0".."N-1"; every later call — including reboots —
        is a no-op. This is deliberately *init-once, not top-up*: re-seeding tokens
        that have since been acquired would recreate the 3xN over-provisioning bug.
        """
        await self._ensure_slots_script(
            keys=[self.slots_key, self._init_marker],
            args=[self.slots],
        )

    async def acquire(self, owner: str, *, timeout: float = 0.0) -> str | None:
        """Block (``BLPOP``) for a free slot; return its token, or None on timeout.

        ``timeout=0`` blocks indefinitely (the production default). A finite timeout
        returns None when no slot frees up in time — the caller decides whether to
        retry or shed load. The returned token doubles as the handle for ``release``.
        """
        raw = await self._client.blpop([self.slots_key], timeout=timeout)
        if raw is None:
            return None
        token = _as_str(raw[1])
        # Record the lease so a crashed holder's slot auto-expires (H6 / X5)...
        await self._client.set(self._lease_key(token), owner, nx=True, ex=self.lease_ttl)
        # ...and keep it alive while we hold it, so a slow-but-healthy holder is
        # never reclaimed out from under itself.
        self._heartbeats[token] = asyncio.create_task(self._heartbeat(token, owner))
        return token

    async def release(self, token: str) -> None:
        """Return a held token to the pool: stop the heartbeat, clear its lease, RPUSH.

        Cancelling the heartbeat first prevents a renew from racing the delete; the
        delete-before-RPUSH order then guarantees the next acquirer's ``SET ... NX``
        on the lease succeeds.
        """
        heartbeat = self._heartbeats.pop(token, None)
        if heartbeat is not None:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        await self._client.delete(self._lease_key(token))
        await self._client.rpush(self.slots_key, token)

    async def _heartbeat(self, token: str, owner: str) -> None:
        """Renew the lease every ⅓-TTL with ``SET ... XX`` (renew only if still held).

        Runs until ``release`` cancels it. XX means a lease that has already expired
        is NOT resurrected — if we ever fall behind past the TTL, the reaper rightly
        wins the token (the documented soft-limit edge).
        """
        interval = self.lease_ttl / 3
        while True:
            await asyncio.sleep(interval)
            await self._client.set(self._lease_key(token), owner, xx=True, ex=self.lease_ttl)

    async def reap(self) -> int:
        """Reclaim tokens whose holder died (lease expired) but never returned them.

        Each token is reclaimed under an atomic owner-safe Lua step, so concurrent
        reapers return any given token at most once. Returns the number reclaimed.
        """
        reclaimed = 0
        for i in range(self.slots):
            token = str(i)
            result = await self._reclaim_script(
                keys=[self.slots_key, self._lease_key(token)],
                args=[token],
            )
            reclaimed += int(result)
        if reclaimed:
            _log.warning(
                "tts semaphore reclaimed %d orphaned lease(s); global limit is "
                "best-effort/soft — reclaiming past a live-but-slow holder can "
                "briefly exceed %d concurrent",
                reclaimed,
                self.slots,
            )
        return reclaimed

    @asynccontextmanager
    async def slot(self, owner: str, *, timeout: float = 0.0) -> AsyncIterator[str | None]:
        """``async with sem.slot(owner) as token:`` — release even on exception.

        Yields the token (or None if a finite timeout elapsed). Releases only when a
        token was actually acquired, so a timed-out acquire never RPUSHes a phantom.
        """
        token = await self.acquire(owner, timeout=timeout)
        try:
            yield token
        finally:
            if token is not None:
                await self.release(token)


class Cache:
    """Content-hash TTS cache (Constraint B: cost-idempotency).

    Maps ``tts:cache:<sha256(text)>`` -> the prior MinIO object URL/key, with a TTL.
    Identical text synthesised twice must NOT re-hit the vendor (SPEC §1): the W4
    handler consults this BEFORE acquiring a semaphore slot, so a hit burns no token.

    Keyed on D4's canonical ``content_hash(text)``, never the task_id — conflating
    the cost cache with the fan-in counter is the named junior trap. This is NOT
    durable truth: it is rebuildable from MinIO and TTL'd, and the TTL MUST stay
    <= the MinIO object lifetime so a HIT never returns a dangling key (H-DANGLE).
    """

    def __init__(
        self,
        client: Redis,
        *,
        ttl: int = DEFAULT_CACHE_TTL,
        inflight_ttl: int = DEFAULT_INFLIGHT_TTL,
    ) -> None:
        self._client = client
        self.ttl = ttl
        self.inflight_ttl = inflight_ttl

    def _key(self, content_hash: str) -> str:
        return f"{CACHE_PREFIX}{content_hash}"

    def _inflight_key(self, content_hash: str) -> str:
        return f"{INFLIGHT_PREFIX}{content_hash}"

    async def cache_get(self, content_hash: str) -> str | None:
        """Return the cached MinIO URL for this content hash, or None on a miss."""
        raw = await self._client.get(self._key(content_hash))
        return None if raw is None else _as_str(raw)

    async def cache_set(self, content_hash: str, url: str) -> None:
        """Record url for this content hash with the configured TTL.

        Uses ``SET key url EX ttl`` — the standalone ``SETEX`` command is deprecated
        in redis-py 8 in favour of ``SET``'s ``ex=`` option (same atomic effect).
        """
        await self._client.set(self._key(content_hash), url, ex=self.ttl)

    async def acquire_inflight(self, content_hash: str, owner: str) -> bool:
        """Try to become the single synthesiser for this content hash (H8).

        ``SET tts:inflight:<hash> owner NX EX`` — returns True only for the first
        caller of a concurrent identical-block burst; that caller synthesises while
        the losers (False) wait on :meth:`wait_for_cache`. The TTL bounds the lock so
        a crashed synthesiser cannot wedge the burst forever.

        MUST be checked WITHOUT holding a TTS semaphore slot — a waiter that held a
        slot would starve the very synthesiser it is waiting on (pool deadlock).
        """
        won = await self._client.set(
            self._inflight_key(content_hash), owner, nx=True, ex=self.inflight_ttl
        )
        return bool(won)

    async def release_inflight(self, content_hash: str) -> None:
        """Drop the in-flight lock after populating the cache (or let the TTL do it)."""
        await self._client.delete(self._inflight_key(content_hash))

    async def wait_for_cache(
        self, content_hash: str, *, attempts: int = 100, poll: float = 0.1
    ) -> str | None:
        """Bounded wait for the winning synthesiser to populate the cache.

        Polls :meth:`cache_get` up to ``attempts`` times. Returns the url once
        present, or None if the budget elapses (caller may then retry the in-flight
        race — e.g. if the original synthesiser crashed and its lock expired).
        """
        for _ in range(attempts):
            url = await self.cache_get(content_hash)
            if url is not None:
                return url
            await asyncio.sleep(poll)
        return None
