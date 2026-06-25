"""R1.3 — MinIO storage adapter integration test (MinIO via testcontainers).

Proves:
  - ensure_bucket is idempotent (safe to call twice)
  - put_text / get_text round-trip
  - put_bytes / get_bytes round-trip
  - list_prefix returns the expected keys
  - key_exists works correctly
"""

from __future__ import annotations

import pytest
from testcontainers.minio import MinioContainer

from core.infra import storage
from core.infra.storage import _make_client

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def minio_client():  # type: ignore[no-untyped-def]
    with MinioContainer("minio/minio:latest") as minio:
        endpoint = f"{minio.get_container_host_ip()}:{minio.get_exposed_port(9000)}"
        yield _make_client(endpoint, "minioadmin", "minioadmin")


async def test_ensure_bucket_idempotent(minio_client) -> None:  # type: ignore[no-untyped-def]
    await storage.ensure_bucket(minio_client)
    await storage.ensure_bucket(minio_client)  # second call must not raise


async def test_put_get_text(minio_client) -> None:  # type: ignore[no-untyped-def]
    await storage.ensure_bucket(minio_client)
    await storage.put_text(minio_client, "raw/test-job.txt", "hello world")
    result = await storage.get_text(minio_client, "raw/test-job.txt")
    assert result == "hello world"


async def test_put_get_bytes(minio_client) -> None:  # type: ignore[no-untyped-def]
    await storage.ensure_bucket(minio_client)
    data = b"\x00\x01\x02audio-bytes"
    await storage.put_bytes(minio_client, "tts/abc123.wav", data)
    result = await storage.get_bytes(minio_client, "tts/abc123.wav")
    assert result == data


async def test_list_prefix(minio_client) -> None:  # type: ignore[no-untyped-def]
    await storage.ensure_bucket(minio_client)
    await storage.put_text(minio_client, "tts/job-list/block-0.wav", "audio0")
    await storage.put_text(minio_client, "tts/job-list/block-1.wav", "audio1")
    keys = await storage.list_prefix(minio_client, "tts/job-list/")
    assert "tts/job-list/block-0.wav" in keys
    assert "tts/job-list/block-1.wav" in keys


async def test_key_exists(minio_client) -> None:  # type: ignore[no-untyped-def]
    await storage.ensure_bucket(minio_client)
    await storage.put_text(minio_client, "raw/exists-test.txt", "data")
    assert storage.key_exists(minio_client, "raw/exists-test.txt")
    assert not storage.key_exists(minio_client, "raw/does-not-exist.txt")
