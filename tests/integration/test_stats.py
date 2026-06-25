"""G7 / R5.1 — GET /stats observability endpoint (L3).

Seeds jobs in mixed statuses directly into Postgres, then asserts that
``GET /stats`` returns accurate per-status counts via B6's SQL aggregate
(``job_counts_by_status``) zero-filled into a stable shape covering every
FSM state.

Fixture mirrors test_ingestion.gateway_ctx: one set of containers + the real
gateway lifespan for the whole module.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from testcontainers.minio import MinioContainer
from testcontainers.postgres import PostgresContainer
from testcontainers.rabbitmq import RabbitMqContainer

import gateway.main as gw_main
from core.config import Settings
from core.domain.state import JobStatus
from core.infra.db import Job, create_tables, get_engine, get_session

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

        async def _setup_schema() -> None:
            engine = get_engine(pg_url)
            async with engine.begin() as conn:
                await create_tables(conn)
            await engine.dispose()

        asyncio.run(_setup_schema())

        mp = pytest.MonkeyPatch()
        mp.setattr(gw_main, "get_settings", lambda: test_settings)

        with TestClient(gw_main.app) as client:
            yield {"client": client, "pg_url": pg_url}

        mp.undo()


async def _seed(pg_url: str, statuses: dict[JobStatus, int]) -> None:
    """Insert ``count`` Job rows for each given status."""
    engine = get_engine(pg_url)
    try:
        async with get_session(engine) as session:
            n = 0
            for status, count in statuses.items():
                for _ in range(count):
                    session.add(
                        Job(
                            job_id=f"stats-{status.value}-{n}",
                            status=status,
                            manuscript_key=f"raw/stats-{n}.txt",
                        )
                    )
                    n += 1
            await session.commit()
    finally:
        await engine.dispose()


def test_stats_returns_per_status_counts(gateway_ctx: dict[str, Any]) -> None:
    """Mixed-status jobs -> /stats jobs dict matches the seeded per-status counts."""
    seeded = {
        JobStatus.PENDING: 2,
        JobStatus.GENERATING: 3,
        JobStatus.COMPLETED: 1,
        JobStatus.FAILED: 4,
    }
    asyncio.run(_seed(gateway_ctx["pg_url"], seeded))

    r = gateway_ctx["client"].get("/stats")
    assert r.status_code == 200
    jobs = r.json()["jobs"]

    for status, count in seeded.items():
        assert jobs[status.value] == count


def test_stats_zero_fills_all_statuses(gateway_ctx: dict[str, Any]) -> None:
    """Every FSM state appears in the response (zero-filled), giving a stable shape."""
    r = gateway_ctx["client"].get("/stats")
    assert r.status_code == 200
    jobs = r.json()["jobs"]

    for status in JobStatus:
        assert status.value in jobs
        assert isinstance(jobs[status.value], int)


def test_stats_is_read_only(gateway_ctx: dict[str, Any]) -> None:
    """Calling /stats twice returns identical counts (no writes / side effects)."""
    first = gateway_ctx["client"].get("/stats").json()["jobs"]
    second = gateway_ctx["client"].get("/stats").json()["jobs"]
    assert first == second
