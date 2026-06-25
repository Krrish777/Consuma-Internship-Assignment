"""Shared test configuration (harness note 11 — validation hierarchy).

Auto-skips `integration` and `e2e` tests when Docker is unavailable, so the no-Docker
`make check` stays green and the verification gates never false-fail for a missing daemon.
The integration/e2e test BODIES are written per rung (TDD) as the pipeline lands; the
scaffolding (markers + skip policy) exists now so those layers slot in without rework.
"""

from __future__ import annotations

import os
import shutil

import pytest

# Disable the Ryuk reaper sidecar — it times out on Windows named-pipe connections.
# Containers are still cleaned up by the `with` block's __exit__; Ryuk is only a
# last-resort guard for crashed test processes.
os.environ["TESTCONTAINERS_RYUK_DISABLED"] = "true"

# Also patch the config object directly in case testcontainers_config was already
# instantiated with a cached _ryuk_disabled=False before this module loaded.
from testcontainers.core.config import testcontainers_config as _tc_config  # noqa: E402

_tc_config.ryuk_disabled = True


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip Docker-dependent tests (integration/e2e) when no Docker daemon is on PATH."""
    if _docker_available():
        return
    skip = pytest.mark.skip(reason="Docker not available; integration/e2e require a daemon")
    for item in items:
        if "integration" in item.keywords or "e2e" in item.keywords:
            item.add_marker(skip)
