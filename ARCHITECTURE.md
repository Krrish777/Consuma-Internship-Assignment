# ARCHITECTURE — Consuma Audio Engine

A choreographed (no central orchestrator) async pipeline that turns a text manuscript into a
simulated produced audio drama: **gateway → `q.parse` → fan-out `q.tts` → fan-in → `q.stitch` →
`COMPLETED`**. This page defends *why each primitive holds what it holds* and how each non-atomic
"mutate-then-emit" boundary is made safe. Every claim maps to code and to a green probe — file names
in `tests/e2e/` (L4) and `tests/integration/` (L3) are cited inline. Source of truth: `docs/SPEC.md`;
rationale log: `docs/DECISIONS.md`.

---

## 1. Data placement — one sentence of defense per boundary

| Store | Holds | Why it lives here (the boundary defense) |
|-------|-------|------------------------------------------|
| **Postgres** | jobs, tasks, `processed_events` — the FSM, the `pending_count` fan-in counter, the durable idempotency inbox | **Durable truth.** Anything that must survive a crash/restart lives here; the fan-in counter is a DB column (not a Python variable, not Redis) precisely because losing it corrupts the join. |
| **Redis** | TTS semaphore tokens + leases, the content cache (`tts:cache:<hash>`), the `seen_once` fast-path | **Ephemeral coordination.** Everything here is *safe to lose / rebuildable*: the cache rebuilds from MinIO, the semaphore re-seeds. It is a coordination layer, **never** durable truth — that is the junior tell (Redis as the DB). |
| **MinIO** | `raw/<job>.txt` (manuscript), `tts/<hash>.wav` (per-block audio), `out/<job>.mp3` (final) | **Bytes.** Content-addressed by `sha256(text)` so identical blocks share one object. Objects never expire, so their lifetime always exceeds the cache TTL (**H-DANGLE** — no `tts:cache` entry can dangle to a 404). |
| **RabbitMQ** | `JobCreated`, `TtsRequested`, `StitchReady` events | **Pointers, never payloads.** A message carries `event_id`, `job_id`/`task_id`, and MinIO keys — never audio bytes. The bytes are in MinIO; putting them on the broker is the classic junior tell, and a hard MUST NOT. |

> Golden rule (CLAUDE.md): durable truth in Postgres · ephemeral coordination in Redis · bytes in
> MinIO · pointers on the broker. If durable truth ends up in Redis, or coordination in Postgres, it
> is misplaced.

---

## 2. The fan-in barrier — atomic, in the database

When N TTS tasks must converge into one stitch, the join is a single atomic statement:

```sql
UPDATE jobs SET pending_count = pending_count - 1 WHERE job_id = :id RETURNING pending_count;
```

Exactly one worker observes `RETURNING 0` and emits `StitchReady` — no read-subtract-write, no lost
update under concurrent redelivery, **never a Python counter** (`core/infra/queries.py::complete_task_and_decrement`, card B4).

The **idempotency guard is the durable conditional `tasks.status` UPDATE in the same transaction** —
`UPDATE tasks SET status='DONE', audio_key=… WHERE task_id=:id AND status<>'DONE'`, and the decrement
runs only if that claim's rowcount is 1. It is **not** a Redis `SETNX`: Redis is "safe to lose," and an
evicted key would let a redelivery double-decrement → an early `StitchReady` → an incomplete drama
wrongly marked `COMPLETED` (H3). Redis `seen_once` may exist only as a non-authoritative fast-path.
Two identical blocks share one cache entry but remain two task rows that each decrement — the cache
key (`sha256(text)`) and the counter key (`task_id`) are deliberately never conflated.

Probe: `tests/e2e/test_cache_fanin.py` (identical blocks dedup to one asset yet the fan-in counts
each → exactly one `StitchReady`); `tests/e2e/test_duplicate_delivery.py` (a redelivered `TtsRequested`
for an already-`DONE` task does not double-decrement).

---

## 3. The four seams — every "mutate-then-emit" boundary, and what converges it

The system has no orchestrator, so correctness rests on **ack ordering** at each consumer seam:
`do work → COMMIT Postgres → PUBLISH next event → ACK message`. **Ack is dead last.** Ack-before-publish
+ crash = a lost event and a stalled job; publish-then-crash = a duplicate, absorbed by idempotency
(`worker/handlers/_base.py::ack_last`, card W2). There are **four seams**:

| Seam | Crash window | Converging mechanism | Probe |
|------|-------------|----------------------|-------|
| **Gateway** (producer) | `COMMIT Job(PENDING)` succeeds, process dies before `publish JobCreated` → orphaned `PENDING` | "Ack last" is a *consumer* rule and cannot cover the *producer*. The **PENDING-sweeper** re-publishes `JobCreated` for jobs stuck `PENDING` past a timeout — the `Job` row **is its own outbox**; it selects ids only and never mutates status (`gateway/sweeper.py`, G8/H1). Safe only because parse is idempotent. | `tests/integration/test_sweeper.py` (L3); `tests/e2e/test_crash_recovery.py` (a durably-queued `JobCreated` survives a worker outage) |
| **Parse** (fan-out emitter) | rows committed, dies mid-publish of the N `TtsRequested` → some children un-emitted | Parse is **re-publishable, never inbox-skipped**: `INSERT … ON CONFLICT DO NOTHING` makes the rows idempotent, `begin_parse` seeds `pending_count` exactly once on the first `PENDING→PARSING` CAS (H15), and it **always re-publishes all N** events on redelivery (H2). Skipping them on an inbox hit would strand the children → hang in `GENERATING`. | `tests/e2e/test_duplicate_delivery.py` (duplicate `JobCreated` → exactly N rows, counter intact); L3 `test_parse.py` redelivery |
| **TTS** (cache → slot → fan-in) | decrement committed, dies before publishing `StitchReady` → barrier crossed, no event | B4's conditional claim means a redelivery does **not** double-decrement; **H-EMIT** covers the gap: when the claim no-ops, the handler re-reads `pending_count` and re-emits `StitchReady` if it is already 0 (`worker/handlers/tts.py`, W4). Cache is checked **before** acquiring a slot (a hit burns no token). | `tests/e2e/test_cache_fanin.py`; `tests/e2e/test_duplicate_delivery.py` |
| **Stitch** (finalize) | asset written, dies before `COMMIT COMPLETED`/ack → redelivery | Idempotent finalize: status writes are **compare-and-set** (`WHERE status IN (legal predecessors)`; rowcount-0 is a *normal* "someone else advanced it," not an error, H-FSM), the asset is content-addressed, and a redelivered `StitchReady` on an already-`COMPLETED` job short-circuits — no double asset, no illegal `COMPLETED→COMPLETED` (`worker/handlers/stitch.py`, W5/H5). | `tests/e2e/test_stitch_webhook.py`; L3 `test_stitch.py` redelivery |

A fifth resilience rule spans the DLQ: a poisoned TTS block that exhausts its `1/4/16s` retry ladder
lands on `q.dlq`, where a resolver — running **off the hot queue** so healthy traffic sees no
head-of-line blocking — marks the task `FAILED` and **still decrements the barrier**, so the job
converges to a partial-drama `STITCHING` instead of hanging forever (W7/H4). Retry gating uses our
custom `x-retry-count` header, **not** `x-death.count` (frozen on RabbitMQ ≥3.13, H-XDEATH). Probe:
`tests/e2e/test_poison_pill.py` (poison → DLQ after 3 attempts; concurrent healthy jobs still complete).

---

## 4. Exactly-once *effect* (not exactly-once delivery)

We do **not** claim exactly-once *delivery* — no broker can provide it, and pretending otherwise is the
mistake. What the system provides is **at-least-once delivery + idempotent processing = exactly-once
effect**. Crash recovery is what *creates* the duplicate-delivery problem (ack-last guarantees a message
is redelivered rather than lost before its effect is durable); idempotency — the `processed_events`
inbox, the conditional `tasks.status` claim, content-addressed MinIO writes, and FSM compare-and-set —
is what *absorbs* that duplicate so the observable effect happens once. Same coin. Proven end-to-end by
`tests/e2e/test_duplicate_delivery.py` (durable DB assertions: exactly N rows, no negative counter) and
`tests/e2e/test_behavior.py` (exact-bytes output + Postgres/MinIO consistency).

---

## 5. Honest limits (candor scores; silent gaps don't)

- **The TTS concurrency limit is a *soft*, best-effort limit (X5).** The global cap of 3 is enforced by a
  leased Redis token list; a ⅓-TTL heartbeat renews a live holder and an owner-checked reaper reclaims a
  crashed holder's token exactly once. Under a pathological pause a slow-but-alive holder could in
  principle have its lease reclaimed — breaches are **logged, not prevented**. The `>3`-never-concurrent
  bound is structural (BLPOP on a 3-token pool); the live peak is L3-proven (`tests/integration/test_redis.py`).
- **Redis bounce strands the semaphore (known gap, found in E-EDGE).** Compose Redis has no volume and
  `ensure_slots` runs only on worker boot, so `docker restart redis` wipes `tts:slots` and a running
  worker never re-seeds → BLPOP blocks. Not yet fixed (re-seed on reconnect / periodic seed / AOF). Not
  probed because it would hang; documented in `docs/DECISIONS.md` "Phase 6".
- **Cache-stampede lock implemented, not simplified (H8).** One vendor call per identical-block burst via
  a Redis `NX` in-flight lock, acquired *without* holding a TTS slot (no pool deadlock).
- **Schema via `create_tables`, not a real Alembic migration** — an accepted simulation simplification
  (single owner: the gateway lifespan); see "Phase 6".
- **Webhook delivery is L3-proven, not L4.** The SSRF guard correctly rejects every private/loopback IP,
  and a hermetic compose stack has no public sink — so real delivery is verified at L3
  (`tests/integration/test_webhook.py`); a webhook failure never fails the job (it stays `COMPLETED`, MUST #8).
- **B4 micro-edge:** `complete_task_and_decrement` guards only `status<>'DONE'`; a DLQ-failed task
  followed by a late TTS success could double-decrement (harmless — `StitchReady` already fired). The
  realistic flow can't reach it; left untouched rather than refactor a passing card.

---

*Traceability:* the full feature ladder and per-card evidence (commit-anchored) live in
`feature_list.json`; the L4 suite is `uv run pytest -m e2e` (14 probes green against the live compose stack).
