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

import asyncio
from collections.abc import AsyncIterator, Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from core.infra.db import (
    Job,
    ProcessedEvent,
    Task,
    create_tables,
    get_engine,
    get_session,
    mark_event,
    purge_processed_events,
)
from core.domain.state import JobStatus
from core.infra.queries import begin_parse, complete_task_and_decrement

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


# --- R4inbox: durable idempotency inbox (the authority) ----------------------


async def test_mark_event_dedupes_durably(engine: AsyncEngine) -> None:
    """mark_event is the AUTHORITY: True the first time, False for a duplicate."""
    async with get_session(engine) as session:
        assert await mark_event(session, "evt-mark-once") is True  # first delivery
        assert await mark_event(session, "evt-mark-once") is False  # duplicate absorbed
        await session.commit()


async def test_purge_processed_events_removes_aged_rows(engine: AsyncEngine) -> None:
    """H10 retention: rows older than the window are deleted; recent ones survive."""
    async with get_session(engine) as session:
        session.add(ProcessedEvent(event_id="inbox-fresh"))
        session.add(
            ProcessedEvent(
                event_id="inbox-aged",
                consumed_at=datetime.now(UTC) - timedelta(days=8),
            )
        )
        await session.commit()

    async with get_session(engine) as session:
        deleted = await purge_processed_events(session, older_than_seconds=7 * 86_400)
        await session.commit()
    assert deleted == 1  # only the 8-day-old row qualifies

    async with get_session(engine) as session:
        remaining = (await session.execute(select(ProcessedEvent.event_id))).scalars().all()
    assert "inbox-aged" not in remaining
    assert "inbox-fresh" in remaining


# --- B4: atomic fan-in decrement, guarded in-transaction (H3) ----------------


async def _seed_job_with_tasks(engine: AsyncEngine, n: int) -> tuple[str, list[str]]:
    """Seed one Job(pending_count=n) with n PENDING Tasks; return (job_id, task_ids)."""
    async with get_session(engine) as session:
        job = Job(manuscript_key=f"raw/fanin-{n}.txt", pending_count=n)
        session.add(job)
        await session.flush()
        job_id = job.job_id
        task_ids: list[str] = []
        for i in range(n):
            task = Task(job_id=job_id, block_index=i, block_hash=f"h{i}")
            session.add(task)
            await session.flush()
            task_ids.append(task.task_id)
        await session.commit()
    return job_id, task_ids


async def test_fan_in_decrements_to_zero_exactly_once(engine: AsyncEngine) -> None:
    """N task completions decrement N..0; exactly one caller observes 0 (-> StitchReady)."""
    job_id, task_ids = await _seed_job_with_tasks(engine, 3)

    observed: list[int | None] = []
    for tid in task_ids:
        async with get_session(engine) as session:
            observed.append(
                await complete_task_and_decrement(session, job_id, tid, f"tts/{tid}.wav")
            )

    assert observed == [2, 1, 0]
    assert observed.count(0) == 1  # exactly one barrier-crossing


async def test_fan_in_duplicate_task_is_noop(engine: AsyncEngine) -> None:
    """The SAME task delivered twice decrements once: 2nd claim rowcount 0 -> None, no decrement."""
    job_id, task_ids = await _seed_job_with_tasks(engine, 2)
    tid = task_ids[0]

    async with get_session(engine) as session:
        first = await complete_task_and_decrement(session, job_id, tid, "tts/dup.wav")
    async with get_session(engine) as session:
        duplicate = await complete_task_and_decrement(session, job_id, tid, "tts/dup.wav")

    assert first == 1  # decremented from 2 -> 1
    assert duplicate is None  # already-counted: no-op sentinel

    async with get_session(engine) as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.pending_count == 1  # decremented exactly once, not twice


async def test_fan_in_concurrent_no_lost_update(engine: AsyncEngine) -> None:
    """N concurrent decrements (separate sessions) lose no update: results == perm(0..N-1)."""
    n = 8
    job_id, task_ids = await _seed_job_with_tasks(engine, n)

    async def complete(tid: str) -> int | None:
        async with get_session(engine) as session:
            return await complete_task_and_decrement(session, job_id, tid, f"tts/{tid}.wav")

    results = await asyncio.gather(*(complete(tid) for tid in task_ids))

    assert sorted(r for r in results if r is not None) == list(range(n))  # 0..n-1, no dupes
    assert results.count(0) == 1  # exactly one saw the barrier

    async with get_session(engine) as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.pending_count == 0


# --- H15: set pending_count only on the first CAS out of PENDING --------------


async def test_counter_once_sets_pending_count_on_first_call(engine: AsyncEngine) -> None:
    """First begin_parse: CAS PENDING->PARSING and seed pending_count=N; returns True."""
    async with get_session(engine) as session:
        job = Job(manuscript_key="raw/h15-first.txt")  # PENDING, pending_count=0
        session.add(job)
        await session.commit()
        job_id = job.job_id

    async with get_session(engine) as session:
        assert await begin_parse(session, job_id, 5) is True

    async with get_session(engine) as session:
        refreshed = await session.get(Job, job_id)
        assert refreshed is not None
        assert refreshed.pending_count == 5
        assert refreshed.status == JobStatus.PARSING


async def test_counter_once_rerun_does_not_reset(engine: AsyncEngine) -> None:
    """A parse redelivery must NOT reset the (possibly-decremented) counter."""
    async with get_session(engine) as session:
        job = Job(manuscript_key="raw/h15-rerun.txt")
        session.add(job)
        await session.commit()
        job_id = job.job_id

    async with get_session(engine) as session:
        assert await begin_parse(session, job_id, 5) is True  # first run seeds 5

    # Simulate one TTS task completing: 5 -> 4.
    async with get_session(engine) as session:
        await session.execute(
            text("UPDATE jobs SET pending_count = pending_count - 1 WHERE job_id = :id"),
            {"id": job_id},
        )
        await session.commit()

    # Parse redelivered (job already PARSING): must be a no-op on the counter.
    async with get_session(engine) as session:
        assert await begin_parse(session, job_id, 5) is False

    async with get_session(engine) as session:
        refreshed = await session.get(Job, job_id)
        assert refreshed is not None
        assert refreshed.pending_count == 4  # left intact, NOT reset to 5
        assert refreshed.status == JobStatus.PARSING


async def test_counter_once_concurrent_only_one_wins(engine: AsyncEngine) -> None:
    """Concurrent first-CAS: exactly one begin_parse wins; counter seeded once."""
    async with get_session(engine) as session:
        job = Job(manuscript_key="raw/h15-concurrent.txt")
        session.add(job)
        await session.commit()
        job_id = job.job_id

    async def attempt() -> bool:
        async with get_session(engine) as session:
            return await begin_parse(session, job_id, 7)

    results = await asyncio.gather(*(attempt() for _ in range(5)))

    assert results.count(True) == 1  # exactly one first-runner
    async with get_session(engine) as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.pending_count == 7  # seeded once
