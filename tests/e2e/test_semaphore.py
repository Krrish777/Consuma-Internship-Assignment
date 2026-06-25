"""R4.1 — global TTS semaphore probe (L4): one shared limit across N workers.

Constraint A: at most ``TTS_CONCURRENCY`` (3) TTS calls run concurrently across
ALL workers, not per-process. The limit lives in Redis as a leased N-token list
seeded exactly once (X4's atomic ``ensure_slots`` defeats the 3×N footgun), so
scaling the worker service does not multiply the budget.

Measurement note: the sim's vendor latency is ``asyncio.sleep(0)``, so a slot is
held for microseconds — external sampling can never catch a live peak. The
deterministic, honest proof is the **global-pool invariant**: with 4 real workers
running, the token pool totals exactly 3 (not 4×3). Combined with the BLPOP acquire
(a token must be popped to run, and only 3 exist), that *structurally* bounds peak
concurrency at 3. The live-peak and crashed-holder-reclaim (X5) behaviours are
L3-proven (test_redis semaphore/reaper). Also covers I4 (scaled replicas share one
semaphore).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx
import pytest
from redis.asyncio import Redis

from .helpers import scale_workers

pytestmark = pytest.mark.e2e

TTS_CONCURRENCY = 3
SLOTS_KEY = "tts:slots"


async def _slot_pool(redis_client: Redis) -> int:
    return int(await redis_client.llen(SLOTS_KEY))


async def test_scaled_workers_share_one_global_semaphore(
    client: httpx.AsyncClient,
    wait_for_status: Callable[..., Awaitable[str]],
    redis_client: Redis,
) -> None:
    scale_workers(4)
    try:
        # Idle global pool must be exactly TTS_CONCURRENCY across all 4 workers —
        # NOT 4×3. New replicas' ensure_slots is an idempotent no-op (init marker
        # already set), so the shared budget never inflates. Poll to let replicas boot.
        pool = -1
        for _ in range(45):
            pool = await _slot_pool(redis_client)
            if pool == TTS_CONCURRENCY:
                break
            await asyncio.sleep(2.0)
        assert pool == TTS_CONCURRENCY, (
            f"global slot pool is {pool}, expected {TTS_CONCURRENCY} across 4 workers "
            "(per-worker seeding would give 12 — the 3×N footgun)"
        )

        # The shared 3-slot limit must still drain a burst (no deadlock): 4 jobs ×
        # 4 blocks = 16 TTS tasks funnel through 3 global slots and all complete.
        async def submit(i: int) -> str:
            manuscript = "\n\n".join(f"Job {i} block {b}." for b in range(4))
            resp = await client.post("/jobs", json={"manuscript": manuscript})
            assert resp.status_code == 202, resp.text
            return str(resp.json()["job_id"])

        job_ids = await asyncio.gather(*(submit(i) for i in range(4)))
        for jid in job_ids:
            status = await wait_for_status(jid, target="COMPLETED", timeout=120.0)
            assert status == "COMPLETED", f"job {jid} ended {status} under the shared limit"

        # After the burst drains, every token is back in the one global pool.
        assert await _slot_pool(redis_client) == TTS_CONCURRENCY, "tokens leaked after burst"
    finally:
        scale_workers(1)  # restore the single-worker baseline for later probes
