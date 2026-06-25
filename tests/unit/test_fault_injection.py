"""R2.0 — Vendor simulation / fault injection unit tests (pure, no Docker).

Proves:
  - rate=0.0 never raises VendorError (across many draws)
  - rate=1.0 always raises VendorError (across many draws)
  - Fixed rng seed: same seed -> same outcome (reproducible)
  - POISON_MARKER always raises regardless of failure_rate
  - split_blocks: empty/blank -> []; non-empty lines split & stripped correctly
  - tts_fake_audio: content-hash deterministic (same text -> same bytes)
"""

from __future__ import annotations

import random

import pytest

from core.domain.vendor import (
    POISON_MARKER,
    VendorError,
    simulate_parse,
    split_blocks,
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

        result_a: str | None = None
        result_b: str | None = None

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


# ── poison manuscript ─────────────────────────────────────────────────────────


def test_poison_marker_always_raises() -> None:
    """POISON_MARKER in text guarantees VendorError regardless of failure_rate."""
    poison_text = f"hello {POISON_MARKER} world"
    for rate in (0.0, 0.15, 1.0):
        with pytest.raises(VendorError, match="poison"):
            simulate_parse(poison_text, failure_rate=rate)


def test_poison_marker_isolated_to_text_content() -> None:
    """Text without POISON_MARKER with rate=0.0 must never raise."""
    clean = "just a normal sentence without the marker"
    assert POISON_MARKER not in clean
    result = simulate_parse(clean, failure_rate=0.0)
    assert result == [clean]


# ── split_blocks ──────────────────────────────────────────────────────────────


def test_split_blocks_empty_string() -> None:
    assert split_blocks("") == []


def test_split_blocks_only_whitespace() -> None:
    assert split_blocks("   \n  \n\t") == []


def test_split_blocks_splits_non_empty_lines() -> None:
    text = "Line one\nLine two\n\nLine three"
    assert split_blocks(text) == ["Line one", "Line two", "Line three"]


def test_split_blocks_strips_surrounding_whitespace() -> None:
    assert split_blocks("  hello  \n  world  ") == ["hello", "world"]


def test_split_blocks_single_line() -> None:
    assert split_blocks("only one line") == ["only one line"]


# ── tts_fake_audio ────────────────────────────────────────────────────────────


def test_tts_fake_audio_deterministic() -> None:
    """Same text always produces the same bytes (cache dedup prerequisite)."""
    assert tts_fake_audio("block A") == tts_fake_audio("block A")


def test_tts_fake_audio_different_text_different_bytes() -> None:
    """Different inputs produce different outputs (no hash collision in test set)."""
    assert tts_fake_audio("block A") != tts_fake_audio("block B")
