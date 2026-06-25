"""W2 — ack-last handler skeleton (L2, pure; no Docker).

The single most important ordering in the system (SPEC §4): do work → COMMIT →
PUBLISH → ACK. Ack dead last. These tests drive the three branches of the
``ack_last`` wrapper with a fake message and assert the ack is the LAST awaited
call on every path:
  - success            → ack (no routing)
  - TransientError     → retry ladder, THEN ack
  - PoisonError        → immediate DLQ (max_retries=0), THEN ack
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aio_pika.abc import AbstractIncomingMessage

from core.config import Settings
from core.infra.broker import Q_TTS
from worker.errors import PoisonError, TransientError
from worker.handlers._base import ack_last


def _fake_message(events: list[str]) -> MagicMock:
    msg = MagicMock(spec=AbstractIncomingMessage)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=None)
    cm.__aexit__ = AsyncMock(return_value=False)
    msg.process = MagicMock(return_value=cm)
    msg.ack = AsyncMock(side_effect=lambda *a, **k: events.append("ack"))
    msg.body = b'{"event_id":"e","job_id":"j"}'
    msg.headers = {}
    msg.content_type = "application/json"
    return msg


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.settings = Settings()  # MAX_RETRIES=3
    return ctx


async def _run(
    do_work: Callable[[AbstractIncomingMessage], Awaitable[None]],
) -> tuple[list[str], AsyncMock, MagicMock]:
    events: list[str] = []
    msg = _fake_message(events)
    route = AsyncMock(side_effect=lambda *a, **k: events.append("route"))
    with patch("worker.handlers._base.broker.route_retry_or_dlq", new=route):
        handler = ack_last(_ctx(), Q_TTS, do_work)
        await handler(msg)
    return events, route, msg


def _last_route_kwargs(route: AsyncMock) -> dict[str, Any]:
    assert route.await_args is not None
    return dict(route.await_args.kwargs)


async def test_success_acks_and_does_not_route() -> None:
    async def do_work(_msg: AbstractIncomingMessage) -> None:
        return None

    events, route, msg = await _run(do_work)
    assert events == ["ack"]
    assert route.await_count == 0
    assert msg.ack.await_count == 1


async def test_transient_routes_to_ladder_then_acks_last() -> None:
    async def do_work(_msg: AbstractIncomingMessage) -> None:
        raise TransientError("flaky")

    events, route, _msg = await _run(do_work)
    assert events == ["route", "ack"]  # ack is LAST
    kwargs = _last_route_kwargs(route)
    assert kwargs["max_retries"] == Settings().MAX_RETRIES


async def test_poison_dead_letters_immediately_then_acks_last() -> None:
    async def do_work(_msg: AbstractIncomingMessage) -> None:
        raise PoisonError("never parses")

    events, route, _msg = await _run(do_work)
    assert events == ["route", "ack"]  # ack is LAST
    kwargs = _last_route_kwargs(route)
    assert kwargs["max_retries"] == 0  # immediate DLQ, no retries wasted


async def test_unknown_exception_treated_as_transient() -> None:
    async def do_work(_msg: AbstractIncomingMessage) -> None:
        raise ValueError("unexpected bug")

    events, route, _msg = await _run(do_work)
    assert events == ["route", "ack"]
    kwargs = _last_route_kwargs(route)
    assert kwargs["max_retries"] == Settings().MAX_RETRIES  # fail-safe: retried, not DLQ'd now


@pytest.mark.parametrize("exc", [TransientError("x"), PoisonError("x"), ValueError("x")])
async def test_ack_is_always_last(exc: Exception) -> None:
    async def do_work(_msg: AbstractIncomingMessage) -> None:
        raise exc

    events, _route, _msg = await _run(do_work)
    assert events[-1] == "ack"
