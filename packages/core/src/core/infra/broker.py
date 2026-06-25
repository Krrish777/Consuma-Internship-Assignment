"""aio-pika adapter — connection + topology + publish/consume.

Molded from the kieled FastAPI+aio-pika skeleton, bent to our MUST rules (CLAUDE.md):
  - durable NAMED exchange + durable queues (skeleton: default exchange, auto_delete)
  - publisher confirms ON (skeleton: confirms off)
  - PERSISTENT messages carrying a pydantic event = pointers, never bytes
  - MANUAL ack: consume registers with no_ack=False; the handler acks LAST, after its
    Postgres commit + downstream publish. This helper deliberately does NOT auto-ack
    (no `async with message.process()`), because ack-before-publish loses events on crash.

Retry ladder:
  - One delay queue PER delay value (q.retry.<stage>.<delay>s) to avoid head-of-line
    blocking from mixed-TTL messages on a single queue.
  - Each delay queue has `x-message-ttl` = delay_ms and dead-letters back to pipeline
    exchange with routing key = original queue. So expired messages return to the live
    queue for another attempt.
  - Retry count is tracked via a custom header `x-retry-count` stamped at publish time.
    NEVER use `x-death.count` — on RabbitMQ ≥3.13 (and 4.x) it is frozen at 1 per
    queue and breaks retry gating.
  - After MAX_RETRIES, publish directly to q.dlq.

Raw aio-pika only — no Celery/Taskiq/ARQ/RQ (CLAUDE.md rule #2).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import aio_pika
from aio_pika.abc import (
    AbstractChannel,
    AbstractExchange,
    AbstractIncomingMessage,
    AbstractQueue,
    AbstractRobustConnection,
)
from pydantic import BaseModel

EXCHANGE = "pipeline"

Q_PARSE = "q.parse"
Q_TTS = "q.tts"
Q_STITCH = "q.stitch"
Q_DLQ = "q.dlq"

RETRY_DELAYS = (1, 4, 16)

_HEADER_RETRY_COUNT = "x-retry-count"

Handler = Callable[[AbstractIncomingMessage], Awaitable[None]]


async def connect(url: str) -> AbstractRobustConnection:
    """Open a self-healing connection (connect_robust auto-reconnects on drop)."""
    return await aio_pika.connect_robust(url)


async def declare_minimal(
    channel: AbstractChannel,
) -> tuple[AbstractExchange, AbstractQueue]:
    """Declare the durable minimal topology: ``pipeline`` exchange + ``q.parse`` bound to it.

    Idempotent — declaring an already-existing durable entity is a no-op, so every worker
    may call this on boot. Returns (exchange, queue) for publish/consume wiring.
    """
    exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True)
    queue = await channel.declare_queue(Q_PARSE, durable=True)
    await queue.bind(exchange, routing_key=Q_PARSE)
    return exchange, queue


async def declare_full(
    channel: AbstractChannel,
    *,
    retry_delays: tuple[int, ...] = RETRY_DELAYS,
) -> AbstractExchange:
    """Declare the full topology.

    Topology layout:
      pipeline (direct exchange)
        → q.parse, q.tts, q.stitch    (live queues)
        → q.dlq                        (poison-pill sink)
        → q.retry.<queue>.<delay>s     (one per delay per live queue, uniform TTL)
                                        expires → back to pipeline/q.<stage>

    Separate delay queue per delay value → no head-of-line blocking.
    Retry count in custom header `x-retry-count`, not `x-death.count`.
    """
    exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True)

    dlq = await channel.declare_queue(Q_DLQ, durable=True)
    await dlq.bind(exchange, routing_key=Q_DLQ)

    for live_queue in (Q_PARSE, Q_TTS, Q_STITCH):
        q = await channel.declare_queue(live_queue, durable=True)
        await q.bind(exchange, routing_key=live_queue)

        for delay_s in retry_delays:
            retry_q_name = f"q.retry.{live_queue}.{delay_s}s"
            retry_q = await channel.declare_queue(
                retry_q_name,
                durable=True,
                arguments={
                    "x-message-ttl": delay_s * 1000,
                    "x-dead-letter-exchange": EXCHANGE,
                    "x-dead-letter-routing-key": live_queue,
                },
            )
            await retry_q.bind(exchange, routing_key=retry_q_name)

    return exchange


async def publish(exchange: AbstractExchange, event: BaseModel, routing_key: str) -> None:
    """Publish a pydantic event as a PERSISTENT JSON message (pointers, never bytes).

    ``event`` is a ``core.domain.events`` model; only its string keys travel — the bytes
    live in MinIO and are fetched by key downstream.
    """
    body = event.model_dump_json().encode()
    message = aio_pika.Message(
        body=body,
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )
    await exchange.publish(message, routing_key=routing_key)


async def publish_with_retry_count(
    exchange: AbstractExchange,
    body: bytes,
    routing_key: str,
    retry_count: int,
    content_type: str = "application/json",
) -> None:
    """Publish a raw message with an explicit retry-count header.

    Used by the retry path to stamp `x-retry-count` before re-routing.
    Callers pass the raw body (already serialised) to avoid a double-encode.
    """
    message = aio_pika.Message(
        body=body,
        content_type=content_type,
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        headers={_HEADER_RETRY_COUNT: retry_count},
    )
    await exchange.publish(message, routing_key=routing_key)


def get_retry_count(message: AbstractIncomingMessage) -> int:
    """Read `x-retry-count` header from an incoming message (0 if absent)."""
    headers = message.headers or {}
    val = headers.get(_HEADER_RETRY_COUNT, 0)
    return int(val) if isinstance(val, (int, float, str)) else 0


async def route_retry_or_dlq(
    exchange: AbstractExchange,
    message: AbstractIncomingMessage,
    *,
    live_queue: str,
    max_retries: int = 3,
    retry_delays: tuple[int, ...] = RETRY_DELAYS,
) -> None:
    """Route a failing message: next delay queue, or DLQ after max_retries.

    Call this from exception handlers BEFORE acking the original message.
    The caller still owns the ack/nack after this returns.

    Uses x-retry-count custom header, not x-death.count.
    One delay queue per delay — no HOL blocking.
    """
    count = get_retry_count(message) + 1
    if count > max_retries:
        await exchange.publish(
            aio_pika.Message(
                body=message.body,
                content_type=message.content_type or "application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                headers={_HEADER_RETRY_COUNT: count},
            ),
            routing_key=Q_DLQ,
        )
    else:
        delay_s = retry_delays[min(count - 1, len(retry_delays) - 1)]
        retry_q_name = f"q.retry.{live_queue}.{delay_s}s"
        await exchange.publish(
            aio_pika.Message(
                body=message.body,
                content_type=message.content_type or "application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                headers={_HEADER_RETRY_COUNT: count},
            ),
            routing_key=retry_q_name,
        )


async def consume(
    channel: AbstractChannel,
    queue: AbstractQueue,
    handler: Handler,
    *,
    prefetch: int,
) -> None:
    """Register ``handler`` for manual-ack consumption.

    ``prefetch`` caps unacked messages per worker (fair dispatch + bounded memory).
    ``no_ack=False`` means the broker holds each message until the handler explicitly
    acks — so a crash mid-handler redelivers it (crash recovery). The handler owns the
    ack and MUST call it LAST: work → COMMIT → PUBLISH → ack.
    """
    await channel.set_qos(prefetch_count=prefetch)
    await queue.consume(handler, no_ack=False)
