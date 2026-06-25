"""The reconciler loop now also purges the inbox (pure).

``run_sweeper`` historically re-published orphaned PENDING jobs only. It now
folds processed_events retention into the same loop. These tests pin the
loop contract: each pass runs BOTH ``sweep_once`` and ``purge_once``, and the two
are independent — a failure in one must not skip the other. The real DELETE
semantics are tested with testcontainers (test_sweeper.py); here both are mocked.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.sweeper import run_sweeper


async def _drive(*, passes: int, sweep: AsyncMock, purge: AsyncMock) -> None:
    """Drive ``run_sweeper`` for exactly ``passes`` iterations (no wall-clock wait).

    The module's ``asyncio.sleep`` is monkeypatched to raise ``CancelledError`` after
    ``passes`` ticks; ``sweep_once``/``purge_once`` are patched with the given mocks.
    """
    ticks = {"n": 0}

    async def fake_sleep(_delay: float) -> None:
        ticks["n"] += 1
        if ticks["n"] > passes:
            raise asyncio.CancelledError

    with (
        patch("gateway.sweeper.asyncio.sleep", new=fake_sleep),
        patch("gateway.sweeper.sweep_once", new=sweep),
        patch("gateway.sweeper.purge_once", new=purge),
    ):
        with contextlib.suppress(asyncio.CancelledError):
            await run_sweeper(
                engine=MagicMock(),
                exchange=MagicMock(),
                interval_s=5,
                pending_timeout_s=120,
                retention_s=604_800,
            )


async def test_run_sweeper_purges_each_pass() -> None:
    sweep, purge = AsyncMock(), AsyncMock(return_value=0)
    await _drive(passes=3, sweep=sweep, purge=purge)
    assert sweep.await_count == 3, "sweep_once not called once per pass"
    assert purge.await_count == 3, "purge_once not called once per pass"


async def test_run_sweeper_purges_with_the_retention_window() -> None:
    sweep, purge = AsyncMock(), AsyncMock(return_value=0)
    await _drive(passes=1, sweep=sweep, purge=purge)
    assert purge.await_args is not None, "purge_once was never awaited"
    assert purge.await_args.kwargs["retention_s"] == 604_800, (
        "purge_once not called with the configured retention window"
    )


async def test_purge_failure_does_not_skip_sweep_and_loop_survives() -> None:
    # A failing purge pass must neither kill the loop nor skip the next sweep.
    sweep = AsyncMock()
    purge = AsyncMock(side_effect=[RuntimeError("db hiccup"), 0, 0])
    await _drive(passes=3, sweep=sweep, purge=purge)
    assert sweep.await_count == 3, "a purge failure skipped sweeps / killed the loop"
    assert purge.await_count == 3


async def test_sweep_failure_does_not_skip_purge() -> None:
    # Conversely, a failing sweep pass must not skip the purge in the same pass.
    sweep = AsyncMock(side_effect=[RuntimeError("broker hiccup"), None, None])
    purge = AsyncMock(return_value=0)
    await _drive(passes=3, sweep=sweep, purge=purge)
    assert purge.await_count == 3, "a sweep failure skipped the purge in that pass"
