"""T-BEHAVIOR — functional correctness (L4): a real manuscript yields a correct,
cross-store-consistent produced asset (SPEC DoD gate #5 — not just status==COMPLETED).

The sim's fake audio is deterministic — ``tts_fake_audio(text) = b"FAKE_AUDIO:" +
content_hash(text)`` — so the exact bytes of ``out/<job>.mp3`` are predictable: the
ordered concatenation of each block's chunk. We assert that exact equality, plus
that the three stores agree: Postgres (final_key + all tasks DONE), and MinIO
(``raw/`` input, one ``tts/<hash>.wav`` per block, the ``out/`` asset).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest
from minio import Minio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from core.domain.hash import content_hash
from core.domain.text import split_blocks
from core.domain.vendor import tts_fake_audio
from core.infra.db import Task, get_session
from core.infra.storage import get_bytes, get_text

pytestmark = pytest.mark.e2e


async def test_real_manuscript_yields_correct_consistent_asset(
    client: httpx.AsyncClient,
    wait_for_status: Callable[..., Awaitable[str]],
    db_engine: AsyncEngine,
    minio_client: Minio,
) -> None:
    manuscript = (
        "The first scene opens on a quiet harbour.\n\n"
        "In the second scene, the storm arrives.\n\n"
        "The third scene closes on calm water."
    )
    blocks = split_blocks(manuscript)
    assert len(blocks) == 3  # guards the fixture text against accidental edits

    resp = await client.post("/jobs", json={"manuscript": manuscript})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    status = await wait_for_status(job_id, target="COMPLETED", timeout=120.0)
    assert status == "COMPLETED", f"job {job_id} ended {status}, expected COMPLETED"

    # --- Postgres truth: every block is a DONE task; final_key recorded. ---
    async with get_session(db_engine) as session:
        result = await session.execute(
            select(Task).where(Task.job_id == job_id).order_by(Task.block_index)
        )
        tasks = list(result.scalars().all())
    assert len(tasks) == len(blocks), f"expected {len(blocks)} tasks, got {len(tasks)}"
    assert all(t.status == "DONE" for t in tasks), "not every block reached DONE"

    final = (await client.get(f"/status/{job_id}")).json()
    final_key = final["final_key"]
    assert final_key == f"out/{job_id}.mp3", f"unexpected final key {final_key!r}"

    # --- MinIO truth: input preserved; one content-addressed chunk per block. ---
    assert await get_text(minio_client, f"raw/{job_id}.txt") == manuscript
    for block in blocks:
        chunk = await get_bytes(minio_client, f"tts/{content_hash(block)}.wav")
        assert chunk == tts_fake_audio(block), "a stored chunk does not match the deterministic sim"

    # --- The produced asset IS the ordered concatenation of the block chunks. ---
    produced = await get_bytes(minio_client, final_key)
    expected = b"".join(tts_fake_audio(block) for block in blocks)
    assert produced == expected, "out/<job>.mp3 is not the correct ordered concatenation"
