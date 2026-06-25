"""Harness smoke — the e2e harness proving itself.

A trivial job submitted to the *live* compose stack must travel
gateway → broker → worker (parse → tts → stitch) and reach COMPLETED. If this
passes, the shared fixtures (`stack` health, `client`, `wait_for_status`) work
and every downstream probe can build on them.

Tagged ``e2e`` so the no-Docker ``make check`` skips it; runs under ``make e2e``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest

pytestmark = pytest.mark.e2e


async def test_harness_smoke(
    client: httpx.AsyncClient,
    wait_for_status: Callable[..., Awaitable[str]],
) -> None:
    resp = await client.post(
        "/jobs",
        json={"manuscript": "Hello world.\n\nA second paragraph for two blocks."},
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    status = await wait_for_status(job_id, target="COMPLETED", timeout=90.0)
    assert status == "COMPLETED", f"job {job_id} ended in {status}, expected COMPLETED"
