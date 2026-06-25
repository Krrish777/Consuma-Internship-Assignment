"""Atomic DB query operations the worker handlers call (Phase 3, spec §6).

These are the cross-boundary state operations that must stay correct under
*concurrent redelivery* — the place where at-least-once delivery meets durable
truth. They live in ``core/infra`` (I/O) and never in ``core/domain`` (the
architecture test keeps the domain pure). The SQL guards are built to honour the
H-FSM compare-and-set contract (``state.py``): ``rowcount`` is the authority and
``rowcount == 0`` is a *normal* concurrent outcome, not an error.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.state import JobStatus, expected_for
from core.infra.db import Job, Task


async def complete_task_and_decrement(
    session: AsyncSession, job_id: str, task_id: str, audio_key: str
) -> int | None:
    """Fan-in barrier (B4) — durable in-tx dup-guard + atomic decrement.

    The single most grade-bearing mechanism: knowing when all N parallel TTS
    tasks are done. Two statements in ONE transaction:

    1. **Durable, idempotent claim of THIS task** — a conditional
       ``UPDATE tasks SET status='DONE', audio_key=… WHERE task_id=… AND
       status<>'DONE'``. If its ``rowcount`` is 0 the task was already counted on
       a prior delivery, so we must NOT decrement again — return ``None``. This
       guard is the *authority*; it is durable and in the same transaction as the
       decrement (H3). Guarding with Redis ``SETNX`` instead would be evictable —
       eviction + redelivery → double-decrement → an early ``StitchReady`` → an
       incomplete drama wrongly marked ``COMPLETED``.
    2. **Atomic barrier decrement** — ``UPDATE jobs SET pending_count =
       pending_count - 1 RETURNING pending_count``. Done at the SQL level (never
       read-subtract-write, which would lose updates), so under concurrent
       redelivery exactly one caller observes ``remaining == 0``.

    Returns the new ``pending_count`` (the caller emits ``StitchReady`` when it
    is 0), or ``None`` when the task was already counted (idempotent no-op). The
    commit happens here so claim+decrement are one atomic unit; the broker ack
    happens *after* this returns, in the W4 handler.
    """
    claim = (
        update(Task)
        .where(Task.task_id == task_id, Task.status != "DONE")
        .values(status="DONE", audio_key=audio_key)
    )
    claimed = cast("CursorResult[Any]", await session.execute(claim))
    if claimed.rowcount == 0:
        # Already counted on an earlier delivery — idempotent no-op, no decrement.
        await session.commit()
        return None

    decrement = (
        update(Job)
        .where(Job.job_id == job_id)
        .values(pending_count=Job.pending_count - 1)
        .returning(Job.pending_count)
    )
    remaining = (await session.execute(decrement)).scalar_one()
    await session.commit()
    return int(remaining)


async def fail_task_and_decrement(session: AsyncSession, job_id: str, task_id: str) -> int | None:
    """DLQ fan-in resolver (W7/H4) — mark a poisoned task FAILED, then decrement.

    Same atomic shape as :func:`complete_task_and_decrement`, but it records the
    task as ``FAILED`` instead of ``DONE`` so a poisoned block still resolves the
    fan-in barrier (the job converges instead of stalling forever in GENERATING;
    the stitch handler later skips FAILED blocks).

    The conditional claim guards against BOTH terminal states
    (``status NOT IN ('DONE','FAILED')``) so a duplicate DLQ delivery — or a task
    that already succeeded — does not double-decrement. Returns the new
    ``pending_count`` (emit ``StitchReady`` when it is 0) or ``None`` when the task
    was already terminal (idempotent no-op).
    """
    claim = (
        update(Task)
        .where(Task.task_id == task_id, Task.status.not_in(["DONE", "FAILED"]))
        .values(status="FAILED")
    )
    claimed = cast("CursorResult[Any]", await session.execute(claim))
    if claimed.rowcount == 0:
        await session.commit()
        return None

    decrement = (
        update(Job)
        .where(Job.job_id == job_id)
        .values(pending_count=Job.pending_count - 1)
        .returning(Job.pending_count)
    )
    remaining = (await session.execute(decrement)).scalar_one()
    await session.commit()
    return int(remaining)


async def begin_parse(session: AsyncSession, job_id: str, n_blocks: int) -> bool:
    """Initialise ``pending_count`` exactly once, on the first CAS out of PENDING (H15).

    A parse message can be redelivered (at-least-once). If every delivery did an
    unconditional ``UPDATE jobs SET pending_count = N`` it would *resurrect*
    already-decremented tasks and the fan-in barrier would never reach 0 — the job
    would hang forever. So the counter is seeded only on the **first**
    compare-and-set transition out of PENDING:

        UPDATE jobs SET status='PARSING', pending_count=:n
         WHERE job_id=:id AND status='PENDING'

    ``rowcount == 1`` → this delivery won the transition: the counter is now seeded;
    return True so the caller proceeds with the (first-time) fan-out bookkeeping.
    ``rowcount == 0`` → a redelivery; the job already advanced, the counter is left
    untouched (this is the *normal* H-FSM concurrent outcome, not an error); return
    False so the caller skips counter init and merely re-publishes the N
    TtsRequested events (parse is a fan-out emitter that must stay re-runnable, H2).

    NOTE on the target state: the FSM (``core/domain/state.py``) makes PARSING the
    only legal successor of PENDING — a direct PENDING→GENERATING jump the card
    title loosely mentions is illegal. The PARSING→GENERATING advance is a separate
    transition owned by the Phase-4 parse handler after the fan-out.
    """
    cas = (
        update(Job)
        .where(Job.job_id == job_id, Job.status == JobStatus.PENDING)
        .values(status=JobStatus.PARSING, pending_count=n_blocks)
    )
    result = cast("CursorResult[Any]", await session.execute(cas))
    await session.commit()
    return result.rowcount == 1


async def advance_status(session: AsyncSession, job_id: str, to_status: JobStatus) -> bool:
    """Compare-and-set the job status to ``to_status`` (the H-FSM contract).

    The ``WHERE status IN (expected_for(to_status))`` guard is built from the one
    source of truth (``state.LEGAL``) so it can never drift from ``can_transition``.
    ``rowcount`` is the authority:

      * ``rowcount == 1`` → this worker won the transition; returns True.
      * ``rowcount == 0`` → a *normal* concurrent outcome (someone already advanced
        the job, or it is terminal), NOT an error; returns False. Handlers treat
        False as "already handled" and proceed idempotently — never retry, never
        mark the job FAILED on a lost CAS.
    """
    expected = expected_for(to_status)
    stmt = (
        update(Job).where(Job.job_id == job_id, Job.status.in_(expected)).values(status=to_status)
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    await session.commit()
    return result.rowcount == 1


async def job_counts_by_status(session: AsyncSession) -> dict[str, int]:
    """Per-status job counts for ``GET /stats`` (B6, powers G7/R5.1).

    A read-only aggregate — no locks, no writes. The count is done in the database
    with ``GROUP BY`` (never by loading every row into Python and counting), so it
    stays cheap as the table grows. Statuses with zero jobs are simply absent from
    the result (that is how ``GROUP BY`` behaves); zero-filling all six FSM states
    into a stable JSON shape is a presentation concern that belongs to the /stats
    endpoint (G7), not this query.
    """
    stmt = select(Job.status, func.count()).group_by(Job.status)
    rows = (await session.execute(stmt)).all()
    return {status.value: count for status, count in rows}
