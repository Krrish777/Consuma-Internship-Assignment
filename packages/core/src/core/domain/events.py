"""Message contracts (pydantic) — broker payloads carry pointers, never bytes (spec §7).

STUB. Implemented across Rung 0.4 / 1.4 / 1.5:
  JobCreated    { event_id, job_id }              gateway -> q.parse
  TtsRequested  { event_id, job_id, task_id }     parse   -> q.tts  (xN)
  StitchReady   { event_id, job_id }              tts(last) -> q.stitch
All carry event_id for idempotency.
"""
