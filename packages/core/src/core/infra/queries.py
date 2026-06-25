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

from sqlalchemy import CursorResult, update
from sqlalchemy.ext.asyncio import AsyncSession

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
