"""X7 — exception taxonomy: retryable vs poison (spec §1; CLAUDE.md MUST #5).

The consume loop (W2 ``ack_last``) must decide, when a handler raises, whether to
**retry** (transient → the 1/4/16s ladder) or **dead-letter immediately** (poison
→ DLQ). One predicate, :func:`is_poison`, drives that branch.

Two classes:
  * :class:`TransientError` — a retryable failure (flaky vendor, transient I/O).
    Routed onto the retry ladder; dead-letters only after ``MAX_RETRIES``.
  * :class:`PoisonError` — a **deterministically unprocessable** message (e.g. an
    event that will never validate). Retrying wastes three attempts on a guaranteed
    failure, so it dead-letters at once.

Unknown / unclassified exceptions are treated as **transient** (fail-safe): we
retry up to ``MAX_RETRIES`` and then DLQ, rather than ack-and-drop a message
because of a bug we did not anticipate (that would silently lose work).

Reconciliation with R2.0 (docs/DECISIONS.md 2026-06-25): the consistently-failing
*manuscript* is NOT a ``PoisonError``. Per SPEC §1 it raises the single retryable
``core.domain.vendor.VendorError`` and dead-letters only after exhausting the
ladder — same routing as a random transient failure, it simply never succeeds.
``PoisonError`` is reserved for messages that are structurally impossible to process.
"""

from __future__ import annotations


class TransientError(Exception):
    """Retryable failure — route onto the retry ladder (DLQ only after MAX_RETRIES)."""


class PoisonError(Exception):
    """Deterministically unprocessable message — dead-letter immediately, no retries."""


def is_poison(exc: BaseException) -> bool:
    """True iff ``exc`` is a deterministic poison (→ immediate DLQ).

    Everything else — :class:`TransientError`, ``VendorError``, and any unknown
    exception — is treated as transient and routed onto the retry ladder.
    """
    return isinstance(exc, PoisonError)
