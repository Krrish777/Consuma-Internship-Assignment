"""W1 — per-queue prefetch sizing (H-PREFETCH) (L2, pure; no Docker).

PREFETCH=16 against only 3 global TTS slots parks 13+ messages unacked on a
blocked BLPOP per worker — a large crash-redelivery blast radius and head-of-line
pressure. So q.tts prefetch is sized near serviceable concurrency; the other
queues keep the global default.
"""

from __future__ import annotations

from core.config import Settings
from core.infra.broker import Q_PARSE, Q_STITCH, Q_TTS
from worker.main import prefetch_for


def test_tts_prefetch_sized_near_semaphore_not_global() -> None:
    s = Settings()  # TTS_CONCURRENCY=3, PREFETCH=16
    tts = prefetch_for(Q_TTS, s)
    assert tts <= s.TTS_CONCURRENCY + 1  # serviceable concurrency + tiny headroom
    assert tts < s.PREFETCH  # explicitly NOT the global 16 (H-PREFETCH)


def test_non_tts_queues_use_global_prefetch() -> None:
    s = Settings()
    assert prefetch_for(Q_PARSE, s) == s.PREFETCH
    assert prefetch_for(Q_STITCH, s) == s.PREFETCH
