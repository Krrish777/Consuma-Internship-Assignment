"""Poison-pill / DLQ probe: dead-letter after 3 retries, no HOL block.

A consistently-failing manuscript (POISON_MARKER → parse raises every attempt)
must exhaust the 1/4/16s exponential backoff ladder and land on ``q.dlq`` — and
crucially, it must do so **off the hot queue**, so concurrently-submitted healthy
jobs are unaffected. The retry ladder rides dedicated TTL'd delay queues with a
dead-letter exchange, so a poisoned message's backoff never head-of-line-blocks
``q.parse``. The DLQ resolver then converges the poison job (parse-poison →
job FAILED), so it never hangs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx
import pytest

from .helpers import poison_manuscript

pytestmark = pytest.mark.e2e


async def test_poison_dlqs_after_retries_without_head_of_line_block(
    client: httpx.AsyncClient,
    wait_for_status: Callable[..., Awaitable[str]],
) -> None:
    async def submit(manuscript: str) -> str:
        resp = await client.post("/jobs", json={"manuscript": manuscript})
        assert resp.status_code == 202, resp.text
        return str(resp.json()["job_id"])

    # Submit healthy + poison together (poison last, so it sits behind healthy work).
    healthy = await asyncio.gather(
        *(submit(f"Healthy broadcast {i}.\n\nA closing block.") for i in range(3))
    )
    poison_id = await submit(poison_manuscript())

    # No head-of-line block: healthy jobs COMPLETE promptly while the poison is
    # still grinding its retry ladder on the delay queues (off q.parse).
    for jid in healthy:
        status = await wait_for_status(jid, target="COMPLETED", timeout=60.0)
        assert status == "COMPLETED", f"healthy job {jid} ended {status} — head-of-line block?"

    # Poison exhausts 3 retries (~1+4+16s) → q.dlq → the resolver resolves the parse-poison
    # to FAILED. It converges (does not hang) and never reaches COMPLETED.
    status = await wait_for_status(poison_id, target="FAILED", timeout=120.0)
    assert status == "FAILED", f"poison job ended {status}, expected FAILED after DLQ-after-3"
