"""PENDING-sweeper / reconciler (closes the gateway dual-write seam).

``POST /jobs`` does MinIO → DB commit → publish, but commit-then-publish is not
atomic. A crash in that window leaves an **orphaned PENDING job whose JobCreated
was never published** — it would never progress. "Ack last" is a *consumer* rule
and cannot cover the *producer*. So a periodic sweeper treats the Job row as its
own outbox: any job stuck in PENDING past a generous timeout gets its JobCreated
re-published.

Re-publishing is safe **only because parse is idempotent and re-runnable** (
ON CONFLICT task inserts + begin_parse seeds pending_count only on the first
CAS out of PENDING). The sweeper itself stays dumb: it **only re-publishes, never
mutates job status** — advancing the FSM is the consumer's job.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from aio_pika.abc import AbstractExchange
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from core.domain.events import JobCreated
from core.domain.state import JobStatus
from core.infra import broker
from core.infra.db import Job, get_session, purge_processed_events
from core.infra.logging import get_logger

log = get_logger("gateway.sweeper")


async def sweep_once(
    *, engine: AsyncEngine, exchange: AbstractExchange, pending_timeout_s: int
) -> int:
    """Re-publish ``JobCreated`` for every job stuck PENDING past the timeout.

    Returns the number of jobs re-driven. The staleness cutoff uses DB-side
    ``now()`` (immune to app/DB clock skew, mirroring purge_processed_events).
    The generous ``pending_timeout_s`` (≫ normal parse latency) ensures the
    sweeper never races a healthy in-flight job. This function does NOT change
    job status — it only re-publishes; the consumer advances the state.
    """
    cutoff = func.now() - timedelta(seconds=pending_timeout_s)
    async with get_session(engine) as session:
        result = await session.execute(
            select(Job.job_id).where(Job.status == JobStatus.PENDING, Job.created_at < cutoff)
        )
        stale_ids = list(result.scalars().all())

    for job_id in stale_ids:
        await broker.publish(exchange, JobCreated(job_id=job_id), routing_key=broker.Q_PARSE)
        log.info("sweeper re-published JobCreated", extra={"job_id": job_id})

    if stale_ids:
        log.info("sweeper pass re-drove stale PENDING jobs", extra={"count": len(stale_ids)})
    return len(stale_ids)


async def purge_once(*, engine: AsyncEngine, retention_s: int) -> int:
    """Delete processed_events inbox rows older than ``retention_s``.

    Returns the number of rows deleted. The inbox is the durable exactly-once
    authority (``mark_event``); without retention it grows unbounded. Kept separate
    from ``sweep_once`` (which stays re-publish-only) so each
    concern is independently tested. ``get_session`` does not auto-commit, so the
    DELETE is committed explicitly. The cutoff uses DB-side ``now()`` inside
    ``purge_processed_events`` (clock-skew-immune).
    """
    async with get_session(engine) as session:
        deleted = await purge_processed_events(session, retention_s)
        await session.commit()
    if deleted:
        log.info("sweeper purged expired processed_events", extra={"count": deleted})
    return deleted


async def run_sweeper(
    *,
    engine: AsyncEngine,
    exchange: AbstractExchange,
    interval_s: int,
    pending_timeout_s: int,
    retention_s: int,
) -> None:
    """Loop the reconciler every ``interval_s`` until cancelled (lifespan-managed).

    Each pass does two independent jobs: ``sweep_once`` re-drives orphaned PENDING
    jobs, and ``purge_once`` trims the processed_events inbox past its retention
    window. Sleeps *before* the first pass so a freshly-started gateway
    doesn't fire a redundant pass on boot, and so tests with a long interval never
    trigger it. Each job is wrapped separately so a failure in one never skips the
    other, and a failing pass is logged and swallowed — one bad pass must never kill
    the reconciler loop.
    """
    while True:
        await asyncio.sleep(interval_s)
        try:
            await sweep_once(engine=engine, exchange=exchange, pending_timeout_s=pending_timeout_s)
        except Exception:  # noqa: BLE001 — the loop must outlive any single pass
            log.exception("sweeper pass failed")
        try:
            await purge_once(engine=engine, retention_s=retention_s)
        except Exception:  # noqa: BLE001 — the loop must outlive any single pass
            log.exception("purge pass failed")
