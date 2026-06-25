"""Handler dispatch / DI table.

Maps each pipeline queue to its context-injected handler so the consume loop
stays generic: it iterates this table, registering one consumer per queue.
A single table also makes the choreography legible — queue → handler → next
event — in one place. Adapters are injected via ``ctx`` (never hardcoded inside
handlers), which keeps the boundary clean and the handlers testable.
"""

from __future__ import annotations

from core.infra.broker import Handler, Q_PARSE, Q_STITCH, Q_TTS
from worker.bootstrap import WorkerContext
from worker.handlers.parse import make_parse_handler
from worker.handlers.stitch import make_stitch_handler
from worker.handlers.tts import make_tts_handler


def build_handlers(ctx: WorkerContext) -> dict[str, Handler]:
    """Return the queue → handler table, each handler bound to ``ctx`` by closure."""
    return {
        Q_PARSE: make_parse_handler(ctx),
        Q_TTS: make_tts_handler(ctx),
        Q_STITCH: make_stitch_handler(ctx),
    }
