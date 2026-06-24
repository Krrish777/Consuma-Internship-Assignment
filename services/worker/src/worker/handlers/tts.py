"""STAGE C — TTS (Simulated vendor) (spec §8, §9 #2/#5/#6).

STUB. Rung 1.5 / 4.1 / 4.2 implement, in order:
  (1) content-hash cache check  BEFORE acquiring a slot
  (2) acquire 1 of 3 global Redis slots (leased w/ TTL)
  (3) "generate" + store tts/<hash>.wav
  (4) release slot
  (5) atomic fan-in: UPDATE jobs SET pending_count=pending_count-1 ... RETURNING;
      if 0 -> publish StitchReady
  decrement_and_check(session, job_id) -> int   (unit-TDD the atomic decrement)
  task:done:<task_id> SETNX guards the decrement (dedup counter, NOT the vendor call).
"""
