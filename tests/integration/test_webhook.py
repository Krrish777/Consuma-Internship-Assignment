"""W5b — webhook notify / failure ≠ job failure (L3, real containers).

After COMPLETED, the job optionally POSTs to the client callback_url. Proves:
  - delivered: an allowlisted+allowed URL receives the JSON payload.
  - failure-tolerant (MUST #8): an unreachable callback is swallowed — the job
    stays COMPLETED with its asset.
  - log-only: with no allowlist, no POST is attempted at all.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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


async def _make_ctx(worker_stack: Settings, **overrides: object) -> WorkerContext:
    settings = worker_stack.model_copy(update={"PARSE_FAILURE_RATE": 0.0, **overrides})
    return await build_context(settings)


async def _setup_with_callback(ctx: WorkerContext, manuscript: str, callback: str) -> str:
    job_id = uuid.uuid4().hex
    await put_text(ctx.minio, f"raw/{job_id}.txt", manuscript)
    async with get_session(ctx.engine) as session:
        session.add(
            Job(
                job_id=job_id,
                status=JobStatus.PENDING,
                manuscript_key=f"raw/{job_id}.txt",
                callback_url=callback,
            )
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
    return job_id


def _mock_client(post: AsyncMock) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = post
    return client


async def test_webhook_delivered_on_allowlisted(worker_stack: Settings) -> None:
    ctx = await _make_ctx(worker_stack, WEBHOOK_ALLOWLIST="api.example.com")
    try:
        job_id = await _setup_with_callback(ctx, "A.\n\nB.", "https://api.example.com/hook")
        post = AsyncMock()
        with (
            patch("worker.handlers.stitch.is_allowed", return_value=True),
            patch("worker.handlers.stitch.httpx.AsyncClient", return_value=_mock_client(post)),
        ):
            await handle_stitch(ctx, StitchReady(job_id=job_id))

        post.assert_awaited_once()
        assert post.await_args is not None
        payload = post.await_args.kwargs["json"]
        assert payload["job_id"] == job_id
        assert payload["status"] == "COMPLETED"
        assert payload["final_key"] == f"out/{job_id}.mp3"
    finally:
        await close_context(ctx)


async def test_webhook_failure_still_completed(worker_stack: Settings) -> None:
    ctx = await _make_ctx(worker_stack, WEBHOOK_ALLOWLIST="api.example.com")
    try:
        job_id = await _setup_with_callback(ctx, "A.\n\nB.", "https://api.example.com/hook")
        post = AsyncMock(side_effect=httpx.ConnectError("unreachable"))
        with (
            patch("worker.handlers.stitch.is_allowed", return_value=True),
            patch("worker.handlers.stitch.httpx.AsyncClient", return_value=_mock_client(post)),
        ):
            await handle_stitch(ctx, StitchReady(job_id=job_id))

        post.assert_awaited_once()  # we tried...
        async with get_session(ctx.engine) as session:
            job = await session.get(Job, job_id)
            assert job is not None
            assert job.status == JobStatus.COMPLETED  # ...and the job is still COMPLETED
        assert await get_bytes(ctx.minio, f"out/{job_id}.mp3")  # asset intact
    finally:
        await close_context(ctx)


async def test_webhook_logonly_when_no_allowlist(worker_stack: Settings) -> None:
    ctx = await _make_ctx(worker_stack, WEBHOOK_ALLOWLIST="")  # default log-only mode
    try:
        job_id = await _setup_with_callback(ctx, "A.\n\nB.", "https://api.example.com/hook")
        with patch("worker.handlers.stitch.httpx.AsyncClient") as client_cls:
            await handle_stitch(ctx, StitchReady(job_id=job_id))

        client_cls.assert_not_called()  # no POST attempted in log-only mode
        async with get_session(ctx.engine) as session:
            job = await session.get(Job, job_id)
            assert job is not None
            assert job.status == JobStatus.COMPLETED
    finally:
        await close_context(ctx)
