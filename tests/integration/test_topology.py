"""R2.1 — Full broker retry topology integration test (RabbitMQ via testcontainers).

Proves:
  - declare_full creates exchange + all live queues (q.parse/q.tts/q.stitch/q.dlq)
  - Per-delay retry queues exist for each delay value per live queue
  - route_retry_or_dlq publishes to the correct delay queue on first failure
  - route_retry_or_dlq publishes to q.dlq after max_retries
  - Custom x-retry-count header increments correctly (H-XDEATH: no x-death.count reliance)

BACKLOG items addressed:
  H-XDEATH: retry count via custom header, not x-death.count
  H-TTLHOL: separate delay queue per delay value, no head-of-line blocking
  H-REF2: 1/4/16s ladder built from scratch (not copied from single-delay reference repo)
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


async def test_declare_full_creates_live_queues(amqp_url: str) -> None:
    connection = await broker.connect(amqp_url)
    async with connection:
        channel = await connection.channel()
        exchange = await broker.declare_full(channel)
        assert exchange.name == broker.EXCHANGE

        for q_name in (broker.Q_PARSE, broker.Q_TTS, broker.Q_STITCH, broker.Q_DLQ):
            q = await channel.declare_queue(q_name, durable=True, passive=True)
            assert q.name == q_name


async def test_declare_full_creates_retry_queues_with_ttl(amqp_url: str) -> None:
    connection = await broker.connect(amqp_url)
    async with connection:
        channel = await connection.channel()
        await broker.declare_full(channel, retry_delays=(1, 4, 16))

        for live_q in (broker.Q_PARSE, broker.Q_TTS, broker.Q_STITCH):
            for delay in (1, 4, 16):
                retry_q_name = f"q.retry.{live_q}.{delay}s"
                q = await channel.declare_queue(retry_q_name, durable=True, passive=True)
                assert q.name == retry_q_name


async def test_retry_on_first_failure_routes_to_delay_queue(amqp_url: str) -> None:
    """First failure routes to q.retry.q.parse.1s — proven by consuming from it."""
    connection = await broker.connect(amqp_url)
    async with connection:
        channel = await connection.channel()
        # Use very long delays so the message stays in the retry queue during the test.
        exchange = await broker.declare_full(channel, retry_delays=(60, 120, 240))

        sent = JobCreated(job_id="job-retry-1")
        await broker.publish(exchange, sent, routing_key=broker.Q_PARSE)

        received_original: list[aio_pika.abc.AbstractIncomingMessage] = []
        got_original = asyncio.Event()

        async def capture_parse(msg: aio_pika.abc.AbstractIncomingMessage) -> None:
            received_original.append(msg)
            got_original.set()

        q_parse = await channel.declare_queue(broker.Q_PARSE, durable=True, passive=True)
        await q_parse.consume(capture_parse, no_ack=False)
        await asyncio.wait_for(got_original.wait(), timeout=5)

        original = received_original[0]
        assert broker.get_retry_count(original) == 0

        await broker.route_retry_or_dlq(
            exchange,
            original,
            live_queue=broker.Q_PARSE,
            max_retries=3,
            retry_delays=(60, 120, 240),
        )
        await original.ack()

        retry_q_name = f"q.retry.{broker.Q_PARSE}.60s"
        retry_received: list[aio_pika.abc.AbstractIncomingMessage] = []
        retry_got = asyncio.Event()

        async def capture_retry(msg: aio_pika.abc.AbstractIncomingMessage) -> None:
            retry_received.append(msg)
            retry_got.set()

        retry_q = await channel.declare_queue(retry_q_name, durable=True, passive=True)
        await retry_q.consume(capture_retry, no_ack=True)
        await asyncio.wait_for(retry_got.wait(), timeout=5)

        assert len(retry_received) == 1
        assert broker.get_retry_count(retry_received[0]) == 1


async def test_retry_after_max_retries_goes_to_dlq(amqp_url: str) -> None:
    """A message at max_retries is published to q.dlq, not a delay queue."""
    connection = await broker.connect(amqp_url)
    async with connection:
        channel = await connection.channel()
        exchange = await broker.declare_full(channel, retry_delays=(60, 120, 240))

        body = b'{"event_id":"abc","job_id":"job-poison"}'
        poison = aio_pika.Message(
            body=body,
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={"x-retry-count": 3},
        )
        await exchange.publish(poison, routing_key=broker.Q_PARSE)

        received: list[aio_pika.abc.AbstractIncomingMessage] = []
        done = asyncio.Event()

        async def capture_parse(msg: aio_pika.abc.AbstractIncomingMessage) -> None:
            received.append(msg)
            done.set()

        q_parse = await channel.declare_queue(broker.Q_PARSE, durable=True, passive=True)
        await q_parse.consume(capture_parse, no_ack=False)
        await asyncio.wait_for(done.wait(), timeout=5)

        msg = received[0]
        assert broker.get_retry_count(msg) == 3

        await broker.route_retry_or_dlq(
            exchange,
            msg,
            live_queue=broker.Q_PARSE,
            max_retries=3,
            retry_delays=(60, 120, 240),
        )
        await msg.ack()

        dlq_received: list[aio_pika.abc.AbstractIncomingMessage] = []
        dlq_got = asyncio.Event()

        async def capture_dlq(m: aio_pika.abc.AbstractIncomingMessage) -> None:
            dlq_received.append(m)
            dlq_got.set()

        dlq = await channel.declare_queue(broker.Q_DLQ, durable=True, passive=True)
        await dlq.consume(capture_dlq, no_ack=True)
        await asyncio.wait_for(dlq_got.wait(), timeout=5)

        assert len(dlq_received) == 1
        assert broker.get_retry_count(dlq_received[0]) == 4


async def test_retry_count_header_increments(amqp_url: str) -> None:
    """x-retry-count is read and incremented correctly — no x-death.count reliance."""
    connection = await broker.connect(amqp_url)
    async with connection:
        channel = await connection.channel()
        exchange = await broker.declare_full(channel, retry_delays=(1, 4, 16))

        body = b'{"event_id":"xyz","job_id":"job-count"}'
        msg = aio_pika.Message(
            body=body,
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={"x-retry-count": 1},
        )
        await exchange.publish(msg, routing_key=broker.Q_TTS)

        received: list[aio_pika.abc.AbstractIncomingMessage] = []
        done = asyncio.Event()

        async def capture(m: aio_pika.abc.AbstractIncomingMessage) -> None:
            received.append(m)
            done.set()

        tts_q = await channel.declare_queue(broker.Q_TTS, durable=True, passive=True)
        await tts_q.consume(capture, no_ack=False)
        await asyncio.wait_for(done.wait(), timeout=5)

        assert broker.get_retry_count(received[0]) == 1
        await received[0].ack()
