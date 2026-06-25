"""Ack-last handler skeleton — the core delivery-ordering rule.

The single most important ordering in the system:

    do work → COMMIT Postgres → PUBLISH next event → ACK message

Ack dead last. Ack-before-publish + crash = a lost event and a stalled job;
publish-then-crash = a duplicate, which the idempotency guards absorb. ``ack_last``
writes that ordering — and the transient/poison routing — ONCE so the three
handlers don't each re-implement it.

Flow per delivery:
  * ``async with message.process(ignore_processed=True)`` so our explicit ack/nack
    isn't double-processed by aio-pika's context manager.
  * Run ``do_work`` (which commits + publishes). On success, ``ack`` last.
  * On a poison error (:func:`worker.errors.is_poison`) → route straight to the DLQ
    (``max_retries=0`` forces an immediate dead-letter), THEN ack.
  * On any other exception (transient / unknown) → route onto the retry ladder
    (``route_retry_or_dlq`` dead-letters only after ``MAX_RETRIES``), THEN ack.
    Routing the in-flight work forward BEFORE acking the original means the message
    is never lost; acking after means we never double-process the hot copy.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from aio_pika.abc import AbstractIncomingMessage

from core.infra import broker
from core.infra.broker import Handler
from core.infra.logging import get_logger
from worker.bootstrap import WorkerContext
from worker.errors import is_poison

log = get_logger("worker.handler")

DoWork = Callable[[AbstractIncomingMessage], Awaitable[None]]


def ack_last(ctx: WorkerContext, live_queue: str, do_work: DoWork) -> Handler:
    """Wrap ``do_work`` so the broker ack is the LAST awaited call on every path."""

    async def handler(message: AbstractIncomingMessage) -> None:
        async with message.process(ignore_processed=True):
            try:
                await do_work(message)
            except Exception as exc:  # noqa: BLE001 — deliberate top-level routing point
                max_retries = 0 if is_poison(exc) else ctx.settings.MAX_RETRIES
                log.warning(
                    "handler failed on %s (%s); routing (max_retries=%d)",
                    live_queue,
                    type(exc).__name__,
                    max_retries,
                )
                await broker.route_retry_or_dlq(
                    ctx.exchange,
                    message,
                    live_queue=live_queue,
                    max_retries=max_retries,
                    retry_delays=ctx.settings.retry_delays,
                )
                await message.ack()  # ack LAST — work already routed forward
                return
            await message.ack()  # ack LAST — work committed + published

    return handler
