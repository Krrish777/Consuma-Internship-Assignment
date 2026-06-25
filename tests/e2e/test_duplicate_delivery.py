"""R3.2 — duplicate-delivery probe (L4): the same event twice → exactly-once effect.

At-least-once delivery means any event can arrive twice. The system must absorb
that with no corruption:

  * a duplicate ``JobCreated`` → parse's ``ON CONFLICT DO NOTHING`` on task rows +
    ``begin_parse`` CAS (counter seeded once) → NO extra task rows, counter intact;
  * a duplicate ``TtsRequested`` for an already-DONE task → B4's conditional claim
    (``UPDATE … WHERE status <> 'DONE'``) no-ops → NO second decrement → the fan-in
    counter never goes negative and no spurious early completion occurs.

The corruption these guards prevent (a Redis-SETNX dedup that evicts, or a Python
counter) would show as extra rows or a negative ``pending_count`` — so we assert
the durable DB truth directly, not just the HTTP status.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from core.domain.events import JobCreated, TtsRequested
from core.infra import broker
from core.infra.db import Task, get_session

pytestmark = pytest.mark.e2e


async def _task_rows(engine: AsyncEngine, job_id: str) -> list[Task]:
    async with get_session(engine) as session:
        result = await session.execute(select(Task).where(Task.job_id == job_id))
        return list(result.scalars().all())


async def test_duplicate_jobcreated_has_exactly_once_effect(
    client: httpx.AsyncClient,
    wait_for_status: Callable[..., Awaitable[str]],
    publish_raw: Callable[..., Awaitable[None]],
    db_engine: AsyncEngine,
) -> None:
    manuscript = "\n\n".join(f"Distinct block number {i}." for i in range(4))
    resp = await client.post("/jobs", json={"manuscript": manuscript})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    # Re-deliver the trigger: the SAME JobCreated object twice (identical event_id),
    # mimicking a true broker redelivery for an already-ingested job.
    dup = JobCreated(job_id=job_id)
    await publish_raw(dup, routing_key=broker.Q_PARSE)
    await publish_raw(dup, routing_key=broker.Q_PARSE)

    status = await wait_for_status(job_id, target="COMPLETED", timeout=180.0)
    assert status == "COMPLETED", f"job {job_id} ended {status}, expected COMPLETED"

    # Exactly-once: the duplicate created NO extra task rows and the barrier reached 0.
    tasks = await _task_rows(db_engine, job_id)
    assert len(tasks) == 4, f"expected 4 task rows, duplicate JobCreated produced {len(tasks)}"
    assert all(t.status == "DONE" for t in tasks), "not all tasks DONE after duplicate delivery"
    final = (await client.get(f"/status/{job_id}")).json()
    assert final["pending_count"] == 0, "fan-in counter wrong after duplicate JobCreated"


async def test_duplicate_ttsrequested_does_not_double_decrement(
    client: httpx.AsyncClient,
    wait_for_status: Callable[..., Awaitable[str]],
    publish_raw: Callable[..., Awaitable[None]],
    db_engine: AsyncEngine,
) -> None:
    manuscript = "\n\n".join(f"Unique narration line {i}." for i in range(3))
    resp = await client.post("/jobs", json={"manuscript": manuscript})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    assert await wait_for_status(job_id, target="COMPLETED", timeout=180.0) == "COMPLETED"

    # Re-deliver a TtsRequested for a REAL, already-DONE task (id read from the DB so
    # the test doesn't depend on the task_id format). The B4 conditional claim must
    # no-op: no second decrement, counter stays 0 (never negative).
    tasks = await _task_rows(db_engine, job_id)
    assert len(tasks) == 3
    dup = TtsRequested(job_id=job_id, task_id=tasks[0].task_id)
    await publish_raw(dup, routing_key=broker.Q_TTS)
    await publish_raw(dup, routing_key=broker.Q_TTS)
    await asyncio.sleep(4.0)  # let the worker consume and no-op the redelivery

    final = (await client.get(f"/status/{job_id}")).json()
    assert final["status"] == "COMPLETED", "job left COMPLETED state after duplicate TtsRequested"
    assert final["pending_count"] == 0, "duplicate TtsRequested double-decremented the barrier"
    assert len(await _task_rows(db_engine, job_id)) == 3, "duplicate TtsRequested added a task row"
