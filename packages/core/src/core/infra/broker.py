"""aio-pika adapter — connection + topology + publish/consume (spec §5, §7).

Molded from the kieled FastAPI+aio-pika skeleton, bent to our MUST rules (CLAUDE.md):
  - durable NAMED exchange + durable queue   (skeleton: default exchange, auto_delete)
  - publisher confirms ON                    (skeleton: confirms off)
  - PERSISTENT messages carrying a pydantic event = pointers, never bytes (spec §7)
  - MANUAL ack: consume registers with no_ack=False; the handler acks LAST, after its
    Postgres commit + downstream publish. This helper deliberately does NOT auto-ack
    (no `async with message.process()`), because ack-before-publish loses events on crash.

Raw aio-pika only — no Celery/Taskiq/ARQ/RQ (CLAUDE.md rule #2).

Scope note: ``declare_minimal`` is the Rung-0/R0.2 topology (exchange + q.parse). The full
retry-ladder topology (q.tts/q.stitch + 1s/4s/16s delay queues + q.dlq) is R2.1, molded
later from ``retry-dlx-aiopika`` — this leaves a clean seam, not a half-stubbed ladder.
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

Handler = Callable[[AbstractIncomingMessage], Awaitable[None]]


async def connect(url: str) -> AbstractRobustConnection:
    """Open a self-healing connection (connect_robust auto-reconnects on drop)."""
    return await aio_pika.connect_robust(url)


async def declare_minimal(
    channel: AbstractChannel,
) -> tuple[AbstractExchange, AbstractQueue]:
    """Declare the durable Rung-0 topology: ``pipeline`` exchange + ``q.parse`` bound to it.

    Idempotent — declaring an already-existing durable entity is a no-op, so every worker
    may call this on boot. Returns (exchange, queue) for publish/consume wiring.
    """
    exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True)
    queue = await channel.declare_queue(Q_PARSE, durable=True)
    await queue.bind(exchange, routing_key=Q_PARSE)
    return exchange, queue


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
