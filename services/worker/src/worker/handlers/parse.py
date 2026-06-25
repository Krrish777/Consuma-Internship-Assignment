"""STAGE A — Parse handler (fan-out emitter) (spec §8, §7.1).

Factory wired into the dispatch table by X2; the consume-loop body is implemented
in W3 (split → N task rows + counter in one tx → fan-out TtsRequested → GENERATING;
0-block → STITCHING). Parse is a re-publishable emitter and is NEVER inbox-skipped.
"""

from __future__ import annotations

from aio_pika.abc import AbstractIncomingMessage

from core.infra.broker import Handler
from worker.bootstrap import WorkerContext


def make_parse_handler(ctx: WorkerContext) -> Handler:
    """Build the parse consumer bound to ``ctx`` (body lands in W3)."""

    async def handler(message: AbstractIncomingMessage) -> None:
        raise NotImplementedError("parse handler body lands in W3")

    return handler
