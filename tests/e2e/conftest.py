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
from minio import Minio
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

GATEWAY_URL = "http://localhost:8000"
# Host-mapped ports from docker-compose.yml (probes run on the host, not in-network).
BROKER_URL = "amqp://guest:guest@localhost:5672/"
DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/consuma"
REDIS_URL = "redis://localhost:6379/0"
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


@pytest.fixture
async def publish_raw(
    stack: str,
) -> AsyncIterator[Callable[..., Awaitable[None]]]:
    """Inject a raw event onto a live queue — the duplicate-delivery injector (R3.2).

    Opens its own connection to the host-mapped RabbitMQ and declares the same
    topology the worker uses (idempotent), so a probe can re-publish a second copy
    of an event the pipeline already produced and assert exactly-once *effect*.
    """
    from core.infra import broker

    conn = await broker.connect(BROKER_URL)
    try:
        channel = await conn.channel()
        exchange = await broker.declare_full(channel)

        async def _publish(event: BaseModel, *, routing_key: str) -> None:
            await broker.publish(exchange, event, routing_key=routing_key)

        yield _publish
    finally:
        await conn.close()


@pytest.fixture
async def db_engine(stack: str) -> AsyncIterator[AsyncEngine]:
    """An async engine to the compose Postgres for asserting durable DB state.

    Probes that need to prove exactly-once effect (no extra task rows, no negative
    counter) read the durable truth directly rather than inferring it from /status.
    """
    from core.infra.db import get_engine

    engine = get_engine(DATABASE_URL)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def redis_client(stack: str) -> AsyncIterator[Redis]:
    """A Redis client to the compose Redis for inspecting coordination state.

    R4.1 reads the global TTS slot pool (``tts:slots``) to prove the semaphore is
    shared across scaled workers (exactly ``TTS_CONCURRENCY`` tokens, not N×).
    """
    from core.infra.redis import get_redis

    client = get_redis(REDIS_URL)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def minio_client(stack: str) -> Minio:
    """A MinIO client to the compose object store for asserting stored bytes.

    T-BEHAVIOR fetches ``raw/``, ``tts/`` and ``out/`` objects to prove the
    produced asset is correct and the stores agree. The async storage helpers wrap
    this sync client in ``to_thread``.
    """
    return Minio("localhost:9000", access_key="minioadmin", secret_key="minioadmin", secure=False)
