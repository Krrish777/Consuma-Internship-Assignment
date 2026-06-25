"""DLQ → fan-in resolver.

A poison message that exhausts the retry ladder lands on ``q.dlq``. For a TTS
block that means the fan-in barrier would never be decremented and the job would
**stall forever in GENERATING**. This consumer — deliberately OFF the hot queue,
so healthy ``q.tts`` traffic is never blocked (no head-of-line) — resolves the
barrier.

Policy (recommended):
  * **TTS poison** (body has ``task_id``): mark that task ``FAILED`` and decrement
    the barrier (``fail_task_and_decrement``). If the decrement reaches 0, emit
    ``StitchReady`` — the job still completes, as a *partial* drama; the stitch
    handler skips FAILED blocks. (The alternative — hard-fail the whole job — is a
    one-line swap to ``advance_status(..., FAILED)``.)
  * **Parse / stitch poison** (body has no ``task_id`` — ``JobCreated`` and
    ``StitchReady`` are field-identical): CAS the whole job to ``FAILED``; there is
    no per-block barrier to resolve.

This consumer manages its own ack/nack (NOT ``ack_last``): ``ack_last`` would route
a failure back onto a retry ladder keyed by the live queue, but ``q.dlq`` has none.
On its own error it requeues (``nack(requeue=True)``) rather than silently drop the
message without touching the barrier.
"""

from __future__ import annotations

import json

from aio_pika.abc import AbstractIncomingMessage

from core.domain.events import StitchReady
from core.infra import broker
from core.infra.broker import Handler, Q_STITCH
from core.infra.db import get_session
from core.infra.logging import bind_job_id, get_logger
from core.infra.queries import advance_status, fail_task_and_decrement
from core.domain.state import JobStatus
from worker.bootstrap import WorkerContext

log = get_logger("worker.dlq")


async def handle_dlq(ctx: WorkerContext, message: AbstractIncomingMessage) -> None:
    """Resolve the fan-in barrier (or fail the job) for a dead-lettered message."""
    data = json.loads(message.body)
    job_id = data.get("job_id")
    if job_id is None:
        log.error("dlq: message without job_id, cannot resolve: %r", message.body)
        return
    bind_job_id(job_id)

    if "task_id" in data:
        task_id = data["task_id"]
        async with get_session(ctx.engine) as session:
            remaining = await fail_task_and_decrement(session, job_id, task_id)
        log.warning("dlq: tts task %s FAILED; barrier remaining=%s", task_id, remaining)
        if remaining == 0:
            await broker.publish(ctx.exchange, StitchReady(job_id=job_id), routing_key=Q_STITCH)
            log.warning("dlq: job %s barrier resolved → StitchReady (partial drama)", job_id)
    else:
        async with get_session(ctx.engine) as session:
            await advance_status(session, job_id, JobStatus.FAILED)
        log.warning("dlq: job %s FAILED (parse/stitch poison)", job_id)


def make_dlq_handler(ctx: WorkerContext) -> Handler:
    """Build the q.dlq consumer (manages its own ack/nack; never silently drops)."""

    async def handler(message: AbstractIncomingMessage) -> None:
        try:
            await handle_dlq(ctx, message)
        except Exception:
            log.exception("dlq resolver failed; requeueing for another attempt")
            await message.nack(requeue=True)
            return
        await message.ack()

    return handler
