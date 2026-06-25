"""TTS handler / cache → slot → generate → fan-in (L3, real containers).

Proves:
  - tts_fan_in: N tasks decrement the barrier; exactly ONE StitchReady is emitted;
    all tasks land DONE with an audio_key and the audio object exists in MinIO.
  - tts_cache: two identical blocks → exactly ONE vendor synth (the second is a
    cache hit and must NOT re-synthesize / burn a slot).
  - tts_emit: redelivering an already-DONE task after the barrier was
    crossed re-emits StitchReady (covers crash-after-decrement-before-publish).

Setup runs the real parse handler first (PARSE_FAILURE_RATE overridden to 0.0 for
determinism), so the job/tasks/manuscript exist exactly as production would build them.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from aio_pika.abc import AbstractIncomingMessage, AbstractQueue
from sqlalchemy import select

from core.config import Settings
from core.domain.events import JobCreated, StitchReady, TtsRequested
from core.domain.state import JobStatus
from core.domain.vendor import tts_fake_audio
from core.infra.broker import Q_STITCH
from core.infra.db import Job, Task, get_session
from core.infra.storage import get_bytes, put_text
from worker.bootstrap import WorkerContext, build_context, close_context
from worker.handlers.parse import handle_parse
from worker.handlers.tts import handle_tts

pytestmark = pytest.mark.integration


@pytest.fixture
async def tts_ctx(worker_stack: Settings) -> AsyncIterator[WorkerContext]:
    settings = worker_stack.model_copy(update={"PARSE_FAILURE_RATE": 0.0})
    ctx = await build_context(settings)
    try:
        yield ctx
    finally:
        await close_context(ctx)


async def _drain(queue: AbstractQueue, limit: int) -> list[AbstractIncomingMessage]:
    out: list[AbstractIncomingMessage] = []
    for _ in range(limit):
        msg = await queue.get(fail=False, no_ack=True, timeout=5)
        if msg is None:
            break
        out.append(msg)
    return out


async def _setup(ctx: WorkerContext, manuscript: str) -> tuple[str, list[str]]:
    """Seed + parse a job; return (job_id, ordered task_ids)."""
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
    return job_id, task_ids


async def test_tts_fan_in_emits_one_stitch_ready(tts_ctx: WorkerContext) -> None:
    job_id, task_ids = await _setup(tts_ctx, "Block A.\n\nBlock B.\n\nBlock C.")
    assert len(task_ids) == 3
    q_stitch = await tts_ctx.channel.get_queue(Q_STITCH)
    await q_stitch.purge()

    for tid in task_ids:
        await handle_tts(tts_ctx, TtsRequested(job_id=job_id, task_id=tid))

    msgs = await _drain(q_stitch, 4)
    assert len(msgs) == 1  # exactly one worker observed pending_count == 0
    assert StitchReady.model_validate_json(msgs[0].body).job_id == job_id

    async with get_session(tts_ctx.engine) as session:
        job = await session.get(Job, job_id)
        assert job is not None and job.pending_count == 0
        tasks = (await session.execute(select(Task).where(Task.job_id == job_id))).scalars().all()
        audio_keys = [t.audio_key for t in tasks]
        assert all(t.status == "DONE" for t in tasks)
        assert all(k is not None for k in audio_keys)

    for key in audio_keys:
        assert key is not None
        assert await get_bytes(tts_ctx.minio, key)  # object actually written


async def test_tts_cache_hit_no_second_synth(tts_ctx: WorkerContext) -> None:
    # Two identical blocks → same content hash → the second must be a cache hit.
    job_id, task_ids = await _setup(tts_ctx, "Same line.\n\nSame line.")
    assert len(task_ids) == 2
    q_stitch = await tts_ctx.channel.get_queue(Q_STITCH)
    await q_stitch.purge()

    calls: list[str] = []

    def counting(text: str) -> bytes:
        calls.append(text)
        return tts_fake_audio(text)

    with patch("worker.handlers.tts.tts_fake_audio", side_effect=counting):
        for tid in task_ids:
            await handle_tts(tts_ctx, TtsRequested(job_id=job_id, task_id=tid))

    assert len(calls) == 1  # identical block synthesised exactly once (2nd is cached)
    msgs = await _drain(q_stitch, 3)
    assert len(msgs) == 1


async def test_tts_emit_reemits_on_redelivery(tts_ctx: WorkerContext) -> None:
    job_id, task_ids = await _setup(tts_ctx, "One.\n\nTwo.")
    q_stitch = await tts_ctx.channel.get_queue(Q_STITCH)
    await q_stitch.purge()

    for tid in task_ids:
        await handle_tts(tts_ctx, TtsRequested(job_id=job_id, task_id=tid))
    first = await _drain(q_stitch, 3)
    assert len(first) == 1

    # Redeliver an already-DONE task: the claim no-ops (None), but the barrier is
    # already 0 → the handler re-emits StitchReady (stitch is idempotent).
    await handle_tts(tts_ctx, TtsRequested(job_id=job_id, task_id=task_ids[0]))
    second = await _drain(q_stitch, 3)
    assert len(second) == 1
