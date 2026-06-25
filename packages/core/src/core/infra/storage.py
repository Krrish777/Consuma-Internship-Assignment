"""MinIO adapter — object bytes (spec §5, §7).

The minio SDK is synchronous; all public functions here are async wrappers
that run the sync calls in the default thread-pool executor so they don't
block the event loop.

Bucket layout:
  raw/<job_id>.txt     — manuscript uploaded at ingestion (R2.2)
  tts/<hash>.wav       — per-block TTS audio, keyed by content hash (R4.2)
  out/<job_id>.mp3     — final stitched drama (R4.3)

The hash-as-key design means two identical text blocks write to the same
MinIO object — zero wasted vendor calls and zero wasted storage (R4.2 cache).

H-DANGLE invariant — object lifetime >= cache TTL:
  The Redis content cache (tts:cache:<hash>, TTL = CACHE_TTL_S) maps a block hash
  to its MinIO object key. A cache HIT skips the vendor call and reads the object
  directly, so the object MUST still exist whenever a cache entry can. If an object
  were pruned sooner than its cache entry, a HIT would return a dangling key -> a
  download 404 mid-pipeline. We therefore keep the simplest correct policy: this
  adapter installs NO bucket lifecycle rule, so tts/ objects never expire and always
  outlive any cache entry. Do NOT add an expiration lifecycle on tts/ shorter than
  CACHE_TTL_S. (Guarded by tests/integration/test_storage.py; see DOC2/DECISIONS.)
"""

from __future__ import annotations

import io
from functools import partial

import asyncio
from minio import Minio
from minio.error import S3Error

BUCKET = "audio-drama"


def _make_client(endpoint: str, access: str, secret: str) -> Minio:
    return Minio(endpoint, access_key=access, secret_key=secret, secure=False)


async def _run(fn: partial) -> object:  # type: ignore[type-arg]
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fn)


async def ensure_bucket(client: Minio) -> None:
    """Create the audio-drama bucket if it doesn't already exist."""

    def _create() -> None:
        if not client.bucket_exists(BUCKET):
            client.make_bucket(BUCKET)

    await _run(partial(_create))


async def put_text(client: Minio, key: str, text: str) -> None:
    """Upload a UTF-8 string as an object. key = e.g. 'raw/<job>.txt'."""
    data = text.encode()

    def _put() -> None:
        client.put_object(
            BUCKET,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type="text/plain; charset=utf-8",
        )

    await _run(partial(_put))


async def get_text(client: Minio, key: str) -> str:
    """Download an object and decode it as UTF-8."""

    def _get() -> bytes:
        resp = client.get_object(BUCKET, key)
        try:
            return resp.read()
        finally:
            resp.close()

    raw: bytes = await _run(partial(_get))  # type: ignore[assignment]
    return raw.decode()


async def put_bytes(
    client: Minio, key: str, data: bytes, content_type: str = "application/octet-stream"
) -> None:
    """Upload raw bytes. key = e.g. 'tts/<hash>.wav' or 'out/<job>.mp3'."""

    def _put() -> None:
        client.put_object(
            BUCKET, key, io.BytesIO(data), length=len(data), content_type=content_type
        )

    await _run(partial(_put))


async def get_bytes(client: Minio, key: str) -> bytes:
    """Download an object as raw bytes."""

    def _get() -> bytes:
        resp = client.get_object(BUCKET, key)
        try:
            return resp.read()
        finally:
            resp.close()

    result: bytes = await _run(partial(_get))  # type: ignore[assignment]
    return result


async def list_prefix(client: Minio, prefix: str) -> list[str]:
    """Return all object keys under the given prefix (e.g. 'tts/<job_id>/')."""

    def _list() -> list[str]:
        objects = client.list_objects(BUCKET, prefix=prefix, recursive=True)
        return [obj.object_name for obj in objects if obj.object_name]

    result: list[str] = await _run(partial(_list))  # type: ignore[assignment]
    return result


def key_exists(client: Minio, key: str) -> bool:
    """Synchronous existence check (cheap — stat only). Use before expensive ops."""
    try:
        client.stat_object(BUCKET, key)
        return True
    except S3Error:
        return False
