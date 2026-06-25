"""T1 e2e helpers — pure docker/subprocess + manuscript builders (06-e2e.md).

No fixtures here: plain callables the conftest fixtures and probe modules import.
Container manipulation drives the REAL compose stack (``docker kill`` / ``restart``)
for the crash-recovery and dependency-bounce probes — that fault injection is the
whole point of L4, and it can't be simulated against a testcontainer.
"""

from __future__ import annotations

import subprocess

from core.domain.vendor import POISON_MARKER

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
    for redelivery (R3.1). Use ``stop_container`` for the graceful-SIGTERM path.
    """
    _docker("kill", name)


def stop_container(name: str) -> None:
    """Graceful SIGTERM (``docker stop``) — exercises the clean-shutdown drain."""
    _docker("stop", name)


def start_container(name: str) -> None:
    """Start a previously stopped/killed container."""
    _docker("start", name)


def restart_container(name: str) -> None:
    """Bounce a dependency mid-job (E-EDGE dependency-down scenario)."""
    _docker("restart", name)


def poison_manuscript() -> str:
    """A manuscript that fails parse on EVERY attempt → DLQ after 3 retries.

    The marker triggers ``simulate_parse`` to raise ``VendorError`` unconditionally
    (R2.0 single retryable class), so the JobCreated message exhausts the 1/4/16s
    ladder and dead-letters — the substrate for the R3.3 poison-pill probe.
    """
    return f"This drama is cursed.\n\n{POISON_MARKER}\n\nIt fails every attempt."
