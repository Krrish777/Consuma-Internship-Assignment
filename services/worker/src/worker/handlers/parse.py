"""STAGE A — Parse (Simulated LLM) (spec §8, §7.1).

STUB. Rung 1.4 / 2.2 / 3.2 implement:
  split_blocks(text) -> list[str]                (unit-TDD)
  handler: write N task rows + pending_count=N in ONE txn, fan-out TtsRequested xN,
           advance GENERATING. 0-block edge -> STITCHING directly (don't hang).
  sim: sleep(rand); raise 500 at PARSE_FAILURE_RATE (15%).
  idempotency: state-based on job status, NOT a generic inbox (spec §7.1).
"""
