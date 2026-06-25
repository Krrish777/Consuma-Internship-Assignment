"""Vendor simulation — fault-injection substrate.

Pure domain: stdlib + sibling domain primitives only. No asyncio, no I/O. The
worker handler wraps these with ``await asyncio.sleep(...)`` to model vendor
latency; the fault-injection logic here is unit-testable without any Docker.

Failure model (single retryable class)
------------------------------------------------
Every simulated failure raises ONE retryable class, ``VendorError``. The spec
defines a "poison pill" as a *consistently-failing* manuscript that lands in the
DLQ **after 3 retries with exponential backoff** — the same routing as the random
15% transient failures, not a fail-fast non-retryable error. So poison and
transient raise the *same* type; they differ only in outcome: a transient failure
almost always succeeds on retry, while a poison manuscript fails every attempt
and therefore deterministically exhausts the ladder → DLQ. We deliberately do
NOT model a separate non-retryable error (see docs/DECISIONS.md 2026-06-25).

Primitive ownership
-------------------
Block splitting and content hashing are the canonical domain primitives —
``simulate_parse`` composes ``core.domain.text.split_blocks`` and ``tts_fake_audio``
keys on ``core.domain.hash.content_hash``. They are imported, never duplicated.
"""

from __future__ import annotations

import random

from core.domain.hash import content_hash
from core.domain.text import split_blocks

POISON_MARKER = "__POISON__"
PARSE_FAILURE_RATE = 0.15  # 15% transient error rate on the parse stage


class VendorError(RuntimeError):
    """Simulated vendor 500 — the single retryable failure class.

    Workers catch this in the top-level handler and route via route_retry_or_dlq:
    the retry ladder (1/4/16s) then the DLQ after MAX_RETRIES. Both the random
    15% failures and the deterministic poison manuscript raise this same type;
    poison simply never succeeds, so it exhausts the ladder and dead-letters
    after 3 attempts.
    """


def simulate_parse(
    text: str,
    *,
    failure_rate: float = PARSE_FAILURE_RATE,
    rng: random.Random | None = None,
) -> list[str]:
    """Simulate the parse vendor call: failure injection, then the block split.

    Poison path (POISON_MARKER in text): always raises ``VendorError`` — a
    consistently-failing manuscript the retry ladder funnels to the DLQ after 3
    attempts. Normal path: raises ``VendorError`` with probability
    ``failure_rate``; pass an explicit ``rng`` for a deterministic, unit-assertable
    draw. On success, returns the canonical paragraph blocks.

    Raises:
        VendorError: simulated transient 500 (retryable; DLQ after MAX_RETRIES).
    """
    if POISON_MARKER in text:
        raise VendorError("poison manuscript: fails every attempt (exhausts retries -> DLQ)")

    r = rng if rng is not None else random.Random()
    if r.random() < failure_rate:
        raise VendorError("simulated parse vendor 500 (transient)")

    return split_blocks(text)


def tts_fake_audio(text: str) -> bytes:
    """Return deterministic fake audio bytes for a text block.

    Keyed on the canonical content hash: identical text → identical bytes,
    which is exactly what the TTS content cache relies on for dedup.
    """
    return b"FAKE_AUDIO:" + content_hash(text).encode("ascii")
