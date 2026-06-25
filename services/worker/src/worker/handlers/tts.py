"""STAGE C — TTS handler (cache → slot → generate → fan-in) (spec §8, §9).

Factory wired into the dispatch table by X2; the consume-loop body is implemented
in W4 (content-cache check BEFORE a leased semaphore slot → synth → store → atomic
fan-in decrement → emit StitchReady when the barrier reaches 0, H-EMIT on redelivery).
"""

from __future__ import annotations

from aio_pika.abc import AbstractIncomingMessage

from core.infra.broker import Handler
from worker.bootstrap import WorkerContext


def make_tts_handler(ctx: WorkerContext) -> Handler:
    """Build the TTS consumer bound to ``ctx`` (body lands in W4)."""

    async def handler(message: AbstractIncomingMessage) -> None:
        raise NotImplementedError("tts handler body lands in W4")

    return handler
