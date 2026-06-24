"""R1.4 — event contract tests (pure, no Docker).

Proves the broker payloads (spec §7): pointers/keys only, every event carries a
unique defaulted ``event_id`` for idempotency, and events are immutable once built.
"""

from __future__ import annotations

import pydantic
import pytest

from core.domain.events import JobCreated, StitchReady, TtsRequested


def test_event_id_is_defaulted_and_unique() -> None:
    a = JobCreated(job_id="job-1")
    b = JobCreated(job_id="job-1")
    # Defaulted (caller need not supply it) ...
    assert a.event_id
    assert b.event_id
    # ... and unique per instance (idempotency key must not collide across events).
    assert a.event_id != b.event_id


def test_event_id_may_be_supplied() -> None:
    e = JobCreated(job_id="job-1", event_id="fixed-id")
    assert e.event_id == "fixed-id"


def test_events_are_immutable() -> None:
    e = JobCreated(job_id="job-1")
    with pytest.raises(pydantic.ValidationError):
        e.job_id = "job-2"  # type: ignore[misc]


def test_roundtrip_json_preserves_fields() -> None:
    original = TtsRequested(job_id="job-7", task_id="task-3")
    restored = TtsRequested.model_validate_json(original.model_dump_json())
    assert restored == original
    assert restored.job_id == "job-7"
    assert restored.task_id == "task-3"
    assert restored.event_id == original.event_id


def test_contracts_carry_pointers_only_no_bytes() -> None:
    # Pointers-not-bytes (CLAUDE.md MUST): every field on every event is a str key,
    # never a payload blob. This guards against a future field smuggling bytes.
    for model in (JobCreated, TtsRequested, StitchReady):
        for name, field in model.model_fields.items():
            assert field.annotation is str, f"{model.__name__}.{name} must be str, got {field.annotation}"


def test_stitch_ready_shape() -> None:
    e = StitchReady(job_id="job-9")
    assert e.job_id == "job-9"
    assert e.event_id


def test_tts_requested_requires_task_id() -> None:
    with pytest.raises(pydantic.ValidationError):
        TtsRequested(job_id="job-1")  # type: ignore[call-arg]
