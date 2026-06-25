"""Gateway Pydantic schemas.

Strict input/output contracts at the HTTP boundary. Empty manuscript is allowed
— it becomes a 0-block job that terminates via the zero-block path.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class CreateJobRequest(BaseModel):
    manuscript: str
    callback_url: str | None = None

    @field_validator("manuscript")
    @classmethod
    def manuscript_not_none(cls, v: str) -> str:
        return v


class JobAccepted(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    pending_count: int | None = None
    manuscript_key: str | None = None
    final_key: str | None = None


class StatsResponse(BaseModel):
    """Runtime aggregate.

    ``jobs`` is zero-filled across every FSM state so the JSON shape is stable
    for dashboards regardless of which statuses currently have rows.
    """

    jobs: dict[str, int]
