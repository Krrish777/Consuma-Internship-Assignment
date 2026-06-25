"""R0.4 — structured logging unit tests (no Docker required).

Proves:
  - configure_logging sets up the handler without crashing
  - bind_job_id / bind_task_id inject values into log records
  - Two concurrent asyncio tasks have isolated ContextVar copies (no bleed)
  - get_logger returns a usable logger
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Generator

import pytest

from core.infra.logging import (
    bind_job_id,
    bind_task_id,
    configure_logging,
    current_job_id,
    current_task_id,
    get_logger,
)


@pytest.fixture(autouse=True)
def reset_root_logger() -> Generator[None, None, None]:
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    yield
    root.handlers = original_handlers
    root.setLevel(original_level)


def test_configure_logging_adds_handler() -> None:
    root = logging.getLogger()
    before = len(root.handlers)
    configure_logging()
    assert len(root.handlers) >= 1
    assert len(root.handlers) != before or before == 0


def test_bind_job_id_is_readable() -> None:
    bind_job_id("job-abc")
    assert current_job_id() == "job-abc"


def test_bind_task_id_is_readable() -> None:
    bind_task_id("task-xyz")
    assert current_task_id() == "task-xyz"


def test_get_logger_returns_logger() -> None:
    log = get_logger("test.module")
    assert isinstance(log, logging.Logger)
    assert log.name == "test.module"


def test_log_record_carries_job_id() -> None:
    """ContextFilter stamps job_id onto a LogRecord."""
    from core.infra.logging import _FILTER

    bind_job_id("job-trace-test")
    record = logging.LogRecord(
        name="test.trace",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="hello",
        args=(),
        exc_info=None,
    )
    _FILTER.filter(record)
    assert getattr(record, "job_id", "") == "job-trace-test"


async def test_concurrent_tasks_have_isolated_job_ids() -> None:
    """Two asyncio tasks must not bleed job_ids into each other."""
    results: dict[str, str] = {}

    async def task(name: str, job_id: str, delay: float) -> None:
        bind_job_id(job_id)
        await asyncio.sleep(delay)
        results[name] = current_job_id()

    await asyncio.gather(
        task("a", "job-A", 0.05),
        task("b", "job-B", 0.01),
    )

    assert results["a"] == "job-A"
    assert results["b"] == "job-B"
