"""R0.2 / toward R2.1 — broker adapter round-trip against a real RabbitMQ.

Marked ``integration``: conftest auto-skips it when no Docker daemon is present.
Proves the molded skeleton (spec §5, §7):
  - connect_robust + durable declare_minimal topology
  - publish carries a pydantic event as JSON (pointers, never bytes)
  - consume uses MANUAL ack (no_ack=False); an un-acked message is REDELIVERED
    (the foundation of crash recovery — CLAUDE.md ack-LAST rule).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import aio_pika
import pytest
from testcontainers.rabbitmq import RabbitMqContainer

from core.domain.events import JobCreated
from core.infra import broker

pytestmark = pytest.mark.integration


@pytest.fixture
async def amqp_url() -> AsyncIterator[str]:
    with RabbitMqContainer("rabbitmq:4-management") as rabbit:
        host = rabbit.get_container_host_ip()
        port = rabbit.get_exposed_port(5672)
        yield f"amqp://guest:guest@{host}:{port}/"


async def test_publish_consume_roundtrip(amqp_url: str) -> None:
    connection = await broker.connect(amqp_url)
    received: list[JobCreated] = []
    done = asyncio.Event()

    async def handler(message: aio_pika.abc.AbstractIncomingMessage) -> None:
        received.append(JobCreated.model_validate_json(message.body))
        await message.ack()  # ack LAST (here, after "work")
        done.set()

    async with connection:
        channel = await connection.channel()
        exchange, queue = await broker.declare_minimal(channel)
        await broker.consume(channel, queue, handler, prefetch=16)

        sent = JobCreated(job_id="job-rt")
        await broker.publish(exchange, sent, routing_key=broker.Q_PARSE)

        await asyncio.wait_for(done.wait(), timeout=10)

    assert len(received) == 1
    assert received[0] == sent


async def test_unacked_message_is_redelivered(amqp_url: str) -> None:
    """Manual-ack proof: reject without requeue-loss → broker redelivers."""
    connection = await broker.connect(amqp_url)
    deliveries: list[bool] = []  # message.redelivered flag per delivery
    done = asyncio.Event()

    async def handler(message: aio_pika.abc.AbstractIncomingMessage) -> None:
        deliveries.append(bool(message.redelivered))
        if message.redelivered:
            await message.ack()  # accept the second time
            done.set()
        else:
            await message.nack(requeue=True)  # first time: drop it back, unacked

    async with connection:
        channel = await connection.channel()
        exchange, queue = await broker.declare_minimal(channel)
        await broker.consume(channel, queue, handler, prefetch=16)

        await broker.publish(
            exchange, JobCreated(job_id="job-redeliver"), routing_key=broker.Q_PARSE
        )
        await asyncio.wait_for(done.wait(), timeout=10)

    assert deliveries[0] is False  # first delivery: fresh
    assert True in deliveries  # was redelivered after the nack
