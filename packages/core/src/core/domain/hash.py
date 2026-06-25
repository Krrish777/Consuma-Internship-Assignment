"""Canonical content hasher (BOM 02-D4, feeds R4.2 cache + R2.3 Task.block_hash).

Pure domain: no I/O, no randomness, no global state.

This is the SINGLE canonical hasher for the engine. Two distinct idempotency
keys must never be conflated (SPEC §4):
  - the **vendor-call cache** keys on ``content_hash(text)`` — identical blocks
    dedupe to one TTS call, and the MinIO object key is ``tts/<hash>.wav``;
  - the **fan-in decrement** keys on ``task_id`` — two identical blocks are still
    two tasks that each decrement the pending counter.

This module provides only the *content* hash. Hashing the task_id here (or
hashing anything other than the block text) is the named junior trap — keep this
function hashing TEXT only.
"""

from __future__ import annotations

import hashlib


def content_hash(text: str) -> str:
    """Return the sha256 hex digest of ``text`` (utf-8), stable across runs.

    Deterministic, unsalted, lowercase 64-char hex — safe as a cache key and a
    MinIO object key. See the module docstring for the cache-vs-counter rule.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
