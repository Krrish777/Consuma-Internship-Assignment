# PROGRESS — Consuma Audio Engine

> Durable cross-session state. Read at session start; update before session end.
> Structured feature status lives in `feature_list.json` (see CLAUDE.md). Decisions: `docs/DECISIONS.md` + `docs/SPEC.md §4, §6`.

## Current State
- Phase: **Phase 3 — DB query layer COMPLETE** (docs/features/03-db-queries.md fully exhausted).
  Phases 0–2 complete.
- Active card: **none** — all 3 DB BOM cards (B4, H15, B6) `passing`; WIP=0. (Phase 2: R1/R2/X4/X5/R3/H8/R4inbox.)
- `make check` (L1 static + L2 unit): **GREEN** — ruff + ruff format + mypy --strict (45 files) + 82 unit tests.
- Integration tests: **RE-RUN this session with Docker up** — `pytest tests/integration -k models`
  → 13 passed (real postgres:17-alpine; Ryuk disabled via conftest.py). The 7 new query-layer tests
  (3 B4 fan_in + 3 H15 counter_once + 1 B6 stats) are L3 and gate the Phase-3 cards' `passing` state.
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

### Tests (43 unit + integration)
- `tests/unit/` — 37 tests (architecture, error_handlers, events, health, logging, schemas, smoke, state_machine)
- `tests/integration/test_ingestion.py` — 7 tests (lifespan, POST /jobs ×4, GET /status ×2)
- `tests/integration/test_broker.py` — broker round-trip + manual-ack-redelivery (testcontainers RabbitMQ)
- `tests/integration/test_models.py` — Job CRUD, Task constraints, ProcessedEvent ON CONFLICT dedup
- `tests/integration/test_storage.py` — MinIO: idempotent bucket, text/bytes roundtrip, list_prefix, key_exists

## In Progress
- **none** — WIP=0. Phase 2 Redis cards complete (all 7 passing). Next phase is the worker pipeline.

## What's Genuinely Unbuilt (FEATURES.md scope)
Phases 0–3 built (foundation, domain logic, Redis coordination, DB query layer). The infra adapters AND
the atomic query ops are now complete (db, broker, storage, redis + `queries.py` fan-in/counter/stats).
Remaining is the pipeline that composes them:
- Phase 4 Worker pipeline: parse/TTS/stitch handlers (X1–X7, W1–W7) — broker topology + all adapters exist,
  but `worker/handlers/{parse,tts,stitch}.py` are still STUBS. This is where redis.Semaphore/Cache/seen_once,
  db.mark_event, and core.domain.vendor finally get wired into the choreography.
- Phase 5 Gateway completion: /stats (G7/B6), PENDING-sweeper (G8/R3.4)
- Phase 6 L4 e2e/behavior probes: crash-recovery, duplicate-delivery, poison-pill, semaphore, cache tests
- Phase 7 Infra verification: I1–I4, H-DANGLE, H-PREFETCH
- Phase 8 Architecture-defense docs: DOC1, DOC2

## Next Steps
1. **Phase 4 — Worker pipeline** (docs/features/04-worker.md): wire the now-complete adapters + `queries.py`
   into the parse→TTS→stitch choreography. Build order by dependency: **X3** bootstrap (`worker/bootstrap.py`:
   one wired context; call `ensure_slots()` once) → **X2** dispatch table (queue→handler) → **X1** run loop
   (replace the idle `await asyncio.Future()`; clean SIGTERM) → then the handlers **W3 parse** (split_blocks →
   `begin_parse` H15 → write N tasks → fan-out TtsRequested), **W4 tts** (`Cache.cache_get` BEFORE
   `Semaphore.acquire` → vendor → MinIO → `cache_set` → `complete_task_and_decrement` B4 → emit StitchReady on
   0), **W?/R4.3 stitch** (concat → COMPLETED → webhook). Invariants: ack LAST; consumers via `mark_event`
   (authority) + `seen_once` (fast-path); NEVER inbox-skip parse (H2); 0-block job → STITCHING directly.
2. **When R3.3 (DLQ e2e) is built**, honor the R2.0 reconciliation: poison routes through the retry ladder and
   dead-letters after 3 attempts (single `VendorError`), per SPEC §1 / DECISIONS 2026-06-25.
3. **When the TTS handler / X3 worker bootstrap is built**, call `Semaphore.ensure_slots()` once on boot and
   run `Semaphore.reap()` periodically (the reaper isn't self-scheduling — X5 provides the primitive only).

## Known Issues / Gaps
- **Arch review 2026-06-24:** 13 hardening holes; full traces in `tmp/ARCH-REVIEW-2026-06-24.md` and `BACKLOG.md`. The FEATURES.md card spine folds every fix into its owning card — do NOT build without those constraints.
- Worker pipeline body: worker/main.py is an idle skeleton; handlers/{parse,tts,stitch}.py are STUBS
  (docstring-only, no logic wired). The parse handler will wrap `core.domain.vendor.simulate_parse`
  with `asyncio.sleep` for latency (no separate `_sim.py` — fault logic stays pure in core/domain).
- `core/infra/redis.py`: **complete** (R1/R2/X4/X5/R3/H8 + `seen_once`). `db.py` gained `mark_event` +
  `purge_processed_events` (R4inbox). `core/infra/queries.py`: **now complete** (B4
  `complete_task_and_decrement`, H15 `begin_parse`, B6 `job_counts_by_status`) — the Phase-4 handlers call
  these. The reaper (`Semaphore.reap`) and retention (`purge_processed_events`) are primitives — a
  scheduler must call them (worker bootstrap / sweeper, Phase 4/5).
- `domain/text.py`, `domain/hash.py`: now exist (D3/D4 done).
- `domain/models.py`: removed (F0.3).
