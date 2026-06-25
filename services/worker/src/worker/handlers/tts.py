"""TTS handler (Stage C) — cache → slot → generate → fan-in.

For each ``TtsRequested`` (pointers: job_id + task_id):

  1. Reconstruct the block text (the message carries no bytes): load the task's
     ``block_index`` + the job's manuscript, ``split_blocks``, index. The content
     hash of that text equals the stored ``block_hash`` and is the cache key AND
     the ``tts/<hash>.wav`` object key.
  2. **Check the content cache BEFORE acquiring a semaphore slot**: a hit burns no
     token — go straight to the fan-in decrement.
  3. On a miss, take the in-flight lock *without* a slot (a waiter holding a slot
     would starve the synthesiser it waits on), then acquire a leased Redis slot
     (never ``asyncio.Semaphore``), synthesise, store to MinIO, populate the
     cache, release. Losers of the in-flight race wait for the winner's cache entry.
  4. **Atomic fan-in decrement** — a durable conditional claim + atomic
     ``UPDATE … RETURNING``. Exactly one caller observes ``pending_count == 0`` and
     emits ``StitchReady``.
  5. If the claim no-ops on a redelivery (task already DONE → ``None``),
     re-read ``pending_count``; if it is 0 the barrier was crossed but the
     ``StitchReady`` may have been lost to a crash before publish, so re-emit it
     (stitch is idempotent).
"""

from __future__ import annotations

import asyncio

from aio_pika.abc import AbstractIncomingMessage

from core.domain.events import StitchReady, TtsRequested
from core.domain.hash import content_hash
from core.domain.text import split_blocks
from core.domain.vendor import tts_fake_audio
from core.infra import broker, storage
from core.infra.broker import Handler, Q_STITCH, Q_TTS
from core.infra.db import Job, Task, get_session
from core.infra.logging import bind_job_id, bind_task_id, get_logger
from core.infra.queries import complete_task_and_decrement
from worker.bootstrap import WorkerContext
from worker.errors import TransientError
from worker.handlers._base import ack_last

log = get_logger("worker.tts")


async def _load_block_text(ctx: WorkerContext, job_id: str, task_id: str) -> str:
    """Reconstruct the block's text from the manuscript via the task's block_index."""
    async with get_session(ctx.engine) as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise RuntimeError(f"tts: task {task_id!r} has no row")
        block_index = task.block_index
        job = await session.get(Job, job_id)
        if job is None:
            raise RuntimeError(f"tts: job {job_id!r} has no row")
        manuscript_key = job.manuscript_key

    text = await storage.get_text(ctx.minio, manuscript_key)
    blocks = split_blocks(text)
    if block_index >= len(blocks):
        # Manuscript changed under us — deterministically unprocessable for this task.
        raise RuntimeError(f"tts: block_index {block_index} out of range for {job_id!r}")
    return blocks[block_index]


async def _synthesize(
    ctx: WorkerContext, h: str, audio_key: str, block_text: str, owner: str
) -> str:
    """Cache-miss path: in-flight lock (no slot) → leased slot → synth → store → cache."""
    if await ctx.cache.acquire_inflight(h, owner=owner):
        try:
            async with ctx.semaphore.slot(owner=owner):
                await asyncio.sleep(0)  # stand-in for vendor latency
                audio = tts_fake_audio(block_text)
                await storage.put_bytes(ctx.minio, audio_key, audio, content_type="audio/wav")
            await ctx.cache.cache_set(h, audio_key)
        finally:
            await ctx.cache.release_inflight(h)
        return audio_key

    # Lost the in-flight race: wait (without a slot) for the winner to populate.
    waited = await ctx.cache.wait_for_cache(h)
    if waited is None:
        raise TransientError("tts: in-flight winner did not populate cache; retry")
    return waited


async def handle_tts(ctx: WorkerContext, event: TtsRequested) -> None:
    """Cache → slot → synth → atomic fan-in → emit StitchReady when barrier hits 0."""
    job_id, task_id = event.job_id, event.task_id

    block_text = await _load_block_text(ctx, job_id, task_id)
    h = content_hash(block_text)
    audio_key = f"tts/{h}.wav"

    # Cache check BEFORE acquiring a slot — a hit burns no token.
    cached = await ctx.cache.cache_get(h)
    audio_key = (
        cached
        if cached is not None
        else await _synthesize(ctx, h, audio_key, block_text, owner=task_id)
    )

    # Atomic, idempotent fan-in: durable claim + UPDATE … RETURNING.
    async with get_session(ctx.engine) as session:
        remaining = await complete_task_and_decrement(session, job_id, task_id, audio_key)

    if remaining == 0:
        await broker.publish(ctx.exchange, StitchReady(job_id=job_id), routing_key=Q_STITCH)
        log.info("job %s: barrier reached 0 → StitchReady emitted", job_id)
    elif remaining is None:
        # duplicate delivery. The barrier may already be 0 with the
        # StitchReady lost to a crash; re-read and re-emit if so (stitch is idempotent).
        async with get_session(ctx.engine) as session:
            job = await session.get(Job, job_id)
        if job is not None and job.pending_count == 0:
            await broker.publish(ctx.exchange, StitchReady(job_id=job_id), routing_key=Q_STITCH)
            log.info("job %s: H-EMIT re-emitted StitchReady on redelivery", job_id)


def make_tts_handler(ctx: WorkerContext) -> Handler:
    """Build the TTS consumer: validate the event (pointers only), then handle it."""

    async def do_work(message: AbstractIncomingMessage) -> None:
        event = TtsRequested.model_validate_json(message.body)
        bind_job_id(event.job_id)
        bind_task_id(event.task_id)
        await handle_tts(ctx, event)

    return ack_last(ctx, Q_TTS, do_work)
