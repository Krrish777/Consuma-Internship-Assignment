"""Redis adapter — ephemeral coordination (spec §5, §7).

STUB. Implemented across Rung 3.2 / Rung 4:
  seen_once(key) -> bool                 SET key 1 NX EX ttl   (idempotency)
  Semaphore(slots=3).acquire()/.release()  leased token w/ TTL (crash-safe, max 3)
  cache_get(hash)/cache_set(hash, url)   content-hash TTS cache

Redis keys: tts:slots, tts:cache:<sha256>, task:done:<task_id>.
"""
