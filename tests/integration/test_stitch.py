"""Stitch handler / idempotent finalize (L3, real containers).

Proves:
  - happy path: StitchReady → job COMPLETED, final_key=out/<job>.mp3, and the
    object is the client-side concat of the job's chunks in block order.
  - idempotent: a redelivered StitchReady leaves one asset and one COMPLETED, and
    does not attempt the illegal COMPLETED→COMPLETED transition.

The webhook is exercised in test_webhook.py.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select

from core.config import Settings
from core.domain.events import JobCreated, StitchReady, TtsRequested
from core.domain.state import JobStatus
from core.infra.db import Job, Task, get_session
from core.infra.storage import get_bytes, put_text
from worker.bootstrap import WorkerContext, build_context, close_context
from worker.handlers.parse import handle_parse
from worker.handlers.stitch import handle_stitch
from worker.handlers.tts import handle_tts

pytestmark = pytest.mark.integration


@pytest.fixture
async def stitch_ctx(worker_stack: Settings) -> AsyncIterator[WorkerContext]:
    settings = worker_stack.model_copy(update={"PARSE_FAILURE_RATE": 0.0})
    ctx = await build_context(settings)
    try:
        yield ctx
    finally:
        await close_context(ctx)


async def _completed_tts(ctx: WorkerContext, manuscript: str) -> tuple[str, list[str]]:
    """Parse + run TTS for every block; return (job_id, ordered audio_keys)."""
    job_id = uuid.uuid4().hex
    await put_text(ctx.minio, f"raw/{job_id}.txt", manuscript)
    async with get_session(ctx.engine) as session:
        session.add(
            Job(job_id=job_id, status=JobStatus.PENDING, manuscript_key=f"raw/{job_id}.txt")
        )
        await session.commit()
    await handle_parse(ctx, JobCreated(job_id=job_id))
    async with get_session(ctx.engine) as session:
        task_ids = [
            t.task_id
            for t in (
                await session.execute(
                    select(Task).where(Task.job_id == job_id).order_by(Task.block_index)
                )
            ).scalars()
        ]
    for tid in task_ids:
        await handle_tts(ctx, TtsRequested(job_id=job_id, task_id=tid))
    async with get_session(ctx.engine) as session:
        audio_keys = [
            t.audio_key
            for t in (
                await session.execute(
                    select(Task).where(Task.job_id == job_id).order_by(Task.block_index)
                )
            ).scalars()
        ]
    return job_id, [k for k in audio_keys if k is not None]


async def test_stitch_happy_path(stitch_ctx: WorkerContext) -> None:
    job_id, audio_keys = await _completed_tts(
        stitch_ctx, "Scene one.\n\nScene two.\n\nScene three."
    )
    expected = b"".join([await get_bytes(stitch_ctx.minio, k) for k in audio_keys])

    await handle_stitch(stitch_ctx, StitchReady(job_id=job_id))

    async with get_session(stitch_ctx.engine) as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.COMPLETED
        assert job.final_key == f"out/{job_id}.mp3"

    out = await get_bytes(stitch_ctx.minio, f"out/{job_id}.mp3")
    assert out == expected  # client-side concat in block order


async def test_stitch_redelivery_is_idempotent(stitch_ctx: WorkerContext) -> None:
    job_id, _ = await _completed_tts(stitch_ctx, "Alpha.\n\nBeta.")

    await handle_stitch(stitch_ctx, StitchReady(job_id=job_id))
    out1 = await get_bytes(stitch_ctx.minio, f"out/{job_id}.mp3")

    # Redelivery: already COMPLETED → short-circuit (no double asset, no illegal CAS).
    await handle_stitch(stitch_ctx, StitchReady(job_id=job_id))
    out2 = await get_bytes(stitch_ctx.minio, f"out/{job_id}.mp3")

    assert out1 == out2
    async with get_session(stitch_ctx.engine) as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.COMPLETED
