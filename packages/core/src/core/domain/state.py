"""Job finite-state machine — legal-transition guard (spec §6).

Pure domain: no I/O. The FSM is data + a predicate.

BACKLOG H-FSM: callers MUST use compare-and-set SQL
  UPDATE jobs SET status=:next WHERE job_id=:id AND status=:expected
rather than read-then-write Python, so two workers racing the same row
can't both win. `can_transition` is the pure guard — the DB enforces it atomically.
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
