"""R4.2 — cache + fan-in probe (L4): dedup at the vendor, not at the counter.

Two identical blocks in one job share a content hash, so they resolve to ONE
content-addressed TTS asset (``tts/<block_hash>.wav``) — the cache/hash is keyed
on block *content*, never on task_id (the named junior trap). Yet they remain two
distinct task rows that each decrement the fan-in barrier, so the job still emits
exactly one StitchReady and COMPLETEs with every block represented. Conflating the
cache key with the counter would either drop a decrement (job hangs) or share a
counter slot (early, incomplete completion).

The pure cost property ("the vendor synthesizes the duplicate only once / burns no
slot") is L3-proven with a call counter (test_tts cache); here we assert the
durable, externally-observable consequences: matching content-addressed keys for
the duplicates, distinct keys for distinct blocks, and a correct fan-in.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from core.infra.db import Task, get_session

pytestmark = pytest.mark.e2e


async def _tasks_by_block(engine: AsyncEngine, job_id: str) -> dict[int, Task]:
    async with get_session(engine) as session:
        result = await session.execute(select(Task).where(Task.job_id == job_id))
        return {t.block_index: t for t in result.scalars().all()}


async def test_identical_blocks_dedup_to_one_asset_but_fan_in_counts_each(
    client: httpx.AsyncClient,
    wait_for_status: Callable[..., Awaitable[str]],
    db_engine: AsyncEngine,
) -> None:
    # Blocks 0 and 2 are identical text → identical content hash; 1 and 3 distinct.
    refrain = "The cursed refrain echoes."
    manuscript = "\n\n".join(
        [refrain, "A unique opening verse.", refrain, "A distinct closing line."]
    )
    resp = await client.post("/jobs", json={"manuscript": manuscript})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    status = await wait_for_status(job_id, target="COMPLETED", timeout=120.0)
    assert status == "COMPLETED", f"job {job_id} ended {status}, expected COMPLETED"

    tasks = await _tasks_by_block(db_engine, job_id)
    assert set(tasks) == {0, 1, 2, 3}, f"expected 4 blocks, got {sorted(tasks)}"
    assert all(t.status == "DONE" for t in tasks.values()), "not all tasks DONE"

    # Fan-in counted every task (incl. the duplicate) — the barrier resolved to 0.
    final = (await client.get(f"/status/{job_id}")).json()
    assert final["pending_count"] == 0, "fan-in barrier did not resolve with a duplicate block"
    assert final["final_key"], "no stitched asset produced"

    # Dedup at the vendor: identical blocks share one content-addressed key...
    assert tasks[0].block_hash == tasks[2].block_hash, "identical blocks must share a content hash"
    assert tasks[0].audio_key == tasks[2].audio_key, "identical blocks must share one TTS asset"
    # ...keyed on content, never task_id (the cache-vs-counter trap):
    assert tasks[0].audio_key == f"tts/{tasks[0].block_hash}.wav", (
        "asset key is not content-addressed"
    )
    # ...while distinct blocks get distinct keys.
    assert tasks[0].block_hash != tasks[1].block_hash, "distinct blocks collided on hash"
    assert tasks[1].audio_key != tasks[3].audio_key, "distinct blocks shared an asset"
