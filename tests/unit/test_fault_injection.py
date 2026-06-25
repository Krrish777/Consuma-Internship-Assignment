"""Vendor simulation / fault injection unit tests (pure, no Docker).

Proves:
  - rate=0.0 never raises; rate=1.0 always raises VendorError
  - fixed rng seed -> reproducible failure decision
  - POISON_MARKER always raises VendorError regardless of failure_rate, and it is
    the SAME retryable type as a transient 500 (consistently-failing -> retry
    ladder -> DLQ after 3; NOT a distinct non-retryable error)
  - on success, simulate_parse returns the canonical paragraph blocks
  - tts_fake_audio is deterministic and keyed on the canonical content hash

Block-splitting behavior is owned by tests/unit/test_text.py and content
hashing by tests/unit/test_hash.py — not re-tested here.
"""

from __future__ import annotations

import random

import pytest

from core.domain.hash import content_hash
from core.domain.vendor import (
    POISON_MARKER,
    VendorError,
    simulate_parse,
    tts_fake_audio,
)


# ── failure rate bounds ───────────────────────────────────────────────────────


def test_rate_zero_never_fails() -> None:
    for _ in range(30):
        result = simulate_parse("some text here", failure_rate=0.0, rng=random.Random())
        assert result == ["some text here"]


def test_rate_one_always_fails() -> None:
    for _ in range(30):
        with pytest.raises(VendorError):
            simulate_parse("some text here", failure_rate=1.0, rng=random.Random())


# ── seedable reproducibility ──────────────────────────────────────────────────


def test_fixed_seed_gives_same_outcome() -> None:
    """Two Random objects with the same seed must produce the same failure decision."""
    for seed in range(20):
        rng_a = random.Random(seed)
        rng_b = random.Random(seed)

        try:
            simulate_parse("test manuscript", failure_rate=0.5, rng=rng_a)
            result_a = "ok"
        except VendorError:
            result_a = "err"

        try:
            simulate_parse("test manuscript", failure_rate=0.5, rng=rng_b)
            result_b = "ok"
        except VendorError:
            result_b = "err"

        assert result_a == result_b, f"seed={seed}: outcomes diverged ({result_a} vs {result_b})"


# ── poison manuscript (retryable, same class as transient) ────────────────────


def test_poison_marker_always_raises_regardless_of_rate() -> None:
    """POISON_MARKER in text guarantees VendorError even at failure_rate=0.0."""
    poison_text = f"hello {POISON_MARKER} world"
    for rate in (0.0, 0.15, 1.0):
        with pytest.raises(VendorError, match="poison"):
            simulate_parse(poison_text, failure_rate=rate)


def test_poison_is_same_retryable_type_as_transient() -> None:
    # Poison is consistently-failing -> DLQ AFTER 3 retries, NOT a
    # distinct non-retryable error. Same exception type as a transient 500, so a
    # single handler routes both through the retry ladder.
    with pytest.raises(VendorError):
        simulate_parse(f"x {POISON_MARKER}", failure_rate=0.0)
    with pytest.raises(VendorError):
        simulate_parse("clean text", failure_rate=1.0, rng=random.Random(0))


def test_clean_text_rate_zero_returns_blocks() -> None:
    clean = "just a normal sentence without the marker"
    assert POISON_MARKER not in clean
    assert simulate_parse(clean, failure_rate=0.0) == [clean]


# ── delegation to the canonical primitives ──────────────────────────────


def test_simulate_parse_returns_d3_paragraph_blocks() -> None:
    # Delegates to split_blocks: blank-line paragraphs, soft newlines kept.
    text = "Para one\nstill one\n\nPara two"
    assert simulate_parse(text, failure_rate=0.0) == ["Para one\nstill one", "Para two"]


def test_tts_fake_audio_deterministic() -> None:
    """Same text always produces the same bytes (cache dedup prerequisite)."""
    assert tts_fake_audio("block A") == tts_fake_audio("block A")


def test_tts_fake_audio_different_text_different_bytes() -> None:
    """Different inputs produce different outputs (no collision in test set)."""
    assert tts_fake_audio("block A") != tts_fake_audio("block B")


def test_tts_fake_audio_keyed_on_d4_content_hash() -> None:
    # Proves the fake audio is derived from the canonical hasher, so the bytes
    # and the cache key (tts/<hash>.wav) stay in lockstep.
    assert tts_fake_audio("hello") == b"FAKE_AUDIO:" + content_hash("hello").encode("ascii")
