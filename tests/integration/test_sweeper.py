"""G8 / R3.4 — PENDING-sweeper / reconciler (L3).

Proves the outbox-via-state recovery: a Job stuck in PENDING with **no**
JobCreated ever published gets re-driven by ``sweep_once`` — the event lands on
q.parse — while healthy / non-PENDING jobs are left untouched and the sweeper
never mutates job status.

Only Postgres + RabbitMQ are needed (``sweep_once`` is a standalone function).
Each test drives its own event loop and opens its own broker connection, because
aio-pika channels are bound to the loop that created them.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer
from testcontainers.rabbitmq import RabbitMqContainer

from core.infra import broker
from core.infra.db import Job, create_tables, get_engine, get_session
from core.domain.state import JobStatus
from gateway.sweeper import sweep_once

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def sweeper_ctx() -> Generator[dict[str, str], None, None]:
    """Start Postgres + RabbitMQ, create the schema, yield connection URLs."""
    with (
        PostgresContainer("postgres:17-alpine") as pg,
        RabbitMqContainer("rabbitmq:4-management") as rmq,
    ):
        pg_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        rmq_url = f"amqp://guest:guest@{rmq.get_container_host_ip()}:{rmq.get_exposed_port(5672)}/"

        async def _setup_schema() -> None:
            engine = get_engine(pg_url)
            async with engine.begin() as conn:
                await create_tables(conn)
            await engine.dispose()

        asyncio.run(_setup_schema())
        yield {"pg_url": pg_url, "rmq_url": rmq_url}


async def _seed_job(pg_url: str, job_id: str, status: JobStatus) -> None:
    engine = get_engine(pg_url)
    try:
        async with get_session(engine) as session:
            session.add(Job(job_id=job_id, status=status, manuscript_key=f"raw/{job_id}.txt"))
            await session.commit()
    finally:
        await engine.dispose()


async def _run_sweep(pg_url: str, rmq_url: str, pending_timeout_s: int) -> int:
    """Open a fresh engine + broker connection (this loop) and sweep once."""
    engine = get_engine(pg_url)
    conn = await broker.connect(rmq_url)
    try:
        channel = await conn.channel()
        exchange = await broker.declare_full(channel)
        return await sweep_once(
            engine=engine, exchange=exchange, pending_timeout_s=pending_timeout_s
        )
    finally:
        await conn.close()
        await engine.dispose()


async def _drain_for(rmq_url: str, job_id: str) -> dict[str, Any] | None:
    """Drain q.parse (acking all) until the JobCreated for job_id is found."""
    conn = await broker.connect(rmq_url)
    try:
        channel = await conn.channel()
        q = await channel.declare_queue(broker.Q_PARSE, passive=True)
        for _ in range(30):
            msg = await q.get(timeout=3, fail=False)
            if msg is None:
                return None
            body: dict[str, Any] = json.loads(msg.body)
            await msg.ack()
            if body.get("job_id") == job_id:
                return body
        return None
    finally:
        await conn.close()


async def _status_of(pg_url: str, job_id: str) -> str:
    engine = get_engine(pg_url)
    try:
        async with get_session(engine) as session:
            result = await session.execute(select(Job).where(Job.job_id == job_id))
            return result.scalar_one().status.value
    finally:
        await engine.dispose()


def test_sweep_once_republishes_stale_pending(sweeper_ctx: dict[str, str]) -> None:
    """An orphaned PENDING job (no event published) -> sweep re-publishes JobCreated."""
    job_id = "sweep-stale-1"
    asyncio.run(_seed_job(sweeper_ctx["pg_url"], job_id, JobStatus.PENDING))

    # timeout 0 -> any PENDING row counts as stale
    count = asyncio.run(_run_sweep(sweeper_ctx["pg_url"], sweeper_ctx["rmq_url"], 0))
    assert count >= 1

    found = asyncio.run(_drain_for(sweeper_ctx["rmq_url"], job_id))
    assert found is not None, f"JobCreated for {job_id} not re-published to q.parse"
    assert found["job_id"] == job_id
    assert "event_id" in found  # pointers-only


def test_sweep_once_does_not_change_status(sweeper_ctx: dict[str, str]) -> None:
    """MUST NOT mutate status — a swept job stays PENDING (consumer advances it)."""
    job_id = "sweep-stale-2"
    asyncio.run(_seed_job(sweeper_ctx["pg_url"], job_id, JobStatus.PENDING))
    asyncio.run(_run_sweep(sweeper_ctx["pg_url"], sweeper_ctx["rmq_url"], 0))
    assert asyncio.run(_status_of(sweeper_ctx["pg_url"], job_id)) == "PENDING"


def test_sweep_once_skips_healthy_pending(sweeper_ctx: dict[str, str]) -> None:
    """A fresh PENDING job within the generous timeout is left untouched (count 0)."""
    job_id = "sweep-healthy-1"
    asyncio.run(_seed_job(sweeper_ctx["pg_url"], job_id, JobStatus.PENDING))
    # huge timeout -> the just-created job is not yet stale
    count = asyncio.run(_run_sweep(sweeper_ctx["pg_url"], sweeper_ctx["rmq_url"], 3600))
    found = asyncio.run(_drain_for(sweeper_ctx["rmq_url"], job_id))
    assert count == 0
    assert found is None


def test_sweep_once_ignores_non_pending(sweeper_ctx: dict[str, str]) -> None:
    """A job past PENDING (e.g. GENERATING) is never re-driven, even when stale."""
    job_id = "sweep-generating-1"
    asyncio.run(_seed_job(sweeper_ctx["pg_url"], job_id, JobStatus.GENERATING))
    count = asyncio.run(_run_sweep(sweeper_ctx["pg_url"], sweeper_ctx["rmq_url"], 0))
    found = asyncio.run(_drain_for(sweeper_ctx["rmq_url"], job_id))
    assert found is None
    # other stale PENDING jobs from earlier tests may exist; this job must not appear
    assert count >= 0
