"""Gateway integration tests.

Uses all three testcontainers (Postgres + RabbitMQ + MinIO) with the REAL
FastAPI lifespan to prove end-to-end gateway behaviour.

Proves:
  - lifespan starts cleanly, app.state.exchange usable, shuts down without leak
  - POST /jobs -> 202 + Job(PENDING) in PG + raw/<id>.txt in MinIO + JobCreated on q.parse
  - GET /status/<id> -> 200 with status; unknown id -> 404

Fixture layout:
  gateway_ctx (module-scoped) — starts all three containers, monkeypatches
  get_settings in gateway.main to point at the containers, enters the TestClient
  lifespan context, then yields a dict{client, pg_url, rmq_url, minio_endpoint}.
  All tests in this module share one set of running containers.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator
from typing import Any

import aio_pika
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from testcontainers.minio import MinioContainer
from testcontainers.postgres import PostgresContainer
from testcontainers.rabbitmq import RabbitMqContainer

import gateway.main as gw_main
from core.config import Settings
from core.infra import broker
from core.infra.db import Job, create_tables, get_engine, get_session
from core.infra.storage import _make_client, get_text

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def gateway_ctx() -> Generator[dict[str, Any], None, None]:
    """Start Postgres + RabbitMQ + MinIO, patch settings, run gateway lifespan."""
    with (
        PostgresContainer("postgres:17-alpine") as pg,
        RabbitMqContainer("rabbitmq:4-management") as rmq,
        MinioContainer("minio/minio:latest") as minio,
    ):
        pg_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        rmq_url = f"amqp://guest:guest@{rmq.get_container_host_ip()}:{rmq.get_exposed_port(5672)}/"
        minio_endpoint = f"{minio.get_container_host_ip()}:{minio.get_exposed_port(9000)}"

        test_settings = Settings(
            DATABASE_URL=pg_url,
            RABBITMQ_URL=rmq_url,
            MINIO_ENDPOINT=minio_endpoint,
            MINIO_ACCESS="minioadmin",
            MINIO_SECRET="minioadmin",
        )

        # Create schema before starting the gateway (lifespan doesn't run migrations).
        async def _setup_schema() -> None:
            engine = get_engine(pg_url)
            async with engine.begin() as conn:
                await create_tables(conn)
            await engine.dispose()

        asyncio.run(_setup_schema())

        mp = pytest.MonkeyPatch()
        mp.setattr(gw_main, "get_settings", lambda: test_settings)

        with TestClient(gw_main.app) as client:
            yield {
                "client": client,
                "rmq_url": rmq_url,
                "pg_url": pg_url,
                "minio_endpoint": minio_endpoint,
            }

        mp.undo()


# ── lifespan ──────────────────────────────────────────────────────────


def test_lifespan_health_check(gateway_ctx: dict[str, Any]) -> None:
    """Gateway started + exchange declared; health probe returns 200."""
    r = gateway_ctx["client"].get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ── ingestion ───────────────────────────────────────────────────────────


def test_post_jobs_returns_202_with_job_id(gateway_ctx: dict[str, Any]) -> None:
    """POST /jobs -> 202 {job_id}."""
    r = gateway_ctx["client"].post("/jobs", json={"manuscript": "Once upon a time."})
    assert r.status_code == 202
    data = r.json()
    assert "job_id" in data
    assert len(data["job_id"]) == 32  # uuid4().hex


def test_post_jobs_creates_pending_db_record(gateway_ctx: dict[str, Any]) -> None:
    """POST /jobs -> Job row in Postgres with status=PENDING."""
    r = gateway_ctx["client"].post("/jobs", json={"manuscript": "DB record test."})
    assert r.status_code == 202
    job_id: str = r.json()["job_id"]

    async def check() -> tuple[str, str]:
        engine = get_engine(gateway_ctx["pg_url"])
        try:
            async with get_session(engine) as session:
                result = await session.execute(select(Job).where(Job.job_id == job_id))
                job = result.scalar_one()
                return job.status.value, job.manuscript_key or ""
        finally:
            await engine.dispose()

    status, key = asyncio.run(check())
    assert status == "PENDING"
    assert key == f"raw/{job_id}.txt"


def test_post_jobs_stores_manuscript_in_minio(gateway_ctx: dict[str, Any]) -> None:
    """POST /jobs -> raw/<job_id>.txt in MinIO with the manuscript text."""
    manuscript = "MinIO roundtrip verification text."
    r = gateway_ctx["client"].post("/jobs", json={"manuscript": manuscript})
    assert r.status_code == 202
    job_id: str = r.json()["job_id"]

    minio_client = _make_client(gateway_ctx["minio_endpoint"], "minioadmin", "minioadmin")

    async def fetch() -> str:
        return await get_text(minio_client, f"raw/{job_id}.txt")

    text = asyncio.run(fetch())
    assert text == manuscript


def test_post_jobs_publishes_job_created_to_parse_queue(
    gateway_ctx: dict[str, Any],
) -> None:
    """POST /jobs -> JobCreated message lands on q.parse with matching job_id."""
    r = gateway_ctx["client"].post("/jobs", json={"manuscript": "Broker publish test."})
    assert r.status_code == 202
    job_id: str = r.json()["job_id"]

    async def find_on_parse() -> dict[str, object] | None:
        """Drain q.parse (acking all) until we find the message for this job_id."""
        conn = await aio_pika.connect_robust(gateway_ctx["rmq_url"])
        channel = await conn.channel()
        q = await channel.declare_queue(broker.Q_PARSE, passive=True)
        try:
            for _ in range(30):
                msg = await q.get(timeout=3, fail=False)
                if msg is None:
                    return None
                body: dict[str, object] = json.loads(msg.body)
                await msg.ack()
                if body.get("job_id") == job_id:
                    return body
            return None
        finally:
            await conn.close()

    found = asyncio.run(find_on_parse())
    assert found is not None, f"JobCreated for {job_id} not found on q.parse"
    assert found["job_id"] == job_id
    assert "event_id" in found  # pointers-only: no bytes in the message


# ── status endpoint ───────────────────────────────────────────────────


def test_get_status_returns_pending_for_new_job(gateway_ctx: dict[str, Any]) -> None:
    """GET /status/{job_id} -> 200 + PENDING immediately after creation."""
    r = gateway_ctx["client"].post("/jobs", json={"manuscript": "Status check test."})
    job_id: str = r.json()["job_id"]

    r2 = gateway_ctx["client"].get(f"/status/{job_id}")
    assert r2.status_code == 200
    data = r2.json()
    assert data["job_id"] == job_id
    assert data["status"] == "PENDING"
    assert data["manuscript_key"] == f"raw/{job_id}.txt"


def test_get_status_unknown_job_returns_404(gateway_ctx: dict[str, Any]) -> None:
    """GET /status/{unknown} -> 404."""
    r = gateway_ctx["client"].get("/status/this-job-does-not-exist-at-all-00000")
    assert r.status_code == 404


# ── manuscript max-size guard ───────────────────────────────────────────


def test_post_jobs_oversized_manuscript_returns_413(gateway_ctx: dict[str, Any]) -> None:
    """A request body over MAX_MANUSCRIPT_BYTES (default 1 MB) -> clean 413 JSON."""
    oversized = "a" * (1_000_001)  # body Content-Length exceeds the 1 MB cap
    r = gateway_ctx["client"].post("/jobs", json={"manuscript": oversized})
    assert r.status_code == 413
    body = r.json()
    assert body["error"] == "manuscript_too_large"
    assert body["max_bytes"] == 1_000_000


def test_post_jobs_normal_manuscript_still_accepted(gateway_ctx: dict[str, Any]) -> None:
    """A normal-sized manuscript is unaffected by the guard -> 202."""
    r = gateway_ctx["client"].post("/jobs", json={"manuscript": "Within the size cap."})
    assert r.status_code == 202
    assert "job_id" in r.json()
