"""X2 — handler dispatch / DI table (L2, pure; no Docker).

The consume loop is generic: it looks each queue up in this table. The test
proves the table binds exactly the three pipeline queues to callables, built
from an injected context (no hardcoded adapters).
"""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

from core.infra.broker import Q_PARSE, Q_STITCH, Q_TTS
from worker.bootstrap import WorkerContext
from worker.dispatch import build_handlers


def test_build_handlers_maps_three_queues_to_callables() -> None:
    ctx = cast("WorkerContext", MagicMock())
    handlers = build_handlers(ctx)

    assert set(handlers) == {Q_PARSE, Q_TTS, Q_STITCH}
    for handler in handlers.values():
        assert callable(handler)
