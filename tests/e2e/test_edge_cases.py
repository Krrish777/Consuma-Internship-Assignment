"""Edge-case battery: the corners the spec calls out must converge.

Covered here (deterministic, hermetic):
  1. 0-block manuscript     → terminates (fan-in barrier of 0 must not hang).
  2. 1-block manuscript     → completes with a single chunk.
  3. all-cache-hit job      → a second job over already-cached blocks completes and
                              reuses the same content-addressed assets (cache served).
  4. dependency bounce      → restart MinIO around a job; the retry ladder rides out
                              the outage (MinIO is persistent) and the job converges.
  5. Redis wipe             → FLUSHALL Redis, then a NEW job still reaches COMPLETED:
                              the worker's periodic re-seeder (run_reseeder) rebuilds
                              the wiped TTS pool, so acquire() no longer BLPOPs forever.

Covered elsewhere (honest split, to avoid duplicate/over-destructive probes):
  * parse-crash-after-writing-some-rows → no duplicate tasks: proven by the
    duplicate-JobCreated probe (parse ON CONFLICT DO NOTHING) + test_parse_redelivery.

History: the Redis-wipe case was previously NOT exercised — ``ensure_slots`` ran only
on worker boot, so a wipe stranded the TTS semaphore (BLPOP on an empty pool) until a
worker reboot. The re-seeder (run_reseeder) closed that gap, which is what case 5 now proves.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from core.infra.db import Task, get_session

from .helpers import MINIO, flush_redis, redis_llen, restart_container

pytestmark = pytest.mark.e2e


async def _tasks(engine: AsyncEngine, job_id: str) -> list[Task]:
    async with get_session(engine) as session:
        result = await session.execute(
            select(Task).where(Task.job_id == job_id).order_by(Task.block_index)
        )
        return list(result.scalars().all())


async def test_zero_block_manuscript_terminates(
    client: httpx.AsyncClient,
    wait_for_status: Callable[..., Awaitable[str]],
    db_engine: AsyncEngine,
) -> None:
    # An empty manuscript splits to 0 blocks: the fan-in barrier of 0 must resolve
    # straight through, not hang waiting for a TTS that never comes.
    resp = await client.post("/jobs", json={"manuscript": "   \n\n  "})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    status = await wait_for_status(job_id, target="COMPLETED", timeout=90.0)
    assert status == "COMPLETED", f"0-block job ended {status}, expected COMPLETED (hang?)"
    assert await _tasks(db_engine, job_id) == [], "0-block job created task rows"
    final = (await client.get(f"/status/{job_id}")).json()
    assert final["pending_count"] == 0


async def test_one_block_manuscript_completes(
    client: httpx.AsyncClient,
    wait_for_status: Callable[..., Awaitable[str]],
    db_engine: AsyncEngine,
) -> None:
    resp = await client.post("/jobs", json={"manuscript": "A solitary scene, alone."})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    assert await wait_for_status(job_id, target="COMPLETED", timeout=90.0) == "COMPLETED"
    tasks = await _tasks(db_engine, job_id)
    assert len(tasks) == 1 and tasks[0].status == "DONE", (
        "1-block job did not produce one DONE task"
    )
    assert (await client.get(f"/status/{job_id}")).json()["final_key"], "no asset for 1-block job"


async def test_all_cache_hit_job_completes_reusing_assets(
    client: httpx.AsyncClient,
    wait_for_status: Callable[..., Awaitable[str]],
    db_engine: AsyncEngine,
) -> None:
    blocks = ["Cached line one.", "Cached line two."]
    manuscript = "\n\n".join(blocks)

    # Warm the cache: first job synthesizes + caches both blocks.
    r1 = await client.post("/jobs", json={"manuscript": manuscript})
    job1 = r1.json()["job_id"]
    assert await wait_for_status(job1, target="COMPLETED", timeout=90.0) == "COMPLETED"
    keys1 = {t.block_index: t.audio_key for t in await _tasks(db_engine, job1)}

    # Second identical job: every block is a cache hit. It must still COMPLETE and
    # resolve to the SAME content-addressed assets (the cache served them).
    r2 = await client.post("/jobs", json={"manuscript": manuscript})
    job2 = r2.json()["job_id"]
    assert await wait_for_status(job2, target="COMPLETED", timeout=90.0) == "COMPLETED"
    tasks2 = await _tasks(db_engine, job2)
    assert all(t.status == "DONE" for t in tasks2), "all-cache-hit job left a task undone"
    keys2 = {t.block_index: t.audio_key for t in tasks2}
    assert keys2 == keys1, "all-cache-hit job did not reuse the cached content-addressed assets"


async def test_dependency_bounce_minio_job_converges(
    client: httpx.AsyncClient,
    wait_for_status: Callable[..., Awaitable[str]],
) -> None:
    # Submit a multi-block job, then bounce MinIO around it. MinIO is persistent
    # (volume), so any storage op that fails during the restart is retried by the
    # ladder and succeeds once it's back — the job must converge to COMPLETED, never
    # corrupt or hang.
    manuscript = "\n\n".join(f"Resilient scene {i}." for i in range(4))
    resp = await client.post("/jobs", json={"manuscript": manuscript})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    restart_container(MINIO)

    status = await wait_for_status(job_id, target="COMPLETED", timeout=180.0)
    assert status == "COMPLETED", f"job ended {status} after a MinIO bounce, expected COMPLETED"
    assert (await client.get(f"/status/{job_id}")).json()["final_key"], (
        "no asset after MinIO bounce"
    )


async def test_redis_wipe_job_still_completes(
    client: httpx.AsyncClient,
    wait_for_status: Callable[..., Awaitable[str]],
) -> None:
    # Redis is "safe to lose" — a wipe must self-heal. Warm the stack, FLUSHALL
    # Redis (drops tts:slots + its init marker, as an eviction/restart would), confirm
    # the pool is genuinely empty, then submit a NEW multi-block job. Previously this
    # would hang forever (acquire() BLPOPs an empty pool that boot-only ensure_slots
    # never refills); with the periodic run_reseeder it re-seeds and the job converges.
    warm = await client.post("/jobs", json={"manuscript": "Warm up the pool."})
    assert warm.status_code == 202, warm.text
    assert await wait_for_status(warm.json()["job_id"], target="COMPLETED", timeout=90.0) == (
        "COMPLETED"
    )

    flush_redis()
    assert redis_llen("tts:slots") == 0, "FLUSHALL did not empty the slots pool — vacuous probe"

    manuscript = "\n\n".join(f"Recovered scene {i}." for i in range(4))
    resp = await client.post("/jobs", json={"manuscript": manuscript})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    # Generous timeout: the re-seed fires on the worker's RESEED_INTERVAL_S cadence
    # (default 30s), so the post-wipe job may wait up to one interval for slots.
    status = await wait_for_status(job_id, target="COMPLETED", timeout=150.0)
    assert status == "COMPLETED", (
        f"job ended {status} after a Redis wipe, expected COMPLETED — re-seeder did not refill the pool"
    )
    assert (await client.get(f"/status/{job_id}")).json()["final_key"], "no asset after Redis wipe"
