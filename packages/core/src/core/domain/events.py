"""Message contracts (pydantic) — broker payloads carry pointers, never bytes.

The broker transports **events**, not data. Every field is a string key/identifier; the
actual bytes live in MinIO and are fetched by key on the consuming side. Each event carries
a defaulted, unique ``event_id``. The live pipeline does NOT dedupe on it — at-least-once
delivery is absorbed by the atomic state-CAS in ``core.infra.queries`` (the handlers are
re-runnable). ``event_id`` is kept as a stable correlation key for logging and for the
optional ``processed_events`` inbox helper.

Pure domain: no I/O imports (enforced by tests/unit/test_architecture.py).

  JobCreated    { event_id, job_id }              gateway -> q.parse
  TtsRequested  { event_id, job_id, task_id }     parse   -> q.tts   (xN)
  StitchReady   { event_id, job_id }              tts(last) -> q.stitch
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


def _new_event_id() -> str:
    """Fresh idempotency key. uuid4 → str so the contract is bytes-free."""
    return uuid.uuid4().hex


class _Event(BaseModel):
    """Base for all broker events: frozen (immutable in flight) + a defaulted event_id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(default_factory=_new_event_id)


class JobCreated(_Event):
    """Gateway → q.parse: a new manuscript was ingested; parse it."""

    job_id: str


class TtsRequested(_Event):
    """Parse → q.tts (fanned out ×N): synthesize one parsed block."""

    job_id: str
    task_id: str


class StitchReady(_Event):
    """TTS (the worker that decremented pending_count to 0) → q.stitch: all blocks done."""

    job_id: str
