"""W3 — parse handler / fan-out emitter (L3, real containers).

Proves the subtlest correctness card:
  - N-block manuscript → N Task rows + pending_count=N (one tx) + N TtsRequested,
    job advanced to GENERATING.
  - Redelivered JobCreated → still N tasks (ON CONFLICT), counter unchanged, events
    re-published (H2: parse is a re-publishable emitter, never inbox-skipped).
  - 0-block manuscript → no tasks, pending_count=0, one StitchReady, no TtsRequested
    (the fan-in barrier of 0 must terminate, not hang).
  - Block count capped at MAX_BLOCKS (H14).

Determinism: PARSE_FAILURE_RATE is overridden to 0.0 so the 15% transient injection
never makes these assertions flaky (the failure path is unit-covered by R2.0).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from aio_pika.abc import AbstractIncomingMessage, AbstractQueue

from core.config import Settings
from core.domain.events import JobCreated, StitchReady, TtsRequested
from core.domain.state import JobStatus
from core.infra.broker import Q_STITCH, Q_TTS
from core.infra.db import Job, Task, get_session
from core.infra.storage import put_text
from sqlalchemy import select
from worker.bootstrap import WorkerContext, build_context, close_context
from worker.handlers.parse import handle_parse

pytestmark = pytest.mark.integration


@pytest.fixture
async def parse_ctx(worker_stack: Settings) -> AsyncIterator[WorkerContext]:
    settings = worker_stack.model_copy(update={"PARSE_FAILURE_RATE": 0.0})
    ctx = await build_context(settings)
    try:
        yield ctx
    finally:
        await close_context(ctx)


async def _seed_job(ctx: WorkerContext, manuscript: str) -> str:
    job_id = uuid.uuid4().hex
    await put_text(ctx.minio, f"raw/{job_id}.txt", manuscript)
    async with get_session(ctx.engine) as session:
        session.add(
            Job(job_id=job_id, status=JobStatus.PENDING, manuscript_key=f"raw/{job_id}.txt")
        )
        await session.commit()
    return job_id


async def _drain(queue: AbstractQueue, limit: int) -> list[AbstractIncomingMessage]:
    out: list[AbstractIncomingMessage] = []
    for _ in range(limit):
        msg = await queue.get(fail=False, no_ack=True, timeout=5)
        if msg is None:
            break
        out.append(msg)
    return out


async def test_parse_fanout(parse_ctx: WorkerContext) -> None:
    job_id = await _seed_job(parse_ctx, "Block one.\n\nBlock two.\n\nBlock three.")
    q_tts = await parse_ctx.channel.get_queue(Q_TTS)
    await q_tts.purge()

    await handle_parse(parse_ctx, JobCreated(job_id=job_id))

    async with get_session(parse_ctx.engine) as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.pending_count == 3
        assert job.status == JobStatus.GENERATING
        task_ids = {
            t.task_id
            for t in (
                await session.execute(select(Task).where(Task.job_id == job_id))
            ).scalars()
        }
    assert len(task_ids) == 3

    events = await _drain(q_tts, 4)
    assert len(events) == 3
    emitted = {TtsRequested.model_validate_json(e.body).task_id for e in events}
    assert emitted == task_ids


async def test_parse_redelivery_is_idempotent(parse_ctx: WorkerContext) -> None:
    job_id = await _seed_job(parse_ctx, "Alpha.\n\nBeta.")
    q_tts = await parse_ctx.channel.get_queue(Q_TTS)
    await q_tts.purge()

    await handle_parse(parse_ctx, JobCreated(job_id=job_id))
    first = await _drain(q_tts, 5)
    assert len(first) == 2

    # Redelivery: rows already exist (ON CONFLICT no-op), counter must NOT double,
    # but the events MUST be re-published (a prior crash may have lost them).
    await handle_parse(parse_ctx, JobCreated(job_id=job_id))
    second = await _drain(q_tts, 5)
    assert len(second) == 2

    async with get_session(parse_ctx.engine) as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.pending_count == 2  # NOT reset to 2-from-fresh nor doubled
        rows = (
            await session.execute(select(Task).where(Task.job_id == job_id))
        ).scalars().all()
        assert len(rows) == 2  # still 2, no duplicates


async def test_zero_block_terminates(parse_ctx: WorkerContext) -> None:
    job_id = await _seed_job(parse_ctx, "   \n\n   \t  ")  # whitespace-only → 0 blocks
    q_tts = await parse_ctx.channel.get_queue(Q_TTS)
    q_stitch = await parse_ctx.channel.get_queue(Q_STITCH)
    await q_tts.purge()
    await q_stitch.purge()

    await handle_parse(parse_ctx, JobCreated(job_id=job_id))

    async with get_session(parse_ctx.engine) as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.pending_count == 0
        assert job.status == JobStatus.GENERATING  # advanced, not stuck at PENDING

    assert await _drain(q_tts, 1) == []  # no TTS work for an empty manuscript
    stitch = await _drain(q_stitch, 2)
    assert len(stitch) == 1
    assert StitchReady.model_validate_json(stitch[0].body).job_id == job_id


async def test_parse_caps_blocks_at_max(worker_stack: Settings) -> None:
    settings = worker_stack.model_copy(update={"PARSE_FAILURE_RATE": 0.0, "MAX_BLOCKS": 2})
    ctx = await build_context(settings)
    try:
        job_id = await _seed_job(ctx, "One.\n\nTwo.\n\nThree.\n\nFour.")  # 4 blocks
        q_tts = await ctx.channel.get_queue(Q_TTS)
        await q_tts.purge()

        await handle_parse(ctx, JobCreated(job_id=job_id))

        async with get_session(ctx.engine) as session:
            job = await session.get(Job, job_id)
            assert job is not None
            assert job.pending_count == 2  # capped at MAX_BLOCKS
        events = await _drain(q_tts, 5)
        assert len(events) == 2
    finally:
        await close_context(ctx)
