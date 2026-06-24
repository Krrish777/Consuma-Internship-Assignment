"""Worker entrypoint — aio-pika consume loop (spec §5, §8).

Rung 0 boot: connect to the broker (connect_robust) and idle, so the service is
runnable under compose and proves broker connectivity. The consume loop with manual
ack-LAST, prefetch, and NACK->retry routing to handlers/{parse,tts,stitch} arrives in
later rungs.

Run by compose as: python -m worker.main
"""

from __future__ import annotations

import asyncio
import logging

import aio_pika

from core.config import get_settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker")


async def main() -> None:
    settings = get_settings()
    log.info("worker booting; connecting to broker %s", settings.RABBITMQ_URL)
    connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
    log.info("worker connected; idle (no handlers wired yet — Rung 0 boot)")
    try:
        await asyncio.Future()  # idle until the container is stopped
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
