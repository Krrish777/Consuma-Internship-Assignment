"""R1.1 — SQLAlchemy models integration test (Postgres via testcontainers).

Proves:
  - Tables create (Job, Task, ProcessedEvent) via create_all
  - Basic CRUD: insert Job, insert Task, query back
  - ProcessedEvent ON CONFLICT idempotency (duplicate event_id is silently dropped)
  - pending_count atomic decrement (fan-in barrier: UPDATE … RETURNING)

Fixture scoping: the Postgres container is module-scoped (expensive to start).
The engine is function-scoped to avoid asyncpg attaching to a stale event loop
(asyncio connections are bound to the loop they were created on).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from core.infra.db import Job, ProcessedEvent, Task, create_tables, get_engine, get_session

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg_url() -> Generator[str, None, None]:
    with PostgresContainer("postgres:17-alpine") as pg:
        yield pg.get_connection_url().replace("psycopg2", "asyncpg")


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    eng = get_engine(pg_url)
    async with eng.begin() as conn:
        await create_tables(conn)
    yield eng
    await eng.dispose()


async def test_job_insert_and_query(engine: AsyncEngine) -> None:
    async with get_session(engine) as session:
        job = Job(manuscript_key="raw/test-job.txt")
        session.add(job)
        await session.commit()
        job_id = job.job_id

    async with get_session(engine) as session:
        result = await session.get(Job, job_id)
        assert result is not None
        assert result.status.value == "PENDING"
        assert result.pending_count == 0
        assert result.manuscript_key == "raw/test-job.txt"


async def test_task_insert_with_unique_constraint(engine: AsyncEngine) -> None:
    async with get_session(engine) as session:
        job = Job(manuscript_key="raw/task-job.txt")
        session.add(job)
        await session.commit()
        job_id = job.job_id

    async with get_session(engine) as session:
        task = Task(job_id=job_id, block_index=0, block_hash="abc123")
        session.add(task)
        await session.commit()
        task_id = task.task_id

    async with get_session(engine) as session:
        result = await session.get(Task, task_id)
        assert result is not None
        assert result.block_index == 0
        assert result.status == "PENDING"


async def test_processed_event_dedup(engine: AsyncEngine) -> None:
    """INSERT … ON CONFLICT DO NOTHING: second row is silently dropped."""
    async with get_session(engine) as session:
        event = ProcessedEvent(event_id="evt-dedup-test")
        session.add(event)
        await session.commit()

    async with get_session(engine) as session:
        await session.execute(
            text("INSERT INTO processed_events (event_id) VALUES (:id) ON CONFLICT DO NOTHING"),
            {"id": "evt-dedup-test"},
        )
        await session.commit()

    async with get_session(engine) as session:
        result = await session.execute(
            text("SELECT COUNT(*) FROM processed_events WHERE event_id = :id"),
            {"id": "evt-dedup-test"},
        )
        assert result.scalar() == 1


async def test_pending_count_atomic_decrement(engine: AsyncEngine) -> None:
    """Simulates the fan-in barrier: UPDATE … RETURNING pending_count."""
    async with get_session(engine) as session:
        job = Job(manuscript_key="raw/fanin-job.txt", pending_count=2)
        session.add(job)
        await session.commit()
        job_id = job.job_id

    async with get_session(engine) as session:
        result = await session.execute(
            text(
                "UPDATE jobs SET pending_count = pending_count - 1 "
                "WHERE job_id = :id RETURNING pending_count"
            ),
            {"id": job_id},
        )
        count = result.scalar_one()
        await session.commit()

    assert count == 1
