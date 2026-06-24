"""Smoke test — proves the toolchain is wired (pytest collects, imports resolve,
core installs into the workspace venv). No Docker required. Behavior/functional
tests for the pipeline arrive with the implementation rungs.
"""

from __future__ import annotations

from core.config import get_settings


def test_settings_defaults() -> None:
    """Spec defaults (config.py) load correctly: the 3-slot TTS limit and 1/4/16 ladder."""
    s = get_settings()
    assert s.TTS_CONCURRENCY == 3
    assert s.MAX_RETRIES == 3
    assert s.RETRY_DELAYS == "1,4,16"
