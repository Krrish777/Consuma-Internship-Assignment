"""R4.3 — stitch + webhook probe (L4): final asset produced; webhook never fails the job.

Two properties:
  * W5 stitch — the happy path concatenates the block chunks into ``out/<job>.mp3``
    and the job reaches COMPLETED;
  * W5b / MUST #8 — a webhook is best-effort: a job with a ``callback_url`` set still
    COMPLETEs with its asset regardless of the notification outcome.

Scope note (honest L3/L4 split): actual webhook *delivery* ("received once") and the
attempted-then-swallowed failure are L3-proven (test_webhook, 3 tests, with the SSRF
resolver mocked). They are not re-exercised here because the H-SSRF guard correctly
rejects every private/loopback IP — and in a hermetic compose stack every reachable
sink (another container, host.docker.internal) is private — so real delivery would
require an external public endpoint, out of scope for a hermetic e2e. With the
default empty WEBHOOK_ALLOWLIST the worker is in log-only mode; either way the job's
completion must not depend on the webhook, which is exactly what we assert.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest

pytestmark = pytest.mark.e2e


async def test_stitch_happy_path_produces_completed_asset(
    client: httpx.AsyncClient,
    wait_for_status: Callable[..., Awaitable[str]],
) -> None:
    manuscript = "\n\n".join(f"Scene {i} of the finished drama." for i in range(3))
    resp = await client.post("/jobs", json={"manuscript": manuscript})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    status = await wait_for_status(job_id, target="COMPLETED", timeout=120.0)
    assert status == "COMPLETED", f"job {job_id} ended {status}, expected COMPLETED"

    final = (await client.get(f"/status/{job_id}")).json()
    final_key = final["final_key"]
    assert final_key, "stitch produced no final asset"
    assert final_key.startswith("out/") and final_key.endswith(".mp3"), (
        f"unexpected final asset key {final_key!r}"
    )


async def test_webhook_failure_still_completed(
    client: httpx.AsyncClient,
    wait_for_status: Callable[..., Awaitable[str]],
) -> None:
    # A callback is set, but it is never deliverable (log-only by default, or SSRF-
    # blocked). MUST #8: the job is COMPLETED with its asset regardless — the
    # notification is best-effort and its failure must not ride the job into the DLQ.
    resp = await client.post(
        "/jobs",
        json={
            "manuscript": "A drama with a doomed callback.\n\nSecond and final scene.",
            "callback_url": "http://sink.invalid/hook",
        },
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    status = await wait_for_status(job_id, target="COMPLETED", timeout=120.0)
    assert status == "COMPLETED", (
        f"job {job_id} ended {status} — webhook config must not fail the job"
    )

    final = (await client.get(f"/status/{job_id}")).json()
    assert final["final_key"], "job completed without an asset despite the callback being set"
