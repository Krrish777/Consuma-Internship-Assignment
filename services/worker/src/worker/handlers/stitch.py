"""Stitch handler (Stage D) — idempotent finalize.

Consumes ``StitchReady``, concatenates the job's TTS chunks into a single
``out/<job>.mp3``, and marks the job COMPLETED. The webhook notification
extends this module.

Key decisions:
  * **Chunk order comes from the DB, not a MinIO prefix.** Objects are
    content-addressed (``tts/<hash>.wav``, deduped system-wide), so a prefix list
    can't identify or order *this* job's chunks. The Task table is the authority:
    ``WHERE job_id=… AND status='DONE' ORDER BY block_index`` (FAILED blocks are
    skipped — the partial-drama policy).
  * **Client-side concat, NOT ``compose_object``.** MinIO server-side compose
    requires every non-final part ≥ 5 MiB; simulated chunks are tiny. Download-join-
    put is correct for small chunks (each MinIO call is already thread-wrapped).
  * **Idempotent.** A redelivered StitchReady on an already-COMPLETED job
    returns immediately — no double asset, no illegal COMPLETED→COMPLETED. Status
    advances via CAS; a lost CAS means "someone else finalised it", not an error.
"""

from __future__ import annotations

import httpx
from aio_pika.abc import AbstractIncomingMessage
from sqlalchemy import select

from core.domain.events import StitchReady
from core.domain.state import JobStatus
from core.infra import storage
from core.infra.broker import Handler, Q_STITCH
from core.infra.db import Job, Task, get_session
from core.infra.logging import bind_job_id, get_logger
from core.infra.queries import advance_status, finalize_job
from worker.bootstrap import WorkerContext
from worker.handlers._base import ack_last
from worker.ssrf import is_allowed

log = get_logger("worker.stitch")


async def _notify(ctx: WorkerContext, job_id: str) -> None:
    """Best-effort webhook. A failure here MUST NOT fail the job.

    The job is already COMPLETED when this runs, and runs OUTSIDE the handler's
    raise-path so it never rides the retry ladder. Empty allowlist = log-only mode;
    otherwise the URL must pass the SSRF guard before any request is made.
    """
    async with get_session(ctx.engine) as session:
        job = await session.get(Job, job_id)
    if job is None or not job.callback_url:
        log.info("job %s: no callback_url — nothing to notify", job_id)
        return

    callback = job.callback_url
    allowlist = ctx.settings.webhook_allowlist
    if not allowlist:
        log.info("job %s: webhook log-only mode (no allowlist) → %s", job_id, job.final_key)
        return
    if not is_allowed(callback, allowlist):
        log.warning("job %s: callback blocked by SSRF guard: %s", job_id, callback)
        return

    payload = {"job_id": job_id, "status": "COMPLETED", "final_key": job.final_key}
    try:
        async with httpx.AsyncClient(
            timeout=ctx.settings.WEBHOOK_TIMEOUT_S, follow_redirects=False
        ) as client:
            await client.post(callback, json=payload)
        log.info("job %s: webhook delivered to %s", job_id, callback)
    except Exception:
        log.warning("job %s: webhook failed (job stays COMPLETED)", job_id, exc_info=True)


async def handle_stitch(ctx: WorkerContext, event: StitchReady) -> None:
    """Concat the job's chunks → out/<job>.mp3 → COMPLETED. Idempotent under redelivery."""
    job_id = event.job_id

    async with get_session(ctx.engine) as session:
        job = await session.get(Job, job_id)
        if job is None:
            raise RuntimeError(f"stitch: job {job_id!r} has no row")
        if job.status == JobStatus.COMPLETED:
            log.info("stitch: job %s already COMPLETED — skipping (H5)", job_id)
            return
        chunk_keys = [
            t.audio_key
            for t in (
                await session.execute(
                    select(Task)
                    .where(Task.job_id == job_id, Task.status == "DONE")
                    .order_by(Task.block_index)
                )
            ).scalars()
            if t.audio_key is not None
        ]

    # CAS toward STITCHING (idempotent; rowcount-0 = a concurrent worker got there).
    async with get_session(ctx.engine) as session:
        await advance_status(session, job_id, JobStatus.STITCHING)

    # Client-side concat of the chunks in block order (small chunks → not compose_object).
    parts = [await storage.get_bytes(ctx.minio, key) for key in chunk_keys]
    final_key = f"out/{job_id}.mp3"
    await storage.put_bytes(ctx.minio, final_key, b"".join(parts), content_type="audio/mpeg")

    # CAS STITCHING→COMPLETED + stamp final_key. Only the winner finalises.
    async with get_session(ctx.engine) as session:
        finalized = await finalize_job(session, job_id, final_key)
    if finalized:
        log.info("job %s COMPLETED → %s (%d chunks)", job_id, final_key, len(chunk_keys))
        await _notify(ctx, job_id)  # best-effort; never fails the job


def make_stitch_handler(ctx: WorkerContext) -> Handler:
    """Build the stitch consumer: validate the event (pointers only), then handle it."""

    async def do_work(message: AbstractIncomingMessage) -> None:
        event = StitchReady.model_validate_json(message.body)
        bind_job_id(event.job_id)
        await handle_stitch(ctx, event)

    return ack_last(ctx, Q_STITCH, do_work)
