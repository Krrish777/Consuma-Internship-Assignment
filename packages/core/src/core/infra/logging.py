"""Structured logging with job_id trace key (CLAUDE.md observability).

`configure_logging()` sets up JSON-ish log formatting for the process.
`get_logger(name)` returns a standard logger — callers use normal `log.info(...)`.
`bind_job_id(job_id)` stores the id in a ContextVar so every log record in this
asyncio Task (and any Tasks it spawns) automatically carries the trace key.

Why ContextVar: each asyncio Task inherits a *copy* of the current Context on
creation, so two concurrent jobs run in isolated copies — they cannot bleed IDs
into each other even without explicit locking (unlike a module-level global).
"""

from __future__ import annotations

import logging
import logging.config
from contextvars import ContextVar

_job_id_var: ContextVar[str] = ContextVar("job_id", default="")
_task_id_var: ContextVar[str] = ContextVar("task_id", default="")


class _ContextFilter(logging.Filter):
    """Inject job_id / task_id from ContextVars into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.job_id = _job_id_var.get()
        record.task_id = _task_id_var.get()
        return True


_FILTER = _ContextFilter()

_FMT = "%(levelname)s %(name)s job=%(job_id)s task=%(task_id)s %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logger with the structured format. Call once at process start."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FMT))
    handler.addFilter(_FILTER)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.addFilter(_FILTER)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. The ContextFilter is on the root handler."""
    return logging.getLogger(name)


def bind_job_id(job_id: str) -> None:
    """Bind job_id to the current asyncio context (copied into child tasks)."""
    _job_id_var.set(job_id)


def bind_task_id(task_id: str) -> None:
    """Bind task_id to the current asyncio context."""
    _task_id_var.set(task_id)


def current_job_id() -> str:
    return _job_id_var.get()


def current_task_id() -> str:
    return _task_id_var.get()
