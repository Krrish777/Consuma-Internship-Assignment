"""aio-pika adapter — connection + topology declaration (spec §5, §7).

STUB. Implemented across Rung 0.3 (connect + q.parse) and Rung 2.1 (full topology):
  connect() -> aio_pika.Connection         (connect_robust)
  declare_topology(channel)                 pipeline exchange, q.parse/q.tts/q.stitch,
                                            per-stage retry ladder (1s/4s/16s) + q.dlq

Raw aio-pika only — no Celery/Taskiq/ARQ/RQ (CLAUDE.md rule #2).
"""
