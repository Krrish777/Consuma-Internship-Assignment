# PROGRESS — Consuma Audio Engine

> Durable cross-session state. Read at session start; update before session end.
> Structured feature status lives in `feature_list.json` (see CLAUDE.md). Decisions: `docs/DECISIONS.md` + `docs/SPEC.md §4, §6`.

## Current State
- Phase: **Phase 1 — Domain pure logic** (FEATURES.md / docs/features/01-domain.md). Phase 0 complete.
- Active card: **none** — D3, D4, H-FSM, R2.0 all `passing`; WIP=0.
- `make check` (L1 static + L2 unit): **GREEN** — ruff + ruff format + mypy --strict (43 files) + 82 unit tests.
- Integration tests: conftest.py Ryuk fix in place (`TESTCONTAINERS_RYUK_DISABLED=true`); not re-run this session (pure-domain cards only, no Docker needed).
- **R2.0 divergence RESOLVED** (docs/DECISIONS.md 2026-06-25 R2.0): SPEC §1 wins — poison is a
  *consistently-failing* manuscript → DLQ after 3 retries via a SINGLE retryable `VendorError` (the
  card's non-retryable `PoisonError`/`_sim.py` wording contradicted SPEC §1 + R3.3 and was re-scoped).
  vendor.py stays in `core/domain` and now composes D3 `split_blocks` + D4 `content_hash` (duplicate
  per-line splitter + inline sha256 removed).

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

### Tests (43 unit + integration)
- `tests/unit/` — 37 tests (architecture, error_handlers, events, health, logging, schemas, smoke, state_machine)
- `tests/integration/test_ingestion.py` — 7 tests (lifespan, POST /jobs ×4, GET /status ×2)
- `tests/integration/test_broker.py` — broker round-trip + manual-ack-redelivery (testcontainers RabbitMQ)
- `tests/integration/test_models.py` — Job CRUD, Task constraints, ProcessedEvent ON CONFLICT dedup
- `tests/integration/test_storage.py` — MinIO: idempotent bucket, text/bytes roundtrip, list_prefix, key_exists

## In Progress
- **none** — WIP=0. Phase 1 domain cards complete. Next pick is the R2.0 decision (below) or Phase 2 Redis.

## What's Genuinely Unbuilt (FEATURES.md scope)
Phase 1 domain logic is now built (D3/D4/H-FSM; R2.0 partial — see Known Issues). Remaining:
- Phase 2 Redis coordination: semaphore (R4.1), content cache (R4.2), idempotency inbox (R3.2)
- Phase 3 DB query layer: fan-in atomic UPDATE (B4), sweeper counter (H15), stats queries (B6)
- Phase 4 Worker pipeline: parse/TTS/stitch handlers (X1–X7, W1–W7) — broker topology exists but handlers are stubs
- Phase 5 Gateway completion: /stats (G7/B6), PENDING-sweeper (G8/R3.4)
- Phase 6 L4 e2e/behavior probes: crash-recovery, duplicate-delivery, poison-pill, semaphore, cache tests
- Phase 7 Infra verification: I1–I4, H-DANGLE, H-PREFETCH
- Phase 8 Architecture-defense docs: DOC1, DOC2

## Next Steps
1. **Phase 2 — Redis coordination** (docs/features/02-redis.md): semaphore (R4.1), content cache (R4.2),
   idempotency inbox (R3.2). First card needs `core/infra/redis.py` (does not exist yet). These are L3
   (testcontainers) cards — require a Docker daemon, unlike the pure-L2 Phase-1 cards just finished.
2. **When R3.3 (DLQ e2e) is built**, honor the R2.0 reconciliation: poison routes through the retry
   ladder and dead-letters after 3 attempts (single `VendorError`), per SPEC §1 / DECISIONS 2026-06-25.

## Known Issues / Gaps
- **Arch review 2026-06-24:** 13 hardening holes; full traces in `tmp/ARCH-REVIEW-2026-06-24.md` and `BACKLOG.md`. The FEATURES.md card spine folds every fix into its owning card — do NOT build without those constraints.
- Worker pipeline body: worker/main.py is an idle skeleton; handlers/{parse,tts,stitch}.py are STUBS
  (docstring-only, no logic wired). The parse handler will wrap `core.domain.vendor.simulate_parse`
  with `asyncio.sleep` for latency (no separate `_sim.py` — fault logic stays pure in core/domain).
- `core/infra/redis.py`: does not exist yet (Phase 2 scope).
- `domain/text.py`, `domain/hash.py`: now exist (D3/D4 done).
- `domain/models.py`: removed (F0.3).
