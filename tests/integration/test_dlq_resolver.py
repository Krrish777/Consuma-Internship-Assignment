"""DLQ → fan-in resolver (L3, real containers).

A poisoned TTS block that exhausts its retries lands on q.dlq. If nothing
decrements pending_count, the job stalls forever in GENERATING. The resolver
must unblock the barrier:
  - tts poison  → mark that task FAILED + decrement; reaching 0 emits StitchReady
                  (partial drama; stitch skips FAILED blocks).
  - parse/stitch poison → CAS the whole job to FAILED.
  - duplicate DLQ delivery → no-op (the conditional claim prevents double-decrement).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import pytest
from aio_pika.abc import AbstractIncomingMessage, AbstractQueue
from pydantic import BaseModel

from core.config import Settings
from core.domain.events import JobCreated, StitchReady, TtsRequested
from core.domain.state import JobStatus
from core.infra.broker import Q_STITCH
from core.infra.db import Job, Task, get_session
from core.infra.queries import complete_task_and_decrement
from core.infra.storage import put_text
from worker.bootstrap import WorkerContext, build_context, close_context
from worker.handlers.dlq import handle_dlq
from worker.handlers.parse import handle_parse

pytestmark = pytest.mark.integration


@pytest.fixture
async def dlq_ctx(worker_stack: Settings) -> AsyncIterator[WorkerContext]:
    settings = worker_stack.model_copy(update={"PARSE_FAILURE_RATE": 0.0})
    ctx = await build_context(settings)
    try:
        yield ctx
    finally:
        await close_context(ctx)


def _msg(event: BaseModel) -> AbstractIncomingMessage:
    m = MagicMock(spec=AbstractIncomingMessage)
    m.body = event.model_dump_json().encode()
    return m


async def _drain(queue: AbstractQueue, limit: int) -> list[AbstractIncomingMessage]:
    out: list[AbstractIncomingMessage] = []
    for _ in range(limit):
        msg = await queue.get(fail=False, no_ack=True, timeout=5)
        if msg is None:
            break
        out.append(msg)
    return out


async def _setup(ctx: WorkerContext, manuscript: str) -> tuple[str, list[str]]:
    job_id = uuid.uuid4().hex
    await put_text(ctx.minio, f"raw/{job_id}.txt", manuscript)
    async with get_session(ctx.engine) as session:
        session.add(
            Job(job_id=job_id, status=JobStatus.PENDING, manuscript_key=f"raw/{job_id}.txt")
        )
        await session.commit()
    await handle_parse(ctx, JobCreated(job_id=job_id))
    async with get_session(ctx.engine) as session:
        from sqlalchemy import select

        task_ids = [
            t.task_id
            for t in (
                await session.execute(
                    select(Task).where(Task.job_id == job_id).order_by(Task.block_index)
                )
            ).scalars()
        ]
    return job_id, task_ids


async def test_dlq_resolver_tts_poison_resolves_barrier(dlq_ctx: WorkerContext) -> None:
    job_id, task_ids = await _setup(dlq_ctx, "Good block.\n\nPoison block.")
    good, poison = task_ids
    q_stitch = await dlq_ctx.channel.get_queue(Q_STITCH)
    await q_stitch.purge()

    # The good block completes normally (barrier 2 → 1).
    async with get_session(dlq_ctx.engine) as session:
        await complete_task_and_decrement(session, job_id, good, "tts/good.wav")

    # The poison block lands on the DLQ → resolver fails it + decrements (1 → 0).
    await handle_dlq(dlq_ctx, _msg(TtsRequested(job_id=job_id, task_id=poison)))

    async with get_session(dlq_ctx.engine) as session:
        job = await session.get(Job, job_id)
        assert job is not None and job.pending_count == 0
        failed = await session.get(Task, poison)
        assert failed is not None and failed.status == "FAILED"

    msgs = await _drain(q_stitch, 3)
    assert len(msgs) == 1  # barrier reached 0 → StitchReady (partial drama)
    assert StitchReady.model_validate_json(msgs[0].body).job_id == job_id


async def test_dlq_resolver_parse_poison_fails_job(dlq_ctx: WorkerContext) -> None:
    job_id = uuid.uuid4().hex
    async with get_session(dlq_ctx.engine) as session:
        session.add(
            Job(job_id=job_id, status=JobStatus.PENDING, manuscript_key=f"raw/{job_id}.txt")
        )
        await session.commit()

    # A JobCreated body (no task_id) on the DLQ → the whole job fails.
    await handle_dlq(dlq_ctx, _msg(JobCreated(job_id=job_id)))

    async with get_session(dlq_ctx.engine) as session:
        job = await session.get(Job, job_id)
        assert job is not None and job.status == JobStatus.FAILED


async def test_dlq_resolver_duplicate_is_noop(dlq_ctx: WorkerContext) -> None:
    job_id, task_ids = await _setup(dlq_ctx, "Alpha.\n\nBeta.")
    good, poison = task_ids
    q_stitch = await dlq_ctx.channel.get_queue(Q_STITCH)
    await q_stitch.purge()

    async with get_session(dlq_ctx.engine) as session:
        await complete_task_and_decrement(session, job_id, good, "tts/good.wav")

    await handle_dlq(dlq_ctx, _msg(TtsRequested(job_id=job_id, task_id=poison)))
    # Duplicate DLQ delivery of the same poison task — must NOT decrement again.
    await handle_dlq(dlq_ctx, _msg(TtsRequested(job_id=job_id, task_id=poison)))

    async with get_session(dlq_ctx.engine) as session:
        job = await session.get(Job, job_id)
        assert job is not None and job.pending_count == 0  # not -1

    msgs = await _drain(q_stitch, 3)
    assert len(msgs) == 1  # only the first resolution emitted StitchReady
