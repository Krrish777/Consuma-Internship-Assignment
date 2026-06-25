# SNIPPET-PREP — your research-and-prep guide (downtime → fast stitch-together)

> **Why this file exists.** We're near the weekly session limit (resets **2026-06-25 20:30 IST**).
> You (the human helper) will spend the downtime gathering + testing real code snippets into a
> `snippets/` folder. When the session resets, I read this map + your snippets and stitch them into
> the project fast, TDD per rung. This doc is the contract: it says *exactly* what I need, in what
> shape, so your snippets drop in with minimal reshaping.
>
> **Single source of truth for requirements stays `docs/SPEC.md`.** This file is *how we’ll build it*,
> not *what*. Where they conflict, SPEC wins.

---

## How the handoff loop works
1. You pick a rung from the **priority order** below (highest leverage first).
2. You gather/write a snippet, **run it** (locally or in a scratch script), confirm it works.
3. You drop it in `snippets/<rung-id>/` with the files described in the template.
4. When I'm back, I read `snippets/<rung-id>/NOTES.md` + the code, adapt it to our file paths and
   MUST rules, write the rung's test first (TDD), then wire it. You filled the research gap; I do the
   integration + typing + tests.

### Snippet folder template — `snippets/<rung-id>/`
```
snippets/R2.1-topology/
  NOTES.md        <- REQUIRED. See template below.
  snippet.py      <- the working code (or several .py files)
  proof.txt       <- paste of the command you ran + its output (proves it works)
```
**`NOTES.md` must contain:**
- **Source:** URL / repo / your own. (If adapted from a ref repo, name it.)
- **What it does** in 2–3 sentences.
- **How you ran it** (exact command) and **what you saw** (paste into `proof.txt`).
- **The hard part / gotcha** you hit — the thing I'd trip on.
- **Open question for me**, if any (e.g. "is durable=True right here?").

Don't polish for style — I'll re-type it into our conventions. I need it **correct and proven**,
not pretty. A snippet you actually ran beats a perfect-looking one you didn't.

---

## Global conventions every snippet must honor (so they drop in clean)
**Stack:** Python 3.13, async everywhere. SQLAlchemy 2.0 async + asyncpg. aio-pika (raw, **never Celery**).
redis-py 8 (`redis.asyncio`). minio (sync SDK — wrap calls in `asyncio.to_thread`). pydantic 2 / pydantic-settings 2.

**MUST rules (CLAUDE.md — these decide the grade; a snippet that breaks one is wrong):**
- **Ack dead last:** do work → COMMIT Postgres → PUBLISH next event → **ACK** the message. Never ack first.
- **Pointers, not bytes** in broker messages — keys/ids only; bytes live in MinIO.
- **Fan-in join** = atomic `UPDATE jobs SET pending_count=pending_count-1 ... RETURNING pending_count;`
  — never a Python counter.
- **TTS limit (3 global)** = a **Redis** leased semaphore with **TTL** (crash-safe), not `asyncio.Semaphore`.
  **Check the content cache BEFORE acquiring a slot** (a cache hit must burn no token).
- **DLQ after 3 retries**, exp backoff 1/4/16s, **off the hot queue** (no head-of-line blocking).
- `core/domain` stays **I/O-free** (a test enforces it). Anything touching DB/Redis/MinIO/broker lives in
  `core/infra` or the worker handlers.

**State placement (golden rule):** Postgres = durable truth · Redis = ephemeral coordination ·
MinIO = bytes · RabbitMQ = pointers. If durable truth lands in Redis, it's misplaced.

**Config keys already defined** (`core/config.py`, `get_settings()`): `DATABASE_URL`, `RABBITMQ_URL`,
`REDIS_URL`, `MINIO_ENDPOINT/ACCESS/SECRET`, `TTS_CONCURRENCY=3`, `PARSE_FAILURE_RATE=0.15`,
`MAX_RETRIES=3`, `RETRY_DELAYS="1,4,16"`, `PREFETCH=16`. Use these names; don't invent new ones.

**Already built (don't redo):** `core/domain/events.py` (JobCreated/TtsRequested/StitchReady, frozen,
`event_id`), `core/infra/broker.py` (`connect`, `declare_minimal`, `publish(exchange, event, routing_key)`,
`consume(channel, queue, handler, *, prefetch)` — manual ack). Exchange name `pipeline`, queue `q.parse`.
Your snippets should **publish/consume through these**, not re-implement them.

---

## Priority order (do the ⭐⭐⭐ first — highest leverage, hardest for me to write unaided)

| Pri | Rung | Snippet you prep | Why it's high-value |
|----|------|------------------|---------------------|
| ⭐⭐⭐ | **R2.1** | aio-pika **retry-ladder topology** (DLX + per-stage TTL delay queues + dlq) | The single hardest mechanism; easy to get subtly wrong. |
| ⭐⭐⭐ | **R4.1** | Redis **leased semaphore** (BLPOP acquire + TTL lease + release) | Distributed + crash-safe; the trap is the TTL/lease reclaim. |
| ⭐⭐⭐ | **R4.2** | **atomic fan-in** `UPDATE ... RETURNING` + content-hash cache | The race the whole grade hinges on. |
| ⭐⭐ | **R1.1** | SQLAlchemy 2.0 **async models** + **async Alembic** env.py + first migration | Async Alembic setup is fiddly; a proven `env.py` saves hours. |
| ⭐⭐ | **R1.3** | **MinIO** put/get/list + **concat** bytes for stitch | SDK is sync; need the `to_thread` + concat pattern proven. |
| ⭐⭐ | **R3.1** | worker **consume loop** with manual ack-LAST + prefetch + nack-on-error | Wires our `broker.consume` into a real handler with crash semantics. |
| ⭐⭐ | **R3.3** | **DLQ x-death gating** (count retries, route to dlq after 3) | The infinite-loop trap lives here. |
| ⭐ | **R2.2** | FastAPI **POST /jobs** ingestion (save MinIO → insert Job → publish) | Mostly glue; ref repo covers it. |
| ⭐ | **R3.2** | idempotency: `processed_events` inbox + `SETNX` | Patterns are short; I can mostly write these. |
| ⭐ | **R2.3 / R4.3 / R5.1** | parse fan-out / stitch+webhook / /stats | Glue over the above; low research need. |
| — | **R1.2** | Job FSM transition table | **I can write this unaided — skip unless you want to.** |
| — | **R0.3** | full-stack `./init.sh` health wait | Already authored; just needs a Docker run to verify. |

---

## Prep cards (per rung)

### ⭐⭐⭐ R2.1 — Broker retry-ladder topology
- **Target:** extend `core/infra/broker.py` — a `declare_topology(channel)` that supersedes
  `declare_minimal`: `pipeline` exchange + `q.parse`/`q.tts`/`q.stitch` + per-stage **retry/delay queues**
  (TTL 1s→4s→16s) that **dead-letter back** to the main queue, + a final `q.dlq`.
- **Mine from:** `tmp/Consuma-Reference-Repos/retry-dlx-aiopika/` and Brian Storti — "Exponential
  Backoff in RabbitMQ". (Both already noted in SPEC §7.)
- **Research/answer in NOTES.md:**
  - The dead-letter wiring: main queue `q.tts` has `x-dead-letter-exchange` → on nack, message goes to a
    delay queue `q.tts.retry.1` (with `message-ttl=1000` + DLX back to `q.tts`). Confirm the exact
    `arguments={...}` dict for each queue.
  - Do we need one delay queue per (stage × attempt) = 3 stages × 3 delays, or one shared ladder? Pick
    and justify. (Recommend per-stage so a poison pill in TTS doesn't block parse.)
- **Snippet I need:** a script that declares the full topology against a local RabbitMQ and prints the
  resulting queue list + their `arguments`. Bonus: publish a message, nack it 3×, watch it land in `q.dlq`.
- **Test it:** `docker run -d -p5672:5672 -p15672:15672 rabbitmq:4-management`, run your declare script,
  open `:15672` → Queues, screenshot/paste the arguments. **proof.txt** = the queue list + a message that
  reached `q.dlq` after 3 nacks.
- **Acceptance (my side):** integration test — poison message hits `q.dlq` after exactly 3 attempts at
  ~1/4/16s; a concurrent healthy message still completes (no head-of-line blocking).
- **Gotcha:** gate re-publish on the **`x-death` count** before requeuing, or you loop forever.

### ⭐⭐⭐ R4.1 — Redis leased semaphore (3 global TTS slots)
- **Target:** `core/infra/redis.py` → `class Semaphore` with `async acquire()` / `async release()`,
  built on `redis.asyncio`. Token list key `tts:slots`.
- **Mine from:** `tmp/Consuma-Reference-Repos/redis-lock-semaphore/` and `py-redis-semaphore` (BLPOP
  token-list pattern).
- **Research/answer in NOTES.md:**
  - Init: `RPUSH tts:slots t1 t2 t3` once (3 tokens). Acquire = `BLPOP tts:slots <timeout>` (blocks, no
    busy-poll). Release = `RPUSH tts:slots <token>`.
  - **The lease/TTL part (the crash-safety trap):** if a worker `BLPOP`s a token then dies, the token is
    gone forever → pool shrinks to 0 → deadlock. How do you reclaim? Research the **lease** approach: store
    the held token in a `tts:slots:held:<token>` key with `EX <ttl>`; a reaper (or each acquirer)
    `RPUSH`es back any held-key that has expired. Write down the exact reclaim mechanism you got working.
- **Snippet I need:** a runnable demo: start 5 concurrent coroutines that each acquire→`sleep`→release;
  assert never more than 3 hold a token at once. Plus a "kill" path: one coroutine acquires and never
  releases; show the token returns after TTL.
- **Test it:** `docker run -d -p6379:6379 redis:7`, run the demo, paste the "max concurrent = 3" assertion
  output and the "token reclaimed after TTL" output into proof.txt.
- **Acceptance:** e2e — >3 TTS requested, never >3 in flight; killed holder's slot returns.
- **Gotcha:** cache check happens BEFORE acquire (different rung, R4.2) — keep `Semaphore` cache-agnostic.

### ⭐⭐⭐ R4.2 — Atomic fan-in + content-hash cache
- **Target:** `worker/handlers/tts.py` → `decrement_and_check(session, job_id) -> int` (unit-TDD the SQL)
  and the cache calls in `core/infra/redis.py` (`cache_get(hash)`, `cache_set(hash, url)`).
- **Research/answer in NOTES.md:**
  - The exact SQLAlchemy 2.0 async statement for `UPDATE jobs SET pending_count = pending_count - 1
    WHERE job_id = :id RETURNING pending_count` and how to read the returned scalar. (This is the line that
    guarantees **exactly one** worker sees 0 and emits StitchReady.)
  - Cache: key `tts:cache:<sha256(text)>` → MinIO url, `SETEX` with a TTL. `cache_get` before slot acquire.
  - **Cache vs counter must NOT be conflated:** two identical blocks share one cache entry but are two task
    rows that each decrement. Confirm your snippet keeps them separate.
- **Snippet I need:** a script against local Postgres: insert a job with `pending_count=3`, fire 3
  concurrent `decrement_and_check` coroutines, assert exactly one returns 0 and the final count is 0
  (no lost decrement under concurrency).
- **Test it:** local Postgres (`docker run -d -p5432:5432 -e POSTGRES_PASSWORD=postgres postgres:17`),
  run the concurrency demo, paste "exactly one saw 0" output.
- **Acceptance:** e2e — identical block → no 2nd vendor call (cache); exactly one StitchReady (fan-in).
- **Gotcha:** the decrement is guarded by `SETNX task:done:<task_id>` so a duplicate delivery of the same
  task doesn't double-decrement — that's R3.2; note where the guard wraps the decrement.

### ⭐⭐ R1.1 — SQLAlchemy 2.0 async models + async Alembic
- **Target:** `core/domain/models.py` (the tables) + a new `alembic/` dir with async `env.py` + first
  migration. Models (signatures already in the stub): `Job(job_id, status, pending_count, callback_url,
  manuscript_key, final_key, created_at, updated_at)`, `Task(task_id, job_id, block_index, text,
  block_hash, status, audio_key, created_at, updated_at)`, `ProcessedEvent(event_id PK, consumed_at)`.
- **Research/answer in NOTES.md:**
  - **Async Alembic `env.py`** — the `run_async_migrations()` / `connectable = create_async_engine(...)`
    pattern. This is the fiddly part; a proven `env.py` is gold.
  - `DeclarativeBase` + `Mapped[...]` / `mapped_column(...)` 2.0 style. Status as a Python `Enum` mapped to
    a PG enum or a string — pick one, note why.
  - **Caution:** `models.py` lives under `core/domain`, which the architecture test says must be **I/O-free**
    — but it imports `sqlalchemy`. ⚠️ **Open decision for me:** we may need to move models to
    `core/infra/models.py` (since SQLAlchemy is in the `_IO_LIBS` ban list for `domain`). Flag this in
    NOTES; don't fight the test — I'll relocate if needed.
- **Snippet I need:** the three models + a working `alembic upgrade head` against local Postgres.
- **Test it:** `alembic upgrade head` then `\dt` shows `jobs`, `tasks`, `processed_events`. Paste into proof.
- **Acceptance:** integration test creates a Job, reads it back.

### ⭐⭐ R1.3 — MinIO adapter + concat
- **Target:** `core/infra/storage.py` → `ensure_bucket()`, `put_text(key, s)`, `get_text(key)`,
  `put_bytes(key, b)`, `get_bytes(key)`, `list_prefix(prefix)`. Bucket `audio-drama`; keys
  `raw/<job>.txt`, `tts/<hash>.wav`, `out/<job>.mp3`.
- **Mine from:** `tmp/Consuma-Reference-Repos/minio-sdk-examples/`.
- **Research/answer in NOTES.md:**
  - The minio SDK is **sync** → every call wrapped in `await asyncio.to_thread(...)`. Confirm the wrap.
  - `put_object` needs a length + stream (`io.BytesIO` + len). Note the exact call.
  - **Concat for stitch (R4.3):** "produced audio" is simulated, so concat = just bytes-append of the
    `tts/<job>/*.wav` objects in `block_index` order into one `out/<job>.mp3`. Confirm ordering by listing
    `tts/<job>/` and sorting. (No real audio lib needed.)
- **Snippet I need:** put 3 small files, `list_prefix`, get them back, concat into one, read it back equal.
- **Test it:** `docker run -d -p9000:9000 -p9001:9001 minio/minio server /data --console-address :9001`,
  run the round-trip, paste output.
- **Acceptance:** integration test — put/get/list round-trip; concat preserves order.

### ⭐⭐ R3.1 — Consume loop with manual ack-LAST
- **Target:** `worker/main.py` consume wiring + a dispatch table routing `q.parse`/`q.tts`/`q.stitch` to
  `handlers/{parse,tts,stitch}`. Uses our existing `broker.consume(channel, queue, handler, prefetch=...)`.
- **Research/answer in NOTES.md:**
  - The handler skeleton that does: parse body → event model → run stage → COMMIT → publish next → **ack**;
    on exception → **nack(requeue=False)** so the DLX/retry ladder (R2.1) takes it (NOT requeue=True, which
    re-heads the hot queue).
  - Confirm prefetch is set on the channel (we pass `prefetch=settings.PREFETCH`).
- **Snippet I need:** a minimal consumer that, on a handler exception, nacks→message goes to retry, and on
  success acks exactly once. Show with a counter that a crash *before* ack redelivers.
- **Test it:** reuse the R2.1 RabbitMQ; kill the process mid-handler, restart, show the message reappears.
- **Acceptance:** e2e crash_recovery — `docker kill` worker mid-job → redelivered, job converges.
- **Gotcha:** ack/nack EXACTLY once per message; double-ack throws.

### ⭐⭐ R3.3 — DLQ x-death gating
- **Target:** the nack path + a check on `message.headers["x-death"]` count vs `MAX_RETRIES` before letting
  the ladder recycle; over the limit → publish to `q.dlq` and set Job FAILED.
- **Research/answer in NOTES.md:** the exact shape of the `x-death` header aio-pika exposes (list of dicts
  with `count`), and how to read the cumulative count for this message.
- **Snippet I need:** print `x-death` for a message that's been nacked twice, so I see the real structure.
- **Acceptance:** e2e poison_pill — DLQ after 3 attempts; healthy jobs unaffected.

### ⭐ R2.2 — FastAPI POST /jobs ingestion
- **Target:** `gateway/main.py` → `POST /jobs` (body: manuscript text + optional callback_url): save
  `raw/<job>.txt` to MinIO → insert `Job(PENDING)` → publish `JobCreated` → return 202 + job_id. Plus
  `GET /status/{id}`. Gateway needs a publish channel (open in FastAPI **lifespan**, like the skeleton).
- **Mine from:** `fastapi-rmq-pg-glue/` + the base skeleton's lifespan publish pattern (already analyzed).
- **Research/answer in NOTES.md:** the FastAPI lifespan that opens `broker.connect` + a channel + the
  `pipeline` exchange, stored on `app.state`, and a `Depends` that yields a DB session.
- **Snippet I need:** a lifespan + one POST route that publishes through our `broker.publish`.
- **Acceptance:** integration — POST /jobs → 202 + Job PENDING in DB + event on `q.parse`.

### ⭐ R3.2 — Idempotency (inbox + SETNX)
- **Target:** `core/infra/redis.py` `seen_once(key)` (`SET key 1 NX EX ttl` → bool) + a
  `processed_events` insert (`INSERT ... ON CONFLICT DO NOTHING`) used at the top of parse/ingest handlers.
- **Research/answer in NOTES.md:** the SQLAlchemy async "insert on conflict do nothing" for asyncpg
  (`from sqlalchemy.dialects.postgresql import insert`), and reading whether a row was inserted.
- **Snippet I need:** insert same `event_id` twice → second is a no-op; `seen_once` returns True then False.
- **Acceptance:** e2e duplicate_delivery — same JobCreated ×2 → single effect, counter correct.

### ⭐ R4.3 — Stitch + webhook
- **Target:** `worker/handlers/stitch.py` → list `tts/<job>/`, concat → `out/<job>.mp3`, Job COMPLETED,
  fire webhook (`httpx.post(callback_url)`); **webhook failure logs a warning, job stays COMPLETED**.
- **Research/answer in NOTES.md:** just confirm the try/except around the webhook never flips status.
- **Snippet I need:** none mandatory (uses R1.3 concat); a tiny httpx-post-with-timeout-and-catch is enough.
- **Acceptance:** e2e — full happy path COMPLETED + asset; webhook 500 still COMPLETED.

### ⭐ R5.1 — GET /stats
- **Target:** `gateway/main.py` → `GET /stats`: counts of jobs by status (SQL `GROUP BY`) + optional queue
  depths. No snippet needed beyond a `select(func.count()).group_by(...)` — I'll write it.

### (skip unless you want to) R1.2 — Job FSM
- **I can write this unaided.** `LEGAL = {PENDING:{PARSING,FAILED}, PARSING:{GENERATING,FAILED}, ...}`,
  `can_transition(cur,nxt) = nxt in LEGAL[cur]`. If you want to prep it, just write the transition table
  in NOTES.md and I'll match it.

### (already authored) R0.2 / R0.3 — boot + full stack
- Just need a **Docker run** to verify: `make dev` (or `./init.sh`) → gateway `/health` 200, worker logs
  "worker connected", `pytest -m integration -k broker` green. Paste results into `snippets/R0-docker/proof.txt`.

---

## What I'll do the moment the session resets
1. Read this file + `PROGRESS.md` + every `snippets/*/NOTES.md`.
2. Start at the lowest open rung with a ready snippet; **write the test first**, then adapt your snippet
   into the target file, honoring the MUST rules.
3. Run the rung's `verification`; record evidence (commit hash) in `feature_list.json`; one atomic commit
   per rung — **only with your permission** (and watch for the auto-committer we saw).
4. Climb the ladder. If a snippet's missing, I implement from the ref repo directly but slower.

**Your highest-leverage hours:** R2.1 retry ladder, R4.1 leased semaphore, R4.2 atomic fan-in. If you only
prep three things, prep those — they're the mechanisms the grade actually weighs (SPEC §2, §4).
