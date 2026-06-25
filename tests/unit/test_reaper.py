"""H2 / H-REAP — worker semaphore reap loop (L2, pure; no Docker).

``Semaphore.reap()`` (owner-checked atomic Lua) returns a crashed holder's
orphaned token to the pool exactly once; its reclaim semantics are L3-proven
(X5, tests/integration/test_redis.py). H2 adds nothing to that logic — it only
*schedules* it. So these tests pin the loop's contract: sleep-first, reap each
pass, survive a failing pass, and cancel cleanly (the worker cancels it on the
shutdown event). The live "killed mid-TTS holder's slot returns" path rides on
the unchanged reap() semantics + the existing L3 reclaim test.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import AsyncMock, patch

from worker.maintenance import run_reaper


async def _drive(sem: Any, *, passes: int, interval_s: int = 5) -> list[tuple[str, Any]]:
    """Run ``run_reaper`` for exactly ``passes`` iterations, recording order.

    The module's ``asyncio.sleep`` is monkeypatched to break the otherwise-infinite
    loop by raising ``CancelledError`` after ``passes`` ticks — deterministic, no
    wall-clock waiting. Each ``reap`` call is recorded so the test can assert the
    sleep-before-reap ordering within a pass.
    """
    order: list[tuple[str, Any]] = []
    ticks = {"n": 0}

    async def fake_sleep(delay: float) -> None:
        order.append(("sleep", delay))
        ticks["n"] += 1
        if ticks["n"] > passes:
            raise asyncio.CancelledError

    real_reap = sem.reap

    async def recording_reap() -> int:
        order.append(("reap", None))
        return int(await real_reap())

    sem.reap = recording_reap

    with contextlib.suppress(asyncio.CancelledError):
        with patch("worker.maintenance.asyncio.sleep", new=fake_sleep):
            await run_reaper(semaphore=sem, interval_s=interval_s)
    return order


async def test_run_reaper_sleeps_before_first_reap() -> None:
    sem = AsyncMock()
    sem.reap.return_value = 0
    order = await _drive(sem, passes=1, interval_s=9)
    assert order[0] == ("sleep", 9), f"expected to sleep {9}s before reaping; got {order}"
    assert order[1] == ("reap", None)


async def test_run_reaper_reaps_each_pass() -> None:
    sem = AsyncMock()
    sem.reap.return_value = 0
    order = await _drive(sem, passes=3)
    reaps = [step for step in order if step[0] == "reap"]
    assert len(reaps) == 3, f"expected one reap per pass; got {order}"


async def test_run_reaper_swallows_a_failing_pass() -> None:
    # A transient reap failure (e.g. Redis mid-bounce) must not kill the loop.
    sem = AsyncMock()
    sem.reap.side_effect = [ConnectionError("redis down"), 0, 1]
    order = await _drive(sem, passes=3)
    reaps = [step for step in order if step[0] == "reap"]
    assert len(reaps) == 3, "loop died on a failing pass instead of continuing"


async def test_run_reaper_is_cancellable() -> None:
    sem = AsyncMock()
    sem.reap.return_value = 0
    task = asyncio.create_task(run_reaper(semaphore=sem, interval_s=0))
    await asyncio.sleep(0.02)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.cancelled() or task.done()
    assert sem.reap.await_count >= 1, "loop never reaped before cancellation"
