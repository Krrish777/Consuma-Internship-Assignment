"""DOC1 verification (Phase 8) — the SPEC must match the built code, not teach the old bugs.

`docs/SPEC.md §4` predates the 2026-06-24 arch review and originally *taught* several of the
S0/S1 bugs (x-death gating, Redis-SETNX fan-in idempotency, ...). Once the corrected mechanisms
were built (B4, W3, W7, G8, W5, H-FSM, H-SSRF), this L1 test pins §4 to the corrected story so a
future edit can't silently regress the source of truth back into teaching a fixed bug.

Pure file read, no Docker — runs in `make check` (L1/L2) and the commit gate.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = _ROOT / "docs" / "SPEC.md"


def _section_4() -> str:
    """Return SPEC §4 ('## 4.' up to the next '## ' heading), lowercased with whitespace collapsed.

    Whitespace is collapsed to single spaces so the checks are robust to line wrapping — the prose
    intentionally wraps, e.g. the old "Gate\\n  on the `x-death` count" split across two lines.
    """
    text = _SPEC.read_text(encoding="utf-8")
    start = text.index("## 4.")
    end = text.index("## 5.", start)
    return " ".join(text[start:end].split()).lower()


def test_spec_no_longer_teaches_x_death_count_gating() -> None:
    body = _section_4()
    # The old bug: "Gate on the `x-death` count before re-publishing" (H-XDEATH: x-death.count is
    # frozen on RabbitMQ >=3.13 under persistent delivery, so gating on it loops forever).
    assert "gate on the `x-death`" not in body, (
        "SPEC §4 still instructs gating on x-death (H-XDEATH bug). FIX: gate on the custom "
        "`x-retry-count` header instead — see docs/DECISIONS.md F0.4/H-XDEATH."
    )


def test_spec_section_4_documents_all_seven_corrections() -> None:
    body = _section_4()
    # topic -> a phrase that must appear in §4 once the correction is applied.
    required = {
        "1-x-retry-count": "x-retry-count",
        "2-fan-in-idempotency-in-db": "conditional `tasks.status`",
        "3-parse-republishable-emitter": "re-publishable emitter",
        "4-pending-sweeper": "pending-sweeper",
        "5-dlq-fan-in-rule": "resolve the fan-in barrier",
        "6-stitch-idempotency-cas": "compare-and-set",
        "7-security-notes": "ssrf",
    }
    missing = {topic: anchor for topic, anchor in required.items() if anchor not in body}
    assert not missing, (
        f"SPEC §4 is missing corrected-mechanism topics: {missing}. "
        "Each of the 7 arch-review corrections (BACKLOG 'Spec changes this implies') must appear."
    )


def test_spec_section_4_covers_size_and_block_count_security_notes() -> None:
    body = _section_4()
    # Correction 7 also covers the DoS surfaces beyond SSRF (H13 manuscript size, H14 block count).
    for anchor in ("manuscript-size", "block-count"):
        assert anchor in body, (
            f"SPEC §4 security notes omit '{anchor}'. FIX: document the manuscript-size (H13) and "
            "block-count (H14) caps alongside the SSRF guard (H-SSRF)."
        )
