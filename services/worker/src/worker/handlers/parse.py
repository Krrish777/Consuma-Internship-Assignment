"""Parse handler (Stage A) — the fan-out emitter.

Consumes ``JobCreated``, loads the manuscript from MinIO, simulates the parse
vendor call (15% transient failure injection; a poison manuscript fails every
attempt → DLQ after the retry ladder, per R2.0/SPEC §1), splits it into N blocks,
writes **N Task rows + pending_count=N in ONE transaction**, advances the job to
GENERATING, and **fans out N ``TtsRequested``** events.

Two correctness rules this card exists for:
  * **Re-publishable emitter, never inbox-skipped (H2).** A redelivered JobCreated
    must still emit the N events — a prior crash may have committed the rows but
    lost the publishes. So the task rows use ``ON CONFLICT DO NOTHING`` and the N
    events are published on EVERY delivery. ``begin_parse`` (H15) sets the counter
    only on the first CAS out of PENDING, so a re-run never resurrects the counter.
  * **0-block must terminate (not hang).** An empty manuscript means a fan-in
    barrier of 0; the job still advances (PENDING→PARSING→GENERATING, FSM-legal)
    and a ``StitchReady`` is emitted immediately (the barrier is already crossed),
    so the stitch handler finalises it. A direct PENDING→STITCHING jump would be
    illegal (state.LEGAL); the queued StitchReady is the FSM-legal realisation of
    the card's "0-block → STITCHING directly".

Deterministic ``task_id = f"{job_id}-{i}"`` (block index) keeps redelivery safe:
re-published events always reference the rows already in the DB.
"""

from __future__ import annotations

import asyncio

from aio_pika.abc import AbstractIncomingMessage
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.domain.events import JobCreated, StitchReady, TtsRequested
from core.domain.hash import content_hash
from core.domain.state import JobStatus
from core.domain.vendor import simulate_parse
from core.infra import broker, storage
from core.infra.broker import Handler, Q_PARSE, Q_STITCH, Q_TTS
from core.infra.db import Job, Task, get_session
from core.infra.logging import bind_job_id, get_logger
from core.infra.queries import advance_status, begin_parse
from worker.bootstrap import WorkerContext
from worker.handlers._base import ack_last

log = get_logger("worker.parse")


async def handle_parse(ctx: WorkerContext, event: JobCreated) -> None:
    """Parse → fan out. Idempotent under redelivery; 0-block terminates."""
    job_id = event.job_id

    async with get_session(ctx.engine) as session:
        job = await session.get(Job, job_id)
        if job is None:
            # No durable row for this event — treat as transient (fail-safe): the
            # gateway commits before publishing, so this should not happen, but we
            # retry rather than silently drop in case of replication lag.
            raise RuntimeError(f"parse: job {job_id!r} has no row yet")
        manuscript_key = job.manuscript_key

    text = await storage.get_text(ctx.minio, manuscript_key)
    await asyncio.sleep(0)  # stand-in for vendor latency
    blocks = simulate_parse(text, failure_rate=ctx.settings.PARSE_FAILURE_RATE)

    if len(blocks) > ctx.settings.MAX_BLOCKS:
        log.warning(
            "job %s: %d blocks exceeds MAX_BLOCKS=%d; capping (dropped %d)",
            job_id,
            len(blocks),
            ctx.settings.MAX_BLOCKS,
            len(blocks) - ctx.settings.MAX_BLOCKS,
        )
        blocks = blocks[: ctx.settings.MAX_BLOCKS]
    n = len(blocks)

    # ── DB: N task rows + counter, atomic in ONE transaction ──────────────────
    async with get_session(ctx.engine) as session:
        if n > 0:
            rows = [
                {
                    "task_id": f"{job_id}-{i}",
                    "job_id": job_id,
                    "block_index": i,
                    "block_hash": content_hash(block),
                    "status": "PENDING",
                }
                for i, block in enumerate(blocks)
            ]
            insert_tasks = (
                pg_insert(Task)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["job_id", "block_index"])
            )
            await session.execute(insert_tasks)  # no commit — begin_parse commits both
        # begin_parse CAS sets pending_count=N only on the first run (H15) and
        # commits, flushing the task inserts in the same transaction.
        await begin_parse(session, job_id, n)

    # Advance toward GENERATING (idempotent CAS; rowcount-0 = already advanced).
    async with get_session(ctx.engine) as session:
        await advance_status(session, job_id, JobStatus.GENERATING)

    # ── ALWAYS (re)publish — fan-out is the trigger and must survive redelivery ─
    if n == 0:
        # Barrier already 0: emit StitchReady so the job terminates (no hang).
        await broker.publish(ctx.exchange, StitchReady(job_id=job_id), routing_key=Q_STITCH)
        log.info("job %s: 0-block manuscript → StitchReady emitted", job_id)
    else:
        for i in range(n):
            await broker.publish(
                ctx.exchange,
                TtsRequested(job_id=job_id, task_id=f"{job_id}-{i}"),
                routing_key=Q_TTS,
            )
        log.info("job %s: fanned out %d TtsRequested", job_id, n)


def make_parse_handler(ctx: WorkerContext) -> Handler:
    """Build the parse consumer: validate the event (pointers only), then handle it."""

    async def do_work(message: AbstractIncomingMessage) -> None:
        event = JobCreated.model_validate_json(message.body)
        bind_job_id(event.job_id)
        await handle_parse(ctx, event)

    return ack_last(ctx, Q_PARSE, do_work)
