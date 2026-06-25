"""T1 — shared L4 e2e scaffolding (06-e2e.md).

Unlike the L3 integration suite (fresh testcontainers, handlers called directly),
these probes drive the REAL running compose stack: POST to the live gateway, poll
``GET /status``, inject duplicate events on the live broker, and ``docker kill``
named containers under fault injection.

``stack`` guarantees the stack is up AND running CURRENT code. The pre-Phase-4
worker image is an idle skeleton that consumes nothing, so "reuse whatever is
running" silently hangs every probe (a job sits in PENDING forever). We therefore
``docker compose up -d --build`` once per session — a near-instant layer-cache
no-op when the source is unchanged — then health-poll the gateway before yielding.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import httpx
import pytest

GATEWAY_URL = "http://localhost:8000"
TERMINAL_STATES = {"COMPLETED", "FAILED"}
_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def stack() -> Iterator[str]:
    """Ensure the 6-service compose stack is up, on current code, gateway-healthy."""
    subprocess.run(
        ["docker", "compose", "up", "-d", "--build"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    deadline = time.monotonic() + 180.0
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{GATEWAY_URL}/health", timeout=5.0).status_code == 200:
                break
        except httpx.HTTPError as exc:  # daemon/gateway not ready yet — keep polling
            last_err = exc
        time.sleep(2.0)
    else:
        raise RuntimeError(f"gateway never became healthy within 180s: {last_err}")
    yield GATEWAY_URL


@pytest.fixture
async def client(stack: str) -> AsyncIterator[httpx.AsyncClient]:
    """An async HTTP client bound to the live gateway."""
    async with httpx.AsyncClient(base_url=stack, timeout=30.0) as http:
        yield http


@pytest.fixture
def wait_for_status(
    client: httpx.AsyncClient,
) -> Callable[..., Awaitable[str]]:
    """Return a poller: await until a job hits ``target`` (or any terminal) or times out.

    Returns the last observed status string. Stopping on *any* terminal state means a
    job that FAILs when we expected COMPLETED returns promptly (a clear assertion
    failure) instead of burning the whole timeout.
    """

    async def _wait(job_id: str, *, target: str, timeout: float = 90.0) -> str:
        deadline = time.monotonic() + timeout
        status = "UNKNOWN"
        while time.monotonic() < deadline:
            resp = await client.get(f"/status/{job_id}")
            if resp.status_code == 200:
                status = str(resp.json()["status"])
                if status == target or status in TERMINAL_STATES:
                    return status
            await asyncio.sleep(1.0)
        return status

    return _wait
