"""Gateway pydantic schema unit tests (no Docker required)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gateway.schemas import CreateJobRequest, JobAccepted, JobStatusResponse


def test_create_job_request_valid() -> None:
    req = CreateJobRequest(manuscript="Hello world")
    assert req.manuscript == "Hello world"
    assert req.callback_url is None


def test_create_job_request_with_callback() -> None:
    req = CreateJobRequest(manuscript="text", callback_url="https://example.com/cb")
    assert req.callback_url == "https://example.com/cb"


def test_create_job_request_empty_manuscript_allowed() -> None:
    req = CreateJobRequest(manuscript="")
    assert req.manuscript == ""


def test_create_job_request_missing_manuscript() -> None:
    with pytest.raises(ValidationError):
        CreateJobRequest()  # type: ignore[call-arg]


def test_job_accepted_shape() -> None:
    resp = JobAccepted(job_id="abc123")
    assert resp.job_id == "abc123"


def test_job_status_response_shape() -> None:
    resp = JobStatusResponse(job_id="j1", status="PENDING", pending_count=3)
    assert resp.status == "PENDING"
    assert resp.pending_count == 3
    assert resp.final_key is None
