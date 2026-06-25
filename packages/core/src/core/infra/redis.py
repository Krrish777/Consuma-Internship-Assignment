"""Redis adapter — ephemeral coordination (spec §5, §7).

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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis, from_url

__all__ = ["Redis", "Semaphore", "get_redis", "ping"]

SLOTS_KEY = "tts:slots"


def _as_str(value: bytes | str) -> str:
    """Normalise a Redis reply to str (decode_responses=False yields bytes)."""
    return value.decode() if isinstance(value, bytes) else value


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

    def _lease_key(self, token: str) -> str:
        return f"tts:lease:{token}"

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
        # Record the lease so a crashed holder's slot auto-expires (H6 / X5).
        await self._client.set(self._lease_key(token), owner, nx=True, ex=self.lease_ttl)
        return token

    async def release(self, token: str) -> None:
        """Return a held token to the pool: clear its lease, then RPUSH it back."""
        await self._client.delete(self._lease_key(token))
        await self._client.rpush(self.slots_key, token)

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
