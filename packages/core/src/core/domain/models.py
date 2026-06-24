"""SQLAlchemy models — durable truth in Postgres (spec §6).

STUB. Rung 1 / Task 1.1 implements:
  Job             (job_id, status, pending_count, callback_url, manuscript_key,
                   final_key, created_at, updated_at)
  Task            (task_id, job_id, block_index, text, block_hash, status,
                   audio_key, created_at, updated_at)
  ProcessedEvent  (event_id PK, consumed_at)   -- idempotency inbox
"""
