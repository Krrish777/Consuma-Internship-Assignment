"""Worker bootstrap / dependency wiring.

One place that constructs and wires every adapter the pipeline handlers need —
broker connection + channel + exchange, the Postgres engine, a single shared
Redis client (plus the leased ``Semaphore`` and content ``Cache`` built on it),
and the MinIO client — from a single ``Settings``. Handlers receive this bundle
(``WorkerContext``) by injection, which keeps them unit/integration testable and
keeps the worker dependent on ``core`` only, never the gateway.

Two boot-time side effects belong here and nowhere else:
  * ``configure_logging()`` — once per process, so every line carries job_id.
  * ``Semaphore.ensure_slots()`` — the idempotent, exactly-once token seed.
    N workers all call it on boot; the atomic Lua marker guarantees the pool
    converges to exactly N tokens (never N×slots).
"""

from __future__ import annotations

from dataclasses import dataclass

from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractRobustConnection
from minio import Minio
from sqlalchemy.ext.asyncio import AsyncEngine

from core.config import Settings, get_settings
from core.infra import broker
from core.infra.db import get_engine
from core.infra.logging import configure_logging, get_logger
from core.infra.redis import Cache, Redis, Semaphore, get_redis
from core.infra.storage import ensure_bucket

log = get_logger("worker.bootstrap")


@dataclass
class WorkerContext:
    """The wired adapter bundle every handler closes over (injected, not global)."""

    settings: Settings
    connection: AbstractRobustConnection
    channel: AbstractChannel
    exchange: AbstractExchange
    engine: AsyncEngine
    redis: Redis
    semaphore: Semaphore
    cache: Cache
    minio: Minio


async def build_context(settings: Settings | None = None) -> WorkerContext:
    """Construct and wire every adapter the handlers need.

    ``settings`` defaults to the cached ``get_settings()`` (the production path);
    tests inject a ``Settings`` pointed at ephemeral containers. Logging is
    configured and the TTS semaphore pool is seeded (exactly-once) right here.
    """
    settings = settings or get_settings()
    configure_logging()
    log.info("worker bootstrap: wiring adapters")

    connection = await broker.connect(settings.RABBITMQ_URL)
    channel = await connection.channel()
    exchange = await broker.declare_full(channel, retry_delays=settings.retry_delays)

    engine = get_engine(settings.DATABASE_URL)

    redis = get_redis(settings.REDIS_URL)
    semaphore = Semaphore(redis, settings.TTS_CONCURRENCY, lease_ttl=settings.LEASE_TTL_S)
    await semaphore.ensure_slots()  # idempotent exactly-once seed across all workers
    cache = Cache(redis, ttl=settings.CACHE_TTL_S)

    minio = Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS,
        secret_key=settings.MINIO_SECRET,
        secure=False,
    )
    await ensure_bucket(minio)

    log.info("worker bootstrap: complete")
    return WorkerContext(
        settings=settings,
        connection=connection,
        channel=channel,
        exchange=exchange,
        engine=engine,
        redis=redis,
        semaphore=semaphore,
        cache=cache,
        minio=minio,
    )


async def close_context(ctx: WorkerContext) -> None:
    """Tear down the wired adapters cleanly (broker → engine → redis)."""
    await ctx.connection.close()
    await ctx.engine.dispose()
    await ctx.redis.aclose()
    log.info("worker bootstrap: torn down")
