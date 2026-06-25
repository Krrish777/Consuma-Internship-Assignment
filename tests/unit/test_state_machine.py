"""R1.2 — Job FSM unit tests (no Docker required).

Covers: legal forward path, any→FAILED, terminal states, illegal
backwards/skip transitions. Pure domain — fast, always runnable.
"""

from __future__ import annotations

import pytest

from core.domain.state import JobStatus, can_transition


def test_legal_forward_path() -> None:
    path = [
        (JobStatus.PENDING, JobStatus.PARSING),
        (JobStatus.PARSING, JobStatus.GENERATING),
        (JobStatus.GENERATING, JobStatus.STITCHING),
        (JobStatus.STITCHING, JobStatus.COMPLETED),
    ]
    for cur, nxt in path:
        assert can_transition(cur, nxt), f"expected {cur} → {nxt} to be legal"


def test_any_non_terminal_to_failed() -> None:
    terminal = {JobStatus.COMPLETED, JobStatus.FAILED}
    for status in JobStatus:
        if status not in terminal:
            assert can_transition(status, JobStatus.FAILED), f"{status} → FAILED must be legal"


def test_completed_is_terminal() -> None:
    for status in JobStatus:
        assert not can_transition(JobStatus.COMPLETED, status), (
            f"COMPLETED → {status} must be illegal (terminal state)"
        )


def test_failed_is_terminal() -> None:
    for status in JobStatus:
        assert not can_transition(JobStatus.FAILED, status), (
            f"FAILED → {status} must be illegal (terminal state)"
        )


def test_backwards_transitions_illegal() -> None:
    assert not can_transition(JobStatus.PARSING, JobStatus.PENDING)
    assert not can_transition(JobStatus.GENERATING, JobStatus.PARSING)
    assert not can_transition(JobStatus.STITCHING, JobStatus.GENERATING)
    assert not can_transition(JobStatus.COMPLETED, JobStatus.STITCHING)


@pytest.mark.parametrize(
    "skip_to",
    [
        JobStatus.GENERATING,
        JobStatus.STITCHING,
        JobStatus.COMPLETED,
    ],
)
def test_skip_transitions_from_pending_illegal(skip_to: JobStatus) -> None:
    assert not can_transition(JobStatus.PENDING, skip_to)
