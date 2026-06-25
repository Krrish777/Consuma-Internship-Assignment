# PROGRESS — Consuma Audio Engine

> Durable cross-session state. Read at session start; update before session end.
> Structured feature status lives in `feature_list.json` (see CLAUDE.md). Decisions: `docs/DECISIONS.md` + `docs/SPEC.md §4, §6`.

## Current State
- Phase: **Phase 5 — Gateway completion COMPLETE** (docs/features/05-gateway.md fully exhausted).
  Phases 0–4 complete.
- Active card: **none** — all 3 gateway BOM cards (G7/R5.1, G8/R3.4, H13) `passing`; WIP=0.
- `make check` (L1 static + L2 unit): **GREEN** — ruff + ruff format + mypy --strict (67 files) + 106 unit tests.
- Integration tests (Docker up, real pg+rmq+redis+minio): per-card L3 all green —
  **Phase 5:** stats 3, ingestion 9 (incl. 2 H13), sweeper 4; full gateway trio (ingestion+stats+sweeper)
  16 passed with the sweeper task live in the lifespan (no regression).
  **Phase 4 (still green):** worker_bootstrap 2, parse 5, tts 3, dlq_resolver 3, stitch 3, webhook 3
  (+ pre-existing models/broker/storage/redis/topology). New `tests/integration/conftest.py` starts all
  four containers once per session (`worker_stack`) and builds a fresh `WorkerContext` per test.
- **Phase-4 L3/L4 split:** handler logic is verified at L3 (direct handler invocation against real
  containers). The genuinely-L4 probes (docker kill mid-job → redeliver; live poison → DLQ-after-3) stay
  with their Phase-6 `R3.x` owners; each card's evidence states the split (no required level silently skipped).
- **Phase 4 design notes** (docs/DECISIONS.md 2026-06-25 "Phase 4"): ack-last via `process(ignore_processed)`;
  W3 deterministic task_id + 0-block→StitchReady; W4 cache-before-slot + H-EMIT; W7 partial-drama policy
  (FAILED block, barrier still resolves) — alternative hard-fail is a one-line swap; W5 DB-ordered chunks +
  client-side concat; X7 PoisonError reserved for structurally-unprocessable (manuscript poison stays retryable).
- **R2.0 divergence RESOLVED** (docs/DECISIONS.md 2026-06-25 R2.0): SPEC §1 wins — poison is a
  *consistently-failing* manuscript → DLQ after 3 retries via a SINGLE retryable `VendorError`.
- **Phase 2 design notes** (docs/DECISIONS.md 2026-06-25 Phase 2): H8 stampede lock IMPLEMENTED (not the
  simplification); the global TTS limit is best-effort/SOFT (heartbeat + logged reclaim, honest framing);
  `mark_event` (Postgres) is the idempotency authority, Redis `seen_once` only a fast-path (H3).

## Completed (all earning `passing` in feature_list.json)

### Rungs 0–1: Core infrastructure (fully passing)
- [x] uv workspace scaffold: packages/core, services/{gateway,worker}
- [x] core/config.py — env settings w/ spec defaults
- [x] docs/SPEC.md — single source of truth
- [x] CLAUDE.md — instruction-file router + session ritual
- [x] PROGRESS.md + docs/DECISIONS.md — durable state + decision log
- [x] Initialization phase: docker-compose.yml (6 svc), Dockerfiles, .dockerignore,
      Makefile (DoD runner), mypy --strict config, pytest config, .env.example, init.sh, smoke test
- [x] .claude harness hooks: block-coauthor, verify-before-commit, check-line-cap, check-wip, check-evidence
- [x] R0.1 — GET /health 200 (commit b6f68f3)
- [x] R0.2 — Worker boots, connects RabbitMQ, idles (commit ac9dee0 + 61d73fe)
- [x] R0.3 — Full 6-service docker-compose stack, all healthy (commit ac9dee0 + 61d73fe)
- [x] R0.4 — Structured logging: configure_logging/get_logger, contextvars job_id isolation (commit ac9dee0 + 61d73fe)
- [x] R1.1 — SQLAlchemy models Job/Task/ProcessedEvent + Alembic migration, Postgres (commit ac9dee0 + 61d73fe)
- [x] R1.2 — Job FSM can_transition() enforces legal state transitions (commit ac9dee0 + 61d73fe)
- [x] R1.3 — MinIO storage adapter: ensure_bucket/put_text/get_text/put_bytes/get_bytes/list_prefix (commit ac9dee0 + 61d73fe)
- [x] R1.4 — Pydantic event contracts JobCreated/TtsRequested/StitchReady (commit b6f68f3)
- [x] R2.1 — Broker topology: exchange + q.parse/tts/stitch + retry ladder (1/4/16s) + DLQ (commit ac9dee0 + 61d73fe)
- [x] R2.2a — Gateway lifespan wiring (open robust broker + channel + exchange + db engine + storage) (commit 61d73fe)
- [x] R2.2b — Gateway pydantic schemas: CreateJobRequest/JobAccepted/JobStatusResponse (commit 61d73fe)
- [x] R2.2c — Gateway CORS + error handlers: 422 structured JSON / 500 JSON (commit 61d73fe)
- [x] R2.2 — Ingestion: POST /jobs → MinIO → PG PENDING + COMMIT → publish JobCreated → 202 (commit 61d73fe)
- [x] R2.2d — Status: GET /status/{job_id} → job status; unknown id → 404 (commit 61d73fe)

### Phase 0 — Foundation reconciliation (all passing)
- [x] F0.1 — Tracker reconciled + Ryuk fix + ruff format fix (anchor ed6693e)
- [x] F0.2 — 9 config knobs + retry_delays/webhook_allowlist property accessors (anchor ed6693e)
- [x] F0.3 — Deleted dead domain/models.py (0 imports) (anchor ed6693e)
- [x] F0.4 — DECISIONS.md H-XDEATH entry: x-retry-count is authority, Task.attempts deferred (anchor ed6693e)

### Phase 1 — Domain pure logic (all passing; L2-only, no Docker)
- [x] D3 — `core/domain/text.py` `split_blocks()`: blank-line paragraph splitter; 0-block path → [] (anchor 0aff00f)
- [x] D4 — `core/domain/hash.py` `content_hash()`: canonical sha256(text) hex; cache/idempotency key (anchor 0aff00f)
- [x] H-FSM — `core/domain/state.py`: CAS contract doc (rowcount-0 = normal) + `expected_for()` predecessor helper (anchor 0aff00f)
- [x] R2.0 — `core/domain/vendor.py` reconciled per SPEC §1: single retryable `VendorError` (poison = DLQ-after-3, not fail-fast); composes D3/D4; 10 tests (anchor 0aff00f)

### Phase 2 — Redis coordination (all passing; L3 testcontainers, Docker required)
- [x] R1 — `core/infra/redis.py` `get_redis`/`ping`: shared `redis.asyncio` client, bytes mode; 2 tests (anchor 6203aeb)
- [x] R2 — `Semaphore` leased N-token BLPOP/RPUSH + TTL lease + async-with `slot()`; 4 tests (anchor 6203aeb)
- [x] X4 — `Semaphore.ensure_slots` atomic Lua exactly-once seed (3×N footgun); 2 tests (anchor 6203aeb)
- [x] X5 — lease reaper: ⅓-TTL `SET XX` heartbeat + owner-checked Lua `reap()`; soft-limit logged; 3 tests (anchor 6203aeb)
- [x] R3 — `Cache` content-hash cache (`SET..EX`, keyed on D4); 4 tests (anchor 6203aeb)
- [x] H8 — `Cache` in-flight stampede lock (`acquire_inflight`/`wait_for_cache`); 2 tests (anchor 6203aeb)
- [x] R4inbox — durable `db.mark_event` (ON CONFLICT, authority) + `purge_processed_events` (H10) + Redis `seen_once` fast-path; 3 tests (anchor 6203aeb)

### Phase 3 — DB query layer (all passing; L3 testcontainers, Docker required) — `core/infra/queries.py`
- [x] B4 — `complete_task_and_decrement`: in-tx conditional task claim (H3 authority) + atomic `UPDATE jobs … pending_count-1 RETURNING`; 3 fan_in tests (anchor b891a95)
- [x] H15 — `begin_parse`: PENDING→PARSING CAS seeding `pending_count=N` only on rowcount 1 (re-run never resets); 3 counter_once tests (anchor b891a95)
- [x] B6 — `job_counts_by_status`: read-only `GROUP BY` per-status aggregate for /stats (G7); 1 stats test (anchor b891a95)

### Phase 4 — Worker pipeline (all passing; L3 testcontainers, Docker required) — anchor f62825c
- [x] X3 — `worker/bootstrap.py` `build_context(settings=None)`/`close_context` + `WorkerContext`; ensure_slots once; 2 tests
- [x] X2 — `worker/dispatch.py` `build_handlers(ctx)` queue→handler table; handler factories in handlers/{parse,tts,stitch}; 1 unit test
- [x] X1 — `worker/main.py` run loop: build_context → register_consumers → await shutdown; SIGTERM/SIGINT (POSIX + Windows fallback); 2 unit tests
- [x] W1 — `prefetch_for` per-queue prefetch (q.tts ≈ TTS_CONCURRENCY+1, not 16; H-PREFETCH); per-queue channels; 2 unit tests
- [x] X7 — `worker/errors.py` TransientError/PoisonError + `is_poison` (manuscript poison stays retryable per R2.0); 3 unit tests
- [x] W2 — `worker/handlers/_base.py` `ack_last` ⭐: process(ignore_processed) + terminal ack; poison→DLQ via max_retries=0; 7 unit tests
- [x] W3 — `worker/handlers/parse.py` ⭐: ON CONFLICT tasks + begin_parse (1 tx) → advance GENERATING → always re-publish; 0-block→StitchReady; MAX_BLOCKS cap; 4 L3 tests
- [x] W4 — `worker/handlers/tts.py` ⭐: cache-before-slot → H8 lock (no slot) → leased slot synth → B4 fan-in → StitchReady; H-EMIT; 3 L3 tests
- [x] W7 — `worker/handlers/dlq.py` (H4): q.dlq resolver, partial-drama policy (FAILED block + decrement); routes by body shape; own ack/nack; 3 L3 tests
- [x] W5 — `worker/handlers/stitch.py`: DB-ordered chunks + client-side concat → out/<job>.mp3 → CAS COMPLETED; idempotent (H5); 2 L3 tests
- [x] H-SSRF — `worker/ssrf.py` `is_allowed`: allowlist + resolve-all-records private/loopback/etc block; 9 unit tests
- [x] W5b — `_notify` in stitch.py: log-only if no allowlist, else SSRF guard + httpx post; failure swallowed (MUST #8); 3 L3 tests
- New `core/infra/queries.py` helpers: `advance_status`, `fail_task_and_decrement` (W7), `finalize_job` (W5)

### Phase 5 — Gateway completion (all passing; L3 testcontainers, Docker required) — anchor e48428e
- [x] G7 / R5.1 — `GET /stats`: `StatsResponse{jobs:dict[str,int]}` over B6 `job_counts_by_status` (SQL GROUP BY),
      zero-filled across all 6 FSM states for a stable shape; read-only. Queue depths deliberately omitted
      (kept robust). 3 L3 tests (test_stats.py).
- [x] H13 — manuscript size guard: `guard_manuscript_size` HTTP middleware checks `Content-Length` BEFORE the
      body is buffered into the pydantic model (route-level len() would be too late to prevent OOM); oversized →
      machine-readable 413 JSON. 2 new L3 tests in test_ingestion.py (9 total).
- [x] G8 / R3.4 ⭐ — PENDING-sweeper: `gateway/sweeper.py` `sweep_once` re-publishes JobCreated for jobs stuck
      PENDING past `PENDING_TIMEOUT_S` (DB-side now() cutoff, select job_ids only, NEVER mutates status); safe
      only because parse is idempotent (H2/H15). `run_sweeper` loops it (sleep-first), launched as an asyncio.Task
      in the gateway lifespan, cancelled cleanly on shutdown. 4 L3 tests (test_sweeper.py).

### Tests (43 unit + integration)
- `tests/unit/` — 37 tests (architecture, error_handlers, events, health, logging, schemas, smoke, state_machine)
- `tests/integration/test_ingestion.py` — 7 tests (lifespan, POST /jobs ×4, GET /status ×2)
- `tests/integration/test_broker.py` — broker round-trip + manual-ack-redelivery (testcontainers RabbitMQ)
- `tests/integration/test_models.py` — Job CRUD, Task constraints, ProcessedEvent ON CONFLICT dedup
- `tests/integration/test_storage.py` — MinIO: idempotent bucket, text/bytes roundtrip, list_prefix, key_exists

## In Progress
- **none** — WIP=0. Phase 5 gateway completion complete (all 3 BOM cards passing). Next phase is the
  Phase-6 L4 e2e probes.

## What's Genuinely Unbuilt (FEATURES.md scope)
Phases 0–5 built (foundation, domain logic, Redis coordination, DB query layer, worker pipeline, gateway
completion). The full parse→TTS→stitch choreography + DLQ resolver + webhook + /stats + PENDING-sweeper now
run end-to-end at L3. Remaining:
- **Still deferred (optional primitives, not yet scheduled):** `Semaphore.reap()` (worker-side lease reaper)
  and `purge_processed_events()` (H10 inbox retention) remain primitives — nothing calls them periodically.
  G8's `run_sweeper` was kept to re-publish only (every added line tested); folding retention into it is the
  documented optional follow-up. `reap()` belongs in the worker bootstrap, not the gateway sweeper.
- Phase 6 L4 e2e/behavior probes (the rung cards R3.1/R3.2/R3.3/R4.1/R4.2/R4.3 `not_started`): crash-recovery
  (docker kill mid-job → redeliver), duplicate-delivery, poison-pill (live DLQ-after-3), semaphore, cache.
  These are the L4 owners of behaviors the Phase-4 cards verified at L3.
- Phase 7 Infra verification: I1–I4, H-DANGLE.
- Phase 8 Architecture-defense docs: DOC1 (document the W7 DLQ policy), DOC2.

## Next Steps
1. **Phase 6 — L4 e2e probes** (needs `make e2e` + the full compose stack): build `tests/e2e/` for R3.1
   (docker kill mid-job → redeliver, no loss), R3.2 (duplicate delivery → exactly-once effect), R3.3 (poison
   → DLQ-after-3, healthy traffic unblocked — honor the R2.0 single-`VendorError` reconciliation), R4.1/4.2/4.3.
   The worker handlers are L3-proven; e2e wires them through the live broker under fault injection.
2. **Optional reaper scheduling:** wire `Semaphore.reap()` into the worker bootstrap and (optionally) fold
   `purge_processed_events()` into `run_sweeper` as a second periodic chore — both primitives exist + are
   tested; only the scheduling glue remains.
3. **DOC1 (Phase 8):** write up the W7 DLQ resolver policy (partial-drama: FAILED block + barrier resolves;
   alternative hard-fail). The decision is already implemented + recorded in DECISIONS 2026-06-25 "Phase 4".

## Known Issues / Gaps
- **Arch review 2026-06-24:** 13 hardening holes; full traces in `tmp/ARCH-REVIEW-2026-06-24.md` and `BACKLOG.md`. The FEATURES.md card spine folds every fix into its owning card — do NOT build without those constraints.
- Worker pipeline body: **complete** (Phase 4). `worker/main.py` runs a real consume loop; `handlers/
  {parse,tts,stitch,dlq}.py` + `bootstrap.py`/`dispatch.py`/`errors.py`/`ssrf.py` are all wired and L3-tested.
  The parse handler wraps `simulate_parse` with `await asyncio.sleep(0)` (latency stand-in; fault logic stays
  pure in core/domain).
- **Reaper/retention still not scheduled:** the gateway PENDING-sweeper (G8) IS now scheduled (asyncio.Task
  in the lifespan), but `build_context` still calls `Semaphore.reap()` / `purge_processed_events()` nowhere
  periodically — they remain tested primitives awaiting a scheduler (reap in the worker bootstrap; purge
  optionally folded into `run_sweeper`). G8 deliberately re-publishes only, so every added line stayed tested.
- **Known edge (W7/B4):** `complete_task_and_decrement` guards only `status != 'DONE'`; a DLQ-failed task
  followed by a late TTS success could double-decrement (harmless — StitchReady already fired). The realistic
  flow can't hit it; left B4 untouched rather than refactor a passing card. See DECISIONS 2026-06-25 "Phase 4".
- `core/infra/redis.py`: **complete** (R1/R2/X4/X5/R3/H8 + `seen_once`). `db.py` has `mark_event` +
  `purge_processed_events` (R4inbox). `core/infra/queries.py`: **complete** (B4/H15/B6 + Phase-4 additions
  `advance_status`, `fail_task_and_decrement`, `finalize_job`).
- `domain/text.py`, `domain/hash.py`: now exist (D3/D4 done).
- `domain/models.py`: removed (F0.3).
