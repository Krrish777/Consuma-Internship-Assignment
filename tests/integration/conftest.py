"""Shared fixtures for the Phase-4 worker pipeline integration tests (L3).

The whole worker pipeline (parse → tts → stitch → dlq) needs the same four
backing services, and starting them is expensive. ``worker_stack`` brings up
Postgres + RabbitMQ + Redis + MinIO ONCE per session, creates the schema, and
returns a single ``Settings`` pointed at all four — every worker handler test
reuses it. Container fixtures yield only URL strings (sync, not loop-bound); the
async adapters are built per-test inside each test's own event loop via
``build_context`` (the asyncpg-loop-binding rule that ``test_models`` follows).

State hygiene: the containers persist across tests, so tests use unique job_ids
and flush Redis when they assert on ephemeral coordination state.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Generator

import pytest
from testcontainers.minio import MinioContainer
from testcontainers.postgres import PostgresContainer
from testcontainers.rabbitmq import RabbitMqContainer
from testcontainers.redis import RedisContainer

from core.config import Settings
from core.infra.db import create_tables, get_engine
from worker.bootstrap import WorkerContext, build_context, close_context


@pytest.fixture(scope="session")
def worker_stack() -> Generator[Settings, None, None]:
    """Start all four backing services and return one Settings wired to them."""
    with (
        PostgresContainer("postgres:17-alpine") as pg,
        RabbitMqContainer("rabbitmq:4-management") as rmq,
        RedisContainer("redis:7-alpine") as rds,
        MinioContainer("minio/minio:latest") as minio,
    ):
        pg_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        rmq_url = f"amqp://guest:guest@{rmq.get_container_host_ip()}:{rmq.get_exposed_port(5672)}/"
        redis_url = f"redis://{rds.get_container_host_ip()}:{rds.get_exposed_port(6379)}/0"
        minio_endpoint = f"{minio.get_container_host_ip()}:{minio.get_exposed_port(9000)}"

        settings = Settings(
            DATABASE_URL=pg_url,
            RABBITMQ_URL=rmq_url,
            REDIS_URL=redis_url,
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
        yield settings


@pytest.fixture
async def ctx(worker_stack: Settings) -> AsyncIterator[WorkerContext]:
    """A freshly-wired WorkerContext for one test, torn down cleanly afterwards."""
    context = await build_context(worker_stack)
    try:
        yield context
    finally:
        await close_context(context)
