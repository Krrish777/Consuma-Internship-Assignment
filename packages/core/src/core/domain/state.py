"""Job finite-state machine — legal-transition guard.

Pure domain: no I/O. The FSM is data + two pure helpers.

Applying transitions (the contract every status write MUST honor)
------------------------------------------------------------------------
`can_transition` is a pure predicate. Applying it as read-then-write in Python
(`if can_transition(cur, nxt): row.status = nxt`) lets two workers both read the
same `current`, both pass the guard, and both write — a lost-update race on the
`status` column. The fix is **compare-and-set** at the database:

    UPDATE jobs SET status = :next
     WHERE job_id = :id AND status IN (:expected)   -- expected = expected_for(next)

`result.rowcount` is the authority:
  - `rowcount == 1` → this worker won the transition; proceed.
  - `rowcount == 0` → **NORMAL concurrent outcome**, NOT an error. The row was
    already advanced (or terminal) by another worker. The handler treats this as
    "already handled" and proceeds idempotently — ack the message, do not retry,
    do not mark the job FAILED.

Build the `WHERE status IN (...)` guard from `expected_for(next)` so the SQL guard
and the pure predicate share one source of truth (`LEGAL`) and cannot drift. The
actual SQLAlchemy `update(...)` lives in the Phase 3/4 handlers — never here, so
`domain` stays I/O-free (architecture test).
"""

from __future__ import annotations

from enum import Enum


class JobStatus(str, Enum):
    PENDING = "PENDING"
    PARSING = "PARSING"
    GENERATING = "GENERATING"
    STITCHING = "STITCHING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


LEGAL: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING: {JobStatus.PARSING, JobStatus.FAILED},
    JobStatus.PARSING: {JobStatus.GENERATING, JobStatus.FAILED},
    JobStatus.GENERATING: {JobStatus.STITCHING, JobStatus.FAILED},
    JobStatus.STITCHING: {JobStatus.COMPLETED, JobStatus.FAILED},
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: set(),
}


def can_transition(current: JobStatus, next_status: JobStatus) -> bool:
    """Return True iff current → next_status is a legal FSM transition."""
    return next_status in LEGAL.get(current, set())


def expected_for(next_status: JobStatus) -> set[JobStatus]:
    """Return the legal predecessor states for ``next_status``.

    These are exactly the states from which ``next_status`` is reachable in one
    legal transition — i.e. the set a compare-and-set guard puts in its
    ``WHERE status IN (...)`` clause (see the module docstring). Derived from
    ``LEGAL`` so it can never drift from ``can_transition``. An initial state
    with no predecessors (PENDING) returns the empty set.
    """
    return {current for current, allowed in LEGAL.items() if next_status in allowed}
