"""Async Postgres adapter — engine, session, ORM models.

Models live here (core/infra), not core/domain, because SQLAlchemy is I/O;
the architecture test enforces that core/domain stays pure. The JobStatus enum
is imported from core/domain/state (pure) and mapped here via a native Postgres
ENUM type so illegal values are rejected at the DB level too.

Fan-in note: pending_count updates and status transitions
MUST use compare-and-set SQL, not Python read-then-write. This module provides
the schema; handlers in worker/ own the atomic UPDATE … RETURNING logic.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, cast

from sqlalchemy import (
    CursorResult,
    DateTime,
    Enum as PgEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    delete,
    func,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from core.domain.state import JobStatus


class Base(DeclarativeBase):
    pass


_JOB_STATUS_ENUM = PgEnum(
    JobStatus,
    name="job_status",
    values_callable=lambda e: [m.value for m in e],
)

_TASK_STATUS_ENUM = PgEnum(
    "PENDING",
    "DONE",
    "FAILED",
    name="task_status",
)


class Job(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    status: Mapped[JobStatus] = mapped_column(
        _JOB_STATUS_ENUM, nullable=False, default=JobStatus.PENDING
    )
    pending_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manuscript_key: Mapped[str] = mapped_column(String, nullable=False)
    final_key: Mapped[str | None] = mapped_column(String, nullable=True)
    callback_url: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tasks: Mapped[list["Task"]] = relationship("Task", back_populates="job", lazy="raise")


class Task(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.job_id"), nullable=False)
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    block_hash: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(_TASK_STATUS_ENUM, nullable=False, default="PENDING")
    audio_key: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    job: Mapped["Job"] = relationship("Job", back_populates="tasks", lazy="raise")

    __table_args__ = (Index("ix_tasks_job_id_block_index", "job_id", "block_index", unique=True),)


class ProcessedEvent(Base):
    """Optional event-level idempotency inbox (event_id PK). NOT on the hot path.

    The live pipeline absorbs at-least-once delivery with the atomic state-CAS in
    ``queries.py`` — see :func:`mark_event` for why an inbox skip-gate is the wrong
    fit here. Retained as a tested building block, not a wired guard.
    """

    __tablename__ = "processed_events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    consumed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


def get_engine(url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine for the given DATABASE_URL."""
    return create_async_engine(url, echo=False, pool_pre_ping=True)


@asynccontextmanager
async def get_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Async context manager yielding a session that auto-rolls-back on error."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


async def create_tables(conn: AsyncConnection) -> None:
    """Create all tables (used in tests; production uses Alembic migrations)."""
    await conn.run_sync(Base.metadata.create_all)


async def mark_event(session: AsyncSession, event_id: str) -> bool:
    """``INSERT event_id ON CONFLICT DO NOTHING`` — event-level idempotency helper.

    NOT the pipeline's idempotency authority. The live guard is the atomic
    state-CAS in ``queries.py`` (``complete_task_and_decrement`` and friends): the
    handlers are deliberately re-runnable and re-publish on every redelivery, so a
    committed-but-unpublished event is recovered on the next delivery. An inbox
    *skip-gate* ("seen this event_id → do nothing") would defeat that recovery and
    strand lost publishes, so this is intentionally kept off the hot path.

    Returns True if the event_id was newly recorded, False if already present. Kept
    as a tested building block for any future effect that genuinely needs once-only
    (non-republishable) semantics.
    """
    stmt = pg_insert(ProcessedEvent).values(event_id=event_id).on_conflict_do_nothing()
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return result.rowcount == 1


async def purge_processed_events(session: AsyncSession, older_than_seconds: int) -> int:
    """Retention — delete inbox rows older than the window; return rows deleted.

    Bounds the processed_events table should it ever be written to. Uses DB-side
    ``now()`` so the cutoff is immune to app/DB clock skew. Wired into the gateway
    sweeper (``run_sweeper`` → ``purge_once``).
    """
    cutoff = func.now() - timedelta(seconds=older_than_seconds)
    stmt = delete(ProcessedEvent).where(ProcessedEvent.consumed_at < cutoff)
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return result.rowcount
