"""Worker entrypoint — aio-pika consume loop (spec §5, §8).

X1 replaces the Rung-0 idle skeleton with a real run loop:

    build_context (X3) → register one consumer per queue (X2 dispatch) → await
    shutdown → close_context.

Each queue gets its OWN channel so prefetch can be sized per-queue (W1/H-PREFETCH:
``set_qos`` is channel-wide, so q.tts needs a separate channel to run a smaller
prefetch than q.parse). Manual ack (``no_ack=False``) is retained — the handler
acks LAST, after its Postgres commit + downstream publish.

Clean shutdown matters for crash-recovery (R3.1): on SIGTERM (compose ``docker
stop``) the worker sets a shutdown event, stops consuming, and closes the broker
connection, so any in-flight unacked message is released for redelivery rather
than lost.

Run by compose as: python -m worker.main
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from core.config import Settings
from core.infra import broker
from core.infra.broker import Handler, Q_DLQ, Q_TTS
from core.infra.logging import get_logger
from worker.bootstrap import WorkerContext, build_context, close_context
from worker.dispatch import build_handlers
from worker.handlers.dlq import make_dlq_handler
from worker.maintenance import run_reaper, run_reseeder

log = get_logger("worker")


def prefetch_for(queue_name: str, settings: Settings) -> int:
    """Per-queue prefetch (W1 / H-PREFETCH).

    q.tts is sized near the TTS semaphore size (slots + a tiny headroom): a worker
    parking many more unacked TTS messages than it can ever service just blocks them
    on ``BLPOP`` and enlarges the crash-redelivery blast radius.

    parse/stitch keep the larger global ``PREFETCH`` deliberately: their handlers never
    block on a scarce leased resource — parse does DB writes + publish, stitch does a
    MinIO concat + DB commit, each running to completion as soon as it is scheduled. A
    deeper prefetch there only keeps the pipeline fed; it does NOT park messages unacked
    behind a blocking primitive, so it adds no crash blast radius or head-of-line stall.
    Only q.tts gates on the global 3-slot semaphore, so only q.tts needs the small bound.
    """
    if queue_name == Q_TTS:
        return settings.TTS_CONCURRENCY + 1
    return settings.PREFETCH


def _request_shutdown(shutdown: asyncio.Event) -> None:
    """Signal-handler callback: ask the run loop to drain and exit."""
    log.info("shutdown signal received; draining")
    shutdown.set()


def install_signal_handlers(loop: asyncio.AbstractEventLoop, shutdown: asyncio.Event) -> None:
    """Wire SIGTERM/SIGINT to set the shutdown event (best-effort across platforms).

    ``loop.add_signal_handler`` is the asyncio-clean path (POSIX, the worker's Linux
    container). It is unsupported on Windows / outside the main thread, where we fall
    back to the classic ``signal.signal`` handler.
    """
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_shutdown, shutdown)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_: _request_shutdown(shutdown))


async def register_consumers(ctx: WorkerContext, handlers: dict[str, Handler]) -> None:
    """Register each handler on its queue, one dedicated channel per queue.

    A per-queue channel lets prefetch be sized per queue (W1): ``prefetch_for``
    sizes q.tts down toward the semaphore size (H-PREFETCH).
    """
    for queue_name, handler in handlers.items():
        channel = await ctx.connection.channel()
        queue = await channel.get_queue(queue_name)
        await broker.consume(
            channel, queue, handler, prefetch=prefetch_for(queue_name, ctx.settings)
        )


async def run() -> None:
    """Bootstrap, register consumers, and run until a shutdown signal arrives."""
    ctx = await build_context()
    shutdown = asyncio.Event()
    install_signal_handlers(asyncio.get_running_loop(), shutdown)

    handlers = build_handlers(ctx)
    # W7: the DLQ resolver runs OFF the hot queue so healthy traffic is unaffected.
    handlers[Q_DLQ] = make_dlq_handler(ctx)
    await register_consumers(ctx, handlers)

    # Background semaphore maintenance (cancelled cleanly on shutdown, below):
    #   H1 run_reseeder — re-seed tts:slots if Redis is ever wiped (else acquire()
    #     BLPOPs an empty pool forever); no-op on a healthy pool (marker-guarded).
    #   H2 run_reaper — return a crashed holder's orphaned token to the pool (else
    #     a worker crash mid-TTS shrinks the effective pool until the next reboot).
    maintenance_tasks = [
        asyncio.create_task(
            run_reseeder(semaphore=ctx.semaphore, interval_s=ctx.settings.RESEED_INTERVAL_S)
        ),
        asyncio.create_task(
            run_reaper(semaphore=ctx.semaphore, interval_s=ctx.settings.REAP_INTERVAL_S)
        ),
    ]
    log.info("worker running; consuming q.parse / q.tts / q.stitch / q.dlq")

    try:
        await shutdown.wait()
    finally:
        for task in maintenance_tasks:
            task.cancel()
        for task in maintenance_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await close_context(ctx)
        log.info("worker stopped cleanly")


if __name__ == "__main__":
    asyncio.run(run())
