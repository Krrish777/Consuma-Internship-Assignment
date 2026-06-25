"""STAGE D — Stitch + notify (spec §8, §9).

Factory wired into the dispatch table by X2; the consume-loop body is implemented
in W5 (client-side concat of the job's chunks → out/<job>.mp3 → CAS COMPLETED,
idempotent under redelivery) and the best-effort webhook in W5b.
"""

from __future__ import annotations

from aio_pika.abc import AbstractIncomingMessage

from core.infra.broker import Handler
from worker.bootstrap import WorkerContext


def make_stitch_handler(ctx: WorkerContext) -> Handler:
    """Build the stitch consumer bound to ``ctx`` (body lands in W5)."""

    async def handler(message: AbstractIncomingMessage) -> None:
        raise NotImplementedError("stitch handler body lands in W5")

    return handler
