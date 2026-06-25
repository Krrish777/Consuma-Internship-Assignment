# PROGRESS — Consuma Audio Engine

> Durable cross-session state. Read at session start; update before session end.
> Structured feature status lives in `feature_list.json` (see CLAUDE.md). Decisions: `docs/DECISIONS.md` + `docs/SPEC.md §4, §6`.

## Current State
- Phase: **Phase 0 — Foundation & reconciliation** (FEATURES.md / docs/features/00-foundation.md).
- Active card: **F0.1** (reconcile stale feature_list.json + refresh PROGRESS.md).
- `make check` (L1 static + L2 unit): **GREEN** — ruff + mypy --strict (38 files) + 37 unit tests.
- Integration tests: fixed conftest.py Ryuk issue (set `TESTCONTAINERS_RYUK_DISABLED=true`); rerun pending.

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

### Tests (43 unit + integration)
- `tests/unit/` — 37 tests (architecture, error_handlers, events, health, logging, schemas, smoke, state_machine)
- `tests/integration/test_ingestion.py` — 7 tests (lifespan, POST /jobs ×4, GET /status ×2)
- `tests/integration/test_broker.py` — broker round-trip + manual-ack-redelivery (testcontainers RabbitMQ)
- `tests/integration/test_models.py` — Job CRUD, Task constraints, ProcessedEvent ON CONFLICT dedup
- `tests/integration/test_storage.py` — MinIO: idempotent bucket, text/bytes roundtrip, list_prefix, key_exists

## In Progress
- **F0.1** — reconcile feature_list.json + refresh PROGRESS.md (this card)
  - R2.0 corrected back to `not_started` (was erroneously left `in_progress`)
  - conftest.py Ryuk fix applied
  - Awaiting integration test green + commit hash to earn `passing`

## What's Genuinely Unbuilt (FEATURES.md scope)
The entire Phase 1–8 roadmap is unbuilt:
- Phase 1 Domain pure logic: fault injection (R2.0), text parser (D3/D4), FSM hardening (H-FSM)
- Phase 2 Redis coordination: semaphore (R4.1), content cache (R4.2), idempotency inbox (R3.2)
- Phase 3 DB query layer: fan-in atomic UPDATE (B4), sweeper counter (H15), stats queries (B6)
- Phase 4 Worker pipeline: parse/TTS/stitch handlers (X1–X7, W1–W7) — broker topology exists but handlers are stubs
- Phase 5 Gateway completion: /stats (G7/B6), PENDING-sweeper (G8/R3.4)
- Phase 6 L4 e2e/behavior probes: crash-recovery, duplicate-delivery, poison-pill, semaphore, cache tests
- Phase 7 Infra verification: I1–I4, H-DANGLE, H-PREFETCH
- Phase 8 Architecture-defense docs: DOC1, DOC2

## Next Steps (after F0.1 earns passing)
1. **F0.2** — Add missing config knobs (MAX_MANUSCRIPT_BYTES, MAX_BLOCKS, WEBHOOK_ALLOWLIST, LEASE_TTL_S, CACHE_TTL_S, PROCESSED_EVENTS_RETENTION_S, SWEEP_INTERVAL_S, PENDING_TIMEOUT_S) to core/config.py.
2. **F0.3** — Remove dead domain/models.py stub.
3. **F0.4** — Document retry-counter source-of-truth decision (H-XDEATH) in DECISIONS.md.
4. Then Phase 1: R2.0 (fault injection) → D3/D4 (text parser) → H-FSM (FSM CAS hardening).

## Known Issues / Gaps
- **Arch review 2026-06-24:** 13 hardening holes; full traces in `tmp/ARCH-REVIEW-2026-06-24.md` and `BACKLOG.md`. The FEATURES.md card spine folds every fix into its owning card — do NOT build without those constraints.
- Worker pipeline body: worker/main.py is an idle skeleton (connects + idles); no message handlers yet.
- `core/infra/redis.py`: does not exist yet (Phase 2 scope).
- `domain/text.py`: does not exist yet (Phase 1 / D3-D4 scope).
- `domain/models.py`: dead stub (F0.3 removes it).
