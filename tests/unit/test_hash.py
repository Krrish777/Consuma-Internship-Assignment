"""content_hash unit tests (no Docker required).

The content hash is the cache/idempotency key for the vendor-call dedupe
(sha256 of the block TEXT). It MUST be stable across processes/runs and
deterministic, so identical blocks collapse to one vendor call + one MinIO
object. Pure domain.
"""

from __future__ import annotations

import pytest

from core.domain.hash import content_hash

# Well-known sha256 vectors — pin the algorithm + utf-8 encoding so a silent
# switch to e.g. utf-16 or a different digest would fail loudly.
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
HELLO_WORLD_SHA256 = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_known_vector_empty_string() -> None:
    assert content_hash("") == EMPTY_SHA256


def test_known_vector_hello_world() -> None:
    assert content_hash("hello world") == HELLO_WORLD_SHA256


def test_same_text_same_hash() -> None:
    assert content_hash("a block of text") == content_hash("a block of text")


def test_different_text_different_hash() -> None:
    assert content_hash("block A") != content_hash("block B")


def test_is_64_char_lowercase_hex() -> None:
    digest = content_hash("anything")
    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(c in "0123456789abcdef" for c in digest)


def test_whitespace_is_significant() -> None:
    # The hasher does not normalize — "hi" and "hi " are distinct cache keys.
    assert content_hash("hi") != content_hash("hi ")


def test_non_ascii_is_handled_deterministically() -> None:
    # utf-8 encoding means non-ASCII text hashes cleanly and stably.
    digest = content_hash("café — naïve")
    assert len(digest) == 64
    assert content_hash("café — naïve") == digest


@pytest.mark.parametrize("text", ["", "x", "hello world", "café", "line1\nline2"])
def test_idempotent_across_calls(text: str) -> None:
    assert content_hash(text) == content_hash(text)
