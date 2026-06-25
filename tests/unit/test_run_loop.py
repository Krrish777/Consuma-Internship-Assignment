"""X1 — worker run loop (L2, pure; no Docker).

Proves the two load-bearing, OS-independent pieces of the run loop:
  - a shutdown signal sets the drain event (run() then closes cleanly), and
  - register_consumers wires a consumer for each of the three pipeline queues.
The live signal/redelivery path is covered by the Phase-6 R3.1 e2e (docker kill).
"""

from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from core.config import Settings
from core.infra.broker import Handler, Q_PARSE, Q_STITCH, Q_TTS
from worker.main import _request_shutdown, register_consumers


async def test_request_shutdown_sets_event() -> None:
    event = asyncio.Event()
    assert not event.is_set()
    _request_shutdown(event)
    assert event.is_set()


async def test_register_consumers_registers_all_three_queues() -> None:
    ctx = MagicMock()
    ctx.settings = Settings()
    channel = MagicMock()
    channel.get_queue = AsyncMock(return_value=MagicMock())
    ctx.connection.channel = AsyncMock(return_value=channel)

    handlers = cast(
        "dict[str, Handler]",
        {Q_PARSE: AsyncMock(), Q_TTS: AsyncMock(), Q_STITCH: AsyncMock()},
    )

    with patch("worker.main.broker.consume", new=AsyncMock()) as mock_consume:
        await register_consumers(ctx, handlers)

    assert mock_consume.await_count == 3
    registered = {call.args[0] for call in channel.get_queue.await_args_list}
    assert registered == {Q_PARSE, Q_TTS, Q_STITCH}
