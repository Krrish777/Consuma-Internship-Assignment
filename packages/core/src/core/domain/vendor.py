"""Vendor simulation — fault injection substrate (spec §1-§2, R2.0).

Pure domain: only stdlib (random, hashlib). No asyncio, no I/O. The worker
handlers wrap these with await asyncio.sleep(...) to simulate latency; the pure
fault-injection logic here is unit-testable without any Docker infrastructure.

Interface
---------
  POISON_MARKER   str           include in manuscript text to guarantee failure
  VendorError     RuntimeError  simulated transient vendor 500; caller retries
  split_blocks    (text) -> list[str]
  simulate_parse  (text, *, failure_rate, rng) -> list[str]
"""

from __future__ import annotations

import hashlib
import random

POISON_MARKER = "__POISON__"
PARSE_FAILURE_RATE = 0.15  # spec §1: 15% transient error rate on the parse stage


class VendorError(RuntimeError):
    """Simulated transient vendor 500 error.

    Workers catch this in their top-level handler and route via route_retry_or_dlq.
    It is NOT permanent failure — the DLQ handles exhaustion after MAX_RETRIES.
    """


def split_blocks(text: str) -> list[str]:
    """Split a manuscript into non-empty blocks (one per non-blank line).

    Each non-empty, stripped line is one block. Order preserved. Blank or
    whitespace-only manuscripts return [] — the parse handler must advance
    directly to STITCHING in this case (R2.3 zero-block path).
    """
    return [line.strip() for line in text.splitlines() if line.strip()]


def simulate_parse(
    text: str,
    *,
    failure_rate: float = PARSE_FAILURE_RATE,
    rng: random.Random | None = None,
) -> list[str]:
    """Simulate a parse vendor call: block split + transient failure injection.

    Poison path (POISON_MARKER in text): always raises, exercises DLQ path.
    Normal path: raises VendorError with probability ``failure_rate``.
    With an explicit ``rng`` the draw is deterministic and unit-assertable.

    Returns the list of blocks if no failure is injected.

    Raises:
        VendorError: simulated transient 500 from the vendor.
    """
    if POISON_MARKER in text:
        raise VendorError("poison manuscript: guaranteed failure (exercises DLQ)")

    r = rng if rng is not None else random.Random()
    if r.random() < failure_rate:
        raise VendorError("simulated parse vendor 500 (transient)")

    return split_blocks(text)


def tts_fake_audio(text: str) -> bytes:
    """Return deterministic fake audio bytes for a text block.

    Content-hash deterministic: same text always produces the same bytes.
    This exercises the TTS cache dedup correctly (R4.2): a cache hit returns
    the same bytes and no second vendor call is needed.
    """
    digest = hashlib.sha256(text.encode()).digest()
    return b"FAKE_AUDIO:" + digest
