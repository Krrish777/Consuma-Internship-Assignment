"""Job finite-state machine + legal-transition guard (spec §6).

STUB. Rung 1 / Task 1.2 (unit-TDD) implements:
  LEGAL: dict[Status, set[Status]]
  can_transition(cur, nxt) -> bool

FSM:  PENDING -> PARSING -> GENERATING -> STITCHING -> COMPLETED
      any state -> FAILED
"""
