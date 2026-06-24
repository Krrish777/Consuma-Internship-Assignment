"""Worker entrypoint — aio-pika consume loop (spec §5, §8).

Rung 0 boot: connect via the shared ``core.infra.broker`` adapter and declare the minimal
durable topology, so the service is runnable under compose and proves broker connectivity.
The consume loop with manual ack-LAST routing to handlers/{parse,tts,stitch} arrives in
later rungs (the adapter's ``consume`` helper is ready for them).

Run by compose as: python -m worker.main
"""

from __future__ import annotations

import asyncio
import logging

from core.config import get_settings
from core.infra import broker

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker")


async def main() -> None:
    settings = get_settings()
    log.info("worker booting; connecting to broker %s", settings.RABBITMQ_URL)
    connection = await broker.connect(settings.RABBITMQ_URL)
    try:
        channel = await connection.channel()
        await broker.declare_minimal(channel)
        log.info("worker connected; idle (no handlers wired yet — Rung 0 boot)")
        await asyncio.Future()  # idle until the container is stopped
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
