# Consuma Audio Engine — Source of Truth

> **This file is the single source of truth.** The original assignment brief plus the
> load-bearing decisions distilled from the author's research notes (`tmp/00-07`, not tracked).
> All code comments that cite `spec §N` resolve to the [Section Map](#section-map) below.
> When anything conflicts, **this file wins**. Refer here, not to `tmp/`.

---

## 1. The assignment (verbatim brief)

**Distributed Multi-Modal GenAI Pipeline.** Build the core asynchronous engine for a
Multi-Modal Generation Platform: users upload large text manuscripts; the system outputs a
fully produced audio drama. Vendor AI calls are **simulated** with `sleep()` + randomized
failure injection — the AI is not the point.

- **Restriction:** NO managed workflow orchestrators (Temporal, Airflow, Step Functions,
  **Celery**). Choreograph using core infrastructure primitives only.
- **Stack:** Python or Go → **this repo: Python 3.13, async, uv workspace.**

**Required infra (via docker-compose):** (1) API Gateway [FastAPI] · (2) Worker Node(s) ·
(3) Message Broker [RabbitMQ] · (4) State DB [Postgres] · (5) Cache/Lock store [Redis] ·
(6) Object storage [MinIO].

**Pipeline steps (simulated):**
1. **Ingestion** — Gateway receives a manuscript string, saves `.txt` to MinIO, creates a DB
   record (`PENDING`), publishes `JobCreated` to the broker.
2. **Parse (sim LLM)** — worker downloads the file, "parses" it. **Inject a 15% 500-error
   rate** to exercise retry logic.
3. **TTS (sim vendor)** —
   - *Constraint A (Concurrency):* vendor allows only **3 concurrent requests globally** →
     distributed semaphore in Redis across all workers.
   - *Constraint B (Cost/Idempotency):* identical text block sent twice must **not** re-hit
     the vendor → content-hash cache returning the prior MinIO URL.
4. **Stitch & Notify** — combine audio files, upload final asset to MinIO, set DB
   `COMPLETED`, fire a webhook (or log) to notify the user.

**Critical resilience requirements:**
| Requirement | Expected implementation |
|---|---|
| Idempotent consumers | Duplicate `JobCreated` delivery must not double-process or corrupt the DB. |
| Dead Letter Queue | A consistently-failing manuscript ("poison pill") → DLQ after **3 retries with exponential backoff**, **without** blocking the rest of the queue. |
| Crash recovery | `docker kill` mid-job must not lose the message; another worker picks it up after timeout, or it resumes on restart. |

---

## 2. What is actually being graded (the rubric)

> The audio drama is a costume. This is a **distributed-systems reasoning test**. The happy
> path is worth almost nothing; the grade lives in how failure is handled. Every probe (15%
> errors, poison pill, `docker kill`) maps to a judging dimension. **They will run these tests.**

1. **Architectural choices** — *did you choose, or copy?* Defend each primitive's boundary in
   one sentence. (Junior tell: audio bytes in the message, or Redis as the DB.)
2. **State across boundaries** *(highest weight)* — state that can race/disagree/be interrupted:
   service↔service, worker↔worker, broker↔DB (dual-write), Redis↔Postgres, before↔after crash.
3. **Edge-case handling** — beyond the 5 named failures: 0-block / 1-block manuscripts (fan-in
   must still terminate); cache-hit-meets-fan-in; parse crash after writing some task rows;
   dependency down mid-job; **webhook failure ≠ job failure** (job is still `COMPLETED`).
4. **System reliability** — `kill -9` at any line → converge to a correct final state. No
   message loss, exactly-once *effect*, no head-of-line blocking, no resource leak.

---

## 3. Architecture & data-placement (the golden rule)

Choreography, **not** orchestration: no central brain. Each service knows "on event X, do my
job, emit event Y." The DAG is emergent in the queue wiring + state table. The hard problem
this pushes onto us is the **fan-in join** (knowing when N parallel tasks are all done).

| Store | Role | Holds |
|---|---|---|
| **Postgres** | Durable truth (survives everything) | `jobs` (id, status enum, `pending_count`, callback_url), `tasks` (id, job_id, status, block_hash), `processed_events` (inbox) |
| **Redis** | Ephemeral coordination (safe to lose / rebuildable) | `tts:slots` (semaphore, 3 tokens), `tts:cache:<hash>` (hash→url, TTL), `task:done:<task_id>` (idempotency, TTL) |
| **MinIO** | The actual bytes | `raw/<job>.txt`, `tts/<hash>.wav`, `out/<job>.mp3` |
| **RabbitMQ** | Event transport / fan-out | carries **pointers/keys, never payloads** |

**Module layering (already scaffolded):** `core/domain` = pure logic, no I/O (unit-testable
without Docker) · `core/infra` = swappable adapters (db, redis, broker, storage) · `gateway`
depends on `core`, **never** on `worker` · `worker` depends on `core`, **never** on `gateway`.

---

## 4. The mechanisms that win the grade

- **Fan-in barrier:** `UPDATE jobs SET pending_count = pending_count - 1 WHERE job_id=:id
  RETURNING pending_count;` — atomic; exactly one worker sees `0` and emits the stitch event.
  Never count in a Python variable.
- **Ack ordering (the single most important line):** `do work → COMMIT Postgres → PUBLISH next
  event → ACK message`. **Ack dead last.** Ack-before-publish + crash = lost event, job stalls.
  Publish-then-crash = duplicate, absorbed by idempotency.
- **Exactly-once effect** = at-least-once delivery + idempotent processing. Crash recovery
  *creates* the duplicate problem idempotency *absorbs* — same coin.
- **Idempotency layers:** event-id inbox (`INSERT ... ON CONFLICT DO NOTHING` in
  `processed_events`) for parse/ingest; `SETNX task:done:<task_id>` for the fan-in decrement;
  content cache (`sha256(text)`) for the vendor call; object key = hash for MinIO writes.
- **Global TTS semaphore:** Redis token list, `BLPOP tts:slots` to acquire (blocks, no
  busy-poll), `RPUSH` to release. **Cache check happens BEFORE acquiring a slot** (a cache hit
  must not burn a token). Each slot is a **lease with TTL** so a crashed worker's slot
  auto-reclaims — otherwise the pool of 3 silently shrinks to 0 → deadlock.
- **DLQ retry ladder:** RabbitMQ has no native delay → per-queue TTL retry queues that
  dead-letter back to the main queue: `1s → 4s → 16s`, then to `q.dlq` after 3 attempts. Gate
  on the `x-death` count *before* re-publishing (infinite-loop trap). Failing message leaves
  the hot queue → no head-of-line blocking. (Defaults in `core/config.py`: `RETRY_DELAYS=1,4,16`,
  `MAX_RETRIES=3`.)
- **Cache vs counter must not be conflated:** dedup the *vendor call* (key `sha256(text)`),
  never the *counter decrement* (key `task_id`). Two identical blocks in one job share a cache
  entry but remain two task rows that must each decrement.

---

## 5. Section Map (resolves code's `spec §N` comments)

| § | Topic | Where it lives |
|---|---|---|
| §5 | Workspace split & module layering | §3 above; `packages/core`, `services/{gateway,worker}` |
| §6 | Postgres models + job FSM (`PENDING→PARSING→GENERATING→STITCHING→COMPLETED/FAILED`, legal transitions only) | `core/domain/models.py`, `core/domain/state.py` |
| §7 | Message contracts (pointers not bytes); §7.1 = state-based parse idempotency | `core/domain/events.py` |
| §8 | Pipeline stages A(parse)→C(tts)→D(stitch) | `worker/handlers/*` |
| §9 | Resilience (retry/DLQ/crash/concurrency/idempotency) | §4 above |
| §10 | Env-driven config (compose-injected) | `core/config.py` |

---

## 6. Locked decisions (2026-06-24)

- **Goal:** build the harness first, then implement the pipeline through it.
- **Environment:** author `docker-compose.yml` + `init.sh` (6 services; env injected per §10).
- **Definition of Done — 5 gates the agent must pass before claiming "done":**
  1. `ruff check` + `ruff format --check`
  2. `pytest` (unit + async + testcontainers integration)
  3. `mypy --strict` (type-check)
  4. **E2E crash tests** — docker-kill / poison-pill / duplicate-delivery scenarios run for real
  5. **Behavior/functional tests** — prove the system actually produces correct output, not just
     that units pass
- **Project agent instructions** live in repo-root `CLAUDE.md` (this `docs/SPEC.md` is the
  requirements truth it points to).

---

## 7. Curated references (only the genuinely useful)

- Skeleton shape: `kieled/fastapi-aiopika-boilerplate` (FastAPI + aio-pika, **not Celery**).
- DLQ backoff theory: Brian Storti — "Exponential Backoff in RabbitMQ" (read before any retry code).
- Redis semaphore: `py-redis-semaphore` (BLPOP token-list pattern).
- Mechanism docs: RabbitMQ DLX docs; Postgres `INSERT ... ON CONFLICT` / `UPDATE ... RETURNING`.
- **The Celery trap:** ~80% of "FastAPI + RabbitMQ" tutorials use Celery (banned). See
  `@app.task`/`@shared_task`/`Flower` → wrong mental model, close the tab.
