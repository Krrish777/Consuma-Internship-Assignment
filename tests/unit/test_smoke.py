"""Smoke tests — proves toolchain is wired and config knobs load correctly.

No Docker required. Uses Settings() directly (not get_settings()) for env-override
tests because lru_cache would return a stale cached instance.
"""

from __future__ import annotations

import pytest

from core.config import Settings, get_settings


def test_settings_defaults() -> None:
    """Spec defaults load correctly: TTS limit, retry count, delay ladder."""
    s = get_settings()
    assert s.TTS_CONCURRENCY == 3
    assert s.MAX_RETRIES == 3
    assert s.RETRY_DELAYS == "1,4,16"


def test_f02_new_knobs_defaults() -> None:
    """F0.2 knobs load with their spec defaults."""
    s = Settings()
    assert s.MAX_MANUSCRIPT_BYTES == 1_000_000
    assert s.MAX_BLOCKS == 10_000
    assert s.WEBHOOK_TIMEOUT_S == 5.0
    assert s.SWEEP_INTERVAL_S == 30
    assert s.PENDING_TIMEOUT_S == 120
    assert s.LEASE_TTL_S == 30
    assert s.CACHE_TTL_S == 86_400
    assert s.PROCESSED_EVENTS_RETENTION_S == 604_800


def test_retry_delays_parsed_to_tuple() -> None:
    """retry_delays property returns the delay ladder as a typed tuple of ints."""
    s = Settings()
    assert s.retry_delays == (1, 4, 16)


def test_webhook_allowlist_empty_gives_empty_tuple() -> None:
    """Empty WEBHOOK_ALLOWLIST (log-only mode) yields an empty tuple."""
    s = Settings()
    assert s.webhook_allowlist == ()


def test_env_override_changes_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env var overrides the default — Settings() reads from the environment."""
    monkeypatch.setenv("MAX_MANUSCRIPT_BYTES", "500000")
    s = Settings()
    assert s.MAX_MANUSCRIPT_BYTES == 500_000


def test_webhook_allowlist_parses_to_tuple(monkeypatch: pytest.MonkeyPatch) -> None:
    """Comma-separated WEBHOOK_ALLOWLIST parses into a tuple of host strings."""
    monkeypatch.setenv("WEBHOOK_ALLOWLIST", "api.example.com,cdn.example.com")
    s = Settings()
    assert s.webhook_allowlist == ("api.example.com", "cdn.example.com")
