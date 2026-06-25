"""DOC2 verification (Phase 8) — ARCHITECTURE.md must defend every boundary, honestly.

A coarse L1 guard for the reviewer-facing one-pager: it must exist, contain each required section
(data-placement table, fan-in, the four seams, exactly-once *effect*), state the honest limits, and
— the load-bearing MUST NOT — never *claim* exactly-once *delivery* (the system gives at-least-once
delivery + idempotent effect). Content accuracy is a manual read; this just stops the doc regressing
into missing a section or over-claiming.

Pure file read, no Docker — runs in `make check`.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DOC = _ROOT / "ARCHITECTURE.md"


def _body() -> str:
    """ARCHITECTURE.md, lowercased with whitespace collapsed (robust to line wrapping)."""
    return " ".join(_DOC.read_text(encoding="utf-8").split()).lower()


def test_architecture_doc_exists() -> None:
    assert _DOC.is_file(), "ARCHITECTURE.md must exist at the repo root (DOC2)."


def test_architecture_doc_has_all_required_sections() -> None:
    body = _body()
    required = {
        "postgres-durable-truth": "durable truth",
        "redis-ephemeral": "ephemeral coordination",
        "minio-bytes": "minio",
        "rabbitmq-pointers": "pointers",
        "fan-in": "fan-in",
        "four-seams": "four seams",
        "ack-last": "ack",
        "exactly-once-effect": "exactly-once effect",
        "honest-limits": "honest limits",
        "soft-semaphore": "soft",
    }
    missing = {topic: anchor for topic, anchor in required.items() if anchor not in body}
    assert not missing, f"ARCHITECTURE.md is missing required sections: {missing}."


def test_architecture_doc_states_at_least_once_and_disclaims_exactly_once_delivery() -> None:
    body = _body()
    assert "at-least-once delivery" in body, (
        "ARCHITECTURE.md must frame the guarantee as at-least-once delivery + idempotent effect."
    )
    assert "not exactly-once" in body, (
        "ARCHITECTURE.md must explicitly disclaim exactly-once *delivery* (the MUST NOT)."
    )
    # The MUST NOT: never make the affirmative claim.
    for forbidden in ("guarantees exactly-once delivery", "guarantee exactly-once delivery"):
        assert forbidden not in body, (
            f"ARCHITECTURE.md claims '{forbidden}'. The system provides at-least-once delivery + "
            "idempotent effect, NOT exactly-once delivery — restate it as the effect."
        )


def test_architecture_doc_maps_seams_to_e2e_probes() -> None:
    body = _body()
    # Each seam claim must be traceable to a real green probe file under tests/e2e/.
    for probe in ("test_crash_recovery", "test_duplicate_delivery", "test_cache_fanin"):
        assert probe in body, (
            f"ARCHITECTURE.md must cite the passing probe '{probe}' so a reviewer can trace the "
            "seam to a test (no aspirational prose)."
        )
