"""R3.1 — crash-recovery probe (L4): a worker crash loses no message.

SPEC §2's central reliability claim. The worker acks LAST — do work → COMMIT
Postgres → PUBLISH next → ACK — so a SIGKILL never acks work that wasn't durably
recorded, and RabbitMQ holds/redelivers the message. The job must still converge
to COMPLETED with its full asset.

Why kill *before* submit (not mid-handler): the sim's only vendor latency is
``asyncio.sleep(0)``, so a worker processes a whole job in milliseconds — faster
than any poll-then-kill could deterministically interleave. Racing the kill would
give a flaky test that usually proves nothing. Killing first yields a
*deterministic* crash-recovery assertion: a job that arrives while the worker is
down sits durably in ``q.parse`` (not lost, not processed), and the recovered
worker runs the full pipeline to completion. The complementary in-flight
unacked-redelivery path is L3-proven by the idempotency handlers (B4 conditional
claim, W4 H-EMIT, W5 stitch H5) — see DECISIONS Phase-6 R3.1.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx
import pytest

from .helpers import WORKER, kill_container, start_container

pytestmark = pytest.mark.e2e


async def test_job_survives_worker_crash_and_completes_on_recovery(
    client: httpx.AsyncClient,
    wait_for_status: Callable[..., Awaitable[str]],
) -> None:
    # Crash the worker BEFORE the job arrives (deterministic — see module docstring).
    kill_container(WORKER)
    try:
        manuscript = "\n\n".join(f"Block {i} of the resilient broadcast." for i in range(5))
        resp = await client.post("/jobs", json={"manuscript": manuscript})
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["job_id"]

        # With no consumer, JobCreated sits durably in q.parse: the job must NOT
        # progress past PENDING. A short observation window proves it is genuinely
        # blocked on the absent worker, not merely racing toward completion — so
        # the later COMPLETED is attributable to recovery, not to a missed kill.
        for _ in range(5):
            await asyncio.sleep(1.0)
            status = (await client.get(f"/status/{job_id}")).json()["status"]
            assert status == "PENDING", f"job advanced to {status} with no worker running"
    finally:
        start_container(WORKER)

    # Recovery: the worker reconnects, consumes the durably-queued JobCreated, and
    # runs parse → tts → stitch to COMPLETED. A lost message would strand the job
    # in PENDING/GENERATING forever, so reaching COMPLETED IS the no-loss proof.
    status = await wait_for_status(job_id, target="COMPLETED", timeout=300.0)
    assert status == "COMPLETED", f"job {job_id} ended {status}, expected COMPLETED after recovery"

    final = (await client.get(f"/status/{job_id}")).json()
    assert final["final_key"], "no final asset produced after crash recovery"
    assert final["pending_count"] == 0, "fan-in barrier never resolved after recovery"
