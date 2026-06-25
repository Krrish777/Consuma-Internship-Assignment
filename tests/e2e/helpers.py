"""E2E helpers — pure docker/subprocess + manuscript builders.

No fixtures here: plain callables the conftest fixtures and probe modules import.
Container manipulation drives the REAL compose stack (``docker kill`` / ``restart``)
for the crash-recovery and dependency-bounce probes — that fault injection is the
whole point, and it can't be simulated against a testcontainer.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from core.domain.vendor import POISON_MARKER

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Compose container names (project "consuma" from docker-compose.yml `name:`).
WORKER = "consuma-worker-1"
GATEWAY = "consuma-gateway-1"
REDIS = "consuma-redis-1"
RABBITMQ = "consuma-rabbitmq-1"
MINIO = "consuma-minio-1"
POSTGRES = "consuma-postgres-1"


def _docker(*args: str) -> str:
    """Run a docker CLI command, raising with captured output on failure."""
    result = subprocess.run(["docker", *args], check=True, capture_output=True, text=True)
    return result.stdout


def kill_container(name: str) -> None:
    """Ungraceful SIGKILL (``docker kill``) — simulates a crash, no clean drain.

    The killed worker's in-flight message is unacked, so the broker releases it
    for redelivery. Use ``stop_container`` for the graceful-SIGTERM path.
    """
    _docker("kill", name)


def stop_container(name: str) -> None:
    """Graceful SIGTERM (``docker stop``) — exercises the clean-shutdown drain."""
    _docker("stop", name)


def start_container(name: str) -> None:
    """Start a previously stopped/killed container."""
    _docker("start", name)


def restart_container(name: str) -> None:
    """Bounce a dependency mid-job (dependency-down scenario)."""
    _docker("restart", name)


def flush_redis(name: str = REDIS) -> None:
    """Wipe ALL Redis keys (``FLUSHALL``) — deterministically simulates a Redis loss.

    Models the H1 failure precisely: ``tts:slots`` and its init marker vanish, as
    they would on an eviction or a restart-without-persistence. Preferred over
    ``docker restart`` for this probe because redis:7-alpine's default RDB snapshots
    could reload on restart (the container layer survives a restart), which would
    leave the pool intact and make the probe vacuous. ``FLUSHALL`` has no such
    ambiguity and keeps the connection up, isolating the test to the re-seed path.
    """
    _docker("exec", name, "redis-cli", "FLUSHALL")


def redis_llen(key: str, name: str = REDIS) -> int:
    """Return the length of a Redis list (used to assert the slots pool state)."""
    return int(_docker("exec", name, "redis-cli", "LLEN", key).strip())


def scale_workers(n: int) -> None:
    """Scale the worker service to N replicas (the deployment shape for R4.1/I4).

    ``--scale`` only adds/removes replicas; it doesn't recreate the running ones,
    so it avoids the recreate race. All replicas share the ONE global Redis
    semaphore — that is exactly what the R4.1 probe asserts (Constraint A is global,
    not per-process).
    """
    subprocess.run(
        ["docker", "compose", "up", "-d", "--scale", f"worker={n}", "--no-build"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )


def poison_manuscript() -> str:
    """A manuscript that fails parse on EVERY attempt → DLQ after 3 retries.

    The marker triggers ``simulate_parse`` to raise ``VendorError`` unconditionally
    (R2.0 single retryable class), so the JobCreated message exhausts the 1/4/16s
    ladder and dead-letters — the substrate for the R3.3 poison-pill probe.
    """
    return f"This drama is cursed.\n\n{POISON_MARKER}\n\nIt fails every attempt."
