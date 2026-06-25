"""Job FSM unit tests (no Docker required).

Covers: legal forward path, any→FAILED, terminal states, illegal
backwards/skip transitions. Pure domain — fast, always runnable.
"""

from __future__ import annotations

import pytest

from core.domain.state import JobStatus, can_transition, expected_for


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


# --- expected_for (legal predecessors → the CAS WHERE guard) ---


def test_expected_for_forward_path_predecessors() -> None:
    assert expected_for(JobStatus.PARSING) == {JobStatus.PENDING}
    assert expected_for(JobStatus.GENERATING) == {JobStatus.PARSING}
    assert expected_for(JobStatus.STITCHING) == {JobStatus.GENERATING}
    assert expected_for(JobStatus.COMPLETED) == {JobStatus.STITCHING}


def test_expected_for_failed_is_every_non_terminal() -> None:
    assert expected_for(JobStatus.FAILED) == {
        JobStatus.PENDING,
        JobStatus.PARSING,
        JobStatus.GENERATING,
        JobStatus.STITCHING,
    }


def test_expected_for_pending_has_no_predecessors() -> None:
    # PENDING is the initial state — nothing transitions INTO it.
    assert expected_for(JobStatus.PENDING) == set()


def test_expected_for_is_exact_inverse_of_can_transition() -> None:
    # The CAS guard derived from expected_for must match the pure predicate
    # for every (current, next) pair — one source of truth, no drift.
    for current in JobStatus:
        for next_status in JobStatus:
            assert (current in expected_for(next_status)) == can_transition(current, next_status)
