"""Worker background maintenance loops (semaphore re-seed).

These loops keep the *ephemeral* Redis coordination state self-healing, honoring
the golden rule that Redis is "safe to lose": the durable truth lives in Postgres,
so anything Redis holds must be rebuildable without operator intervention.

``run_reseeder`` closes the one real resilience gap called out in ARCHITECTURE.md
§5. ``Semaphore.ensure_slots()`` seeds the TTS token pool exactly once on worker
boot (``bootstrap.py``), but compose Redis carries no volume — a ``docker restart
redis`` (or any flush/eviction) wipes ``tts:slots`` *and* its init marker, after
which a running worker's next ``acquire()`` BLPOPs an empty list forever and the
job never reaches COMPLETED. Periodically re-running ``ensure_slots`` re-seeds the
pool after such a wipe. It is safe to call repeatedly because ``ensure_slots`` is
marker-guarded (atomic Lua, init-once not top-up): on a healthy or merely contended
pool the marker is present and the call is a no-op, so only a genuinely wiped pool
(marker gone with the data) is ever re-seeded.

``run_reaper`` schedules ``Semaphore.reap()`` — the owner-checked atomic Lua step
that returns a crashed holder's orphaned token to the pool exactly once. Its
reclaim logic is already L3-proven; without a periodic caller, though, a dead
holder's slot only comes back if someone happens to reap, so a crash would shrink
the effective pool until the next reboot. The ⅓-TTL heartbeat keeps a live-but-slow
holder's lease fresh, so a periodic reaper only ever reclaims genuinely-dead ones.

Both loops mirror the gateway ``run_sweeper`` shape: sleep-first (a freshly-booted
worker already seeded, so don't fire a redundant re-seed on boot), swallow-and-
continue (one failing pass — e.g. Redis mid-bounce — must never kill the loop),
and cancel cleanly when the worker's shutdown event cancels the task.
"""

from __future__ import annotations

import asyncio

from core.infra.logging import get_logger
from core.infra.redis import Semaphore

log = get_logger("worker.maintenance")


async def run_reseeder(*, semaphore: Semaphore, interval_s: int) -> None:
    """Re-seed the TTS semaphore pool every ``interval_s`` until cancelled.

    Sleeps before the first pass (boot already seeded). A failing pass is logged
    and swallowed so the reconciler outlives any single Redis hiccup.
    """
    while True:
        await asyncio.sleep(interval_s)
        try:
            await semaphore.ensure_slots()
        except Exception:  # noqa: BLE001 — the loop must outlive any single pass
            log.exception("semaphore re-seed pass failed")


async def run_reaper(*, semaphore: Semaphore, interval_s: int) -> None:
    """Reclaim orphaned TTS tokens every ``interval_s`` until cancelled.

    Sleeps before the first pass (no orphans exist on a fresh boot). A failing pass
    is logged and swallowed so the reaper outlives any single Redis hiccup. ``reap``
    is owner-checked, so it never disturbs a live holder.
    """
    while True:
        await asyncio.sleep(interval_s)
        try:
            await semaphore.reap()
        except Exception:  # noqa: BLE001 — the loop must outlive any single pass
            log.exception("semaphore reap pass failed")
