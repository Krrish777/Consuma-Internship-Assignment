"""H1 / H-RESEED — worker semaphore re-seed loop (L2, pure; no Docker).

Proves the load-bearing behavior of ``run_reseeder``, the periodic loop that
closes the Redis-bounce gap (ARCHITECTURE.md §5): after a Redis wipe strips
``tts:slots`` + its init marker, the next pass re-runs the marker-guarded
``Semaphore.ensure_slots()`` so a running worker stops BLPOP-ing an empty pool.

The live ``docker restart redis`` recovery is covered by the L4 e2e probe; here
we pin the loop's contract: sleep-first, re-seed each pass, survive a failing
pass, and cancel cleanly (the worker cancels it on the shutdown event).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import AsyncMock

from worker.maintenance import run_reseeder


async def _drive(sem: Any, *, passes: int, interval_s: int = 5) -> list[tuple[str, Any]]:
    """Run ``run_reseeder`` for exactly ``passes`` iterations, recording order.

    The module's ``asyncio.sleep`` is monkeypatched to break the otherwise-infinite
    loop by raising ``CancelledError`` after ``passes`` ticks — deterministic, with
    no wall-clock waiting. Each ``ensure_slots`` call is recorded too, so the test
    can assert the sleep-before-reseed ordering within a pass.
    """
    order: list[tuple[str, Any]] = []
    ticks = {"n": 0}

    async def fake_sleep(delay: float) -> None:
        order.append(("sleep", delay))
        ticks["n"] += 1
        if ticks["n"] > passes:
            raise asyncio.CancelledError

    real_ensure = sem.ensure_slots

    async def recording_ensure() -> None:
        order.append(("reseed", None))
        await real_ensure()

    sem.ensure_slots = recording_ensure

    with contextlib.suppress(asyncio.CancelledError):
        with _patched_sleep(fake_sleep):
            await run_reseeder(semaphore=sem, interval_s=interval_s)
    return order


class _patched_sleep:
    """Context manager swapping ``worker.maintenance.asyncio.sleep`` for a fake."""

    def __init__(self, fake: Any) -> None:
        self._fake = fake

    def __enter__(self) -> None:
        import worker.maintenance as mod

        self._orig = mod.asyncio.sleep
        mod.asyncio.sleep = self._fake

    def __exit__(self, *exc: object) -> None:
        import worker.maintenance as mod

        mod.asyncio.sleep = self._orig


async def test_run_reseeder_sleeps_before_first_reseed() -> None:
    sem = AsyncMock()
    order = await _drive(sem, passes=1, interval_s=7)
    # Sleep-first: a freshly-booted worker already seeded in bootstrap, so the loop
    # must wait one interval before its first (redundant) re-seed.
    assert order[0] == ("sleep", 7), f"expected to sleep {7}s before re-seeding; got {order}"
    assert order[1] == ("reseed", None)


async def test_run_reseeder_reseeds_each_pass() -> None:
    sem = AsyncMock()
    order = await _drive(sem, passes=3)
    reseeds = [step for step in order if step[0] == "reseed"]
    assert len(reseeds) == 3, f"expected one re-seed per pass; got {order}"


async def test_run_reseeder_swallows_a_failing_pass() -> None:
    # A transient ensure_slots failure (e.g. Redis mid-bounce) must not kill the loop;
    # the next pass must still attempt a re-seed.
    sem = AsyncMock()
    sem.ensure_slots.side_effect = [ConnectionError("redis down"), None, None]
    order = await _drive(sem, passes=3)
    reseeds = [step for step in order if step[0] == "reseed"]
    assert len(reseeds) == 3, "loop died on a failing pass instead of continuing"


async def test_run_reseeder_is_cancellable() -> None:
    # The worker cancels this task on shutdown; cancellation must propagate cleanly.
    sem = AsyncMock()
    task = asyncio.create_task(run_reseeder(semaphore=sem, interval_s=0))
    await asyncio.sleep(0.02)  # let it run a few real iterations
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.cancelled() or task.done()
    assert sem.ensure_slots.await_count >= 1, "loop never re-seeded before cancellation"
