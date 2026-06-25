"""X7 — exception taxonomy (retryable vs poison) (L2, pure; no Docker).

The consume loop (W2) must decide, on a raised exception, whether to retry
(transient → ladder) or dead-letter immediately (poison → DLQ). The classifier
``is_poison`` drives that single decision.

Reconciliation with R2.0 (docs/DECISIONS.md): the consistently-failing manuscript
is NOT poison here — it raises the retryable ``VendorError`` and dead-letters only
after the 3-retry ladder (SPEC §1). ``PoisonError`` is reserved for deterministically
unprocessable messages. Unknown exceptions are treated as transient (fail-safe).
"""

from __future__ import annotations

from core.domain.vendor import VendorError
from worker.errors import PoisonError, TransientError, is_poison


def test_taxonomy_classes_are_exceptions() -> None:
    assert issubclass(TransientError, Exception)
    assert issubclass(PoisonError, Exception)


def test_poison_error_is_classified_as_poison() -> None:
    assert is_poison(PoisonError("deterministically unprocessable")) is True


def test_transient_vendor_and_unknown_are_not_poison() -> None:
    # Transient -> ladder.
    assert is_poison(TransientError("flaky")) is False
    # R2.0: a vendor failure (incl. the poison manuscript) is retryable, not poison.
    assert is_poison(VendorError("simulated 500")) is False
    # Unknown/unclassified -> treated as transient (fail-safe; never ack-drop).
    assert is_poison(ValueError("unexpected bug")) is False
