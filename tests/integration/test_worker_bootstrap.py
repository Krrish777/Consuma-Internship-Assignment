"""Worker bootstrap integration test (L3, all four backing services).

Proves build_context() wires every adapter the handlers need against real
containers, and that Semaphore.ensure_slots() seeds exactly N tokens
on boot and stays idempotent across a simulated worker restart.
"""

from __future__ import annotations

import pytest
from minio import Minio
from sqlalchemy import text

from core.config import Settings
from core.infra import redis as redis_infra
from core.infra.redis import SLOTS_KEY
from core.infra.storage import BUCKET
from worker.bootstrap import WorkerContext, build_context, close_context

pytestmark = pytest.mark.integration


async def _flush(settings: Settings) -> None:
    raw = redis_infra.get_redis(settings.REDIS_URL)
    await raw.flushdb()
    await raw.aclose()


async def test_worker_bootstrap_wires_all_adapters(worker_stack: Settings) -> None:
    await _flush(worker_stack)

    ctx = await build_context(worker_stack)
    try:
        assert isinstance(ctx, WorkerContext)

        # Redis client live + semaphore seeded exactly N on boot (ensure_slots).
        assert await redis_infra.ping(ctx.redis) is True
        assert await ctx.redis.llen(SLOTS_KEY) == worker_stack.TTS_CONCURRENCY

        # Broker channel usable (full topology declared, exchange grabbed).
        assert ctx.channel is not None and not ctx.channel.is_closed
        assert ctx.exchange is not None

        # DB engine connects.
        async with ctx.engine.connect() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1

        # MinIO client built + bucket ensured.
        assert isinstance(ctx.minio, Minio)
        assert ctx.minio.bucket_exists(BUCKET)
    finally:
        await close_context(ctx)


async def test_ensure_slots_seeds_once_across_restart(worker_stack: Settings) -> None:
    await _flush(worker_stack)

    ctx1 = await build_context(worker_stack)
    await close_context(ctx1)

    # A second bootstrap (worker restart) must NOT re-seed to 2N — ensure_slots
    # is init-once, not top-up.
    ctx2 = await build_context(worker_stack)
    try:
        assert await ctx2.redis.llen(SLOTS_KEY) == worker_stack.TTS_CONCURRENCY
    finally:
        await close_context(ctx2)
