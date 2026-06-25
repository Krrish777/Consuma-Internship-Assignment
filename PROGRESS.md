# PROGRESS — Consuma Audio Engine

> Durable cross-session state. Read at session start; update before session end.
> Structured feature status lives in `feature_list.json` (see CLAUDE.md). Decisions: `docs/DECISIONS.md` + `docs/SPEC.md §4, §6`.

> **⚡ SESSION-RESET PLAN (read FIRST on 2026-06-25 after limit reset):** the human prepped working,
> tested code snippets during downtime. **Read `snippets/README.md` FIRST** — it's the full Bill of
> Materials: the whole project decomposed into 11 component folders (`snippets/01-config` … `11-tests`),
> each with bite-size snippet cards (target file, signature, MUST rules, acceptance test, gotcha). The
> human dropped `snippet.py` + `proof.txt` per card. `docs/SNIPPET-PREP.md` remains the *deep-dive* for the
> 9 hardest mechanisms (⭐⭐⭐). Then climb the Rung ladder: write the rung's test first, adapt the snippet,
> verify, record evidence. Highest-value: M2 retry ladder, R2 leased semaphore, B4 fan-in (all ⭐⭐⭐).

## Current State
- Phase: **implementation** — molding reference repos into our shape, 1 at a time, up the Rung ladder.
- First slice DONE (uncommitted): molded `base-aiopika-pattern` skeleton → broker adapter + event contracts.
  - `core/domain/events.py` — R1.4 contracts (JobCreated/TtsRequested/StitchReady, frozen, defaulted
    `event_id`, str-only). **Unit-green, no Docker.** 7 tests in `tests/unit/test_events.py`.
  - `core/infra/broker.py` — connect/declare_minimal/publish/consume molded to MUST rules
    (durable named `pipeline` exchange + `q.parse`, persistent msgs, **manual ack-LAST**, pointers-not-bytes).
  - `worker/main.py` rewired to the shared adapter (R0.2 boot now via `broker.connect`+`declare_minimal`).
  - `tests/integration/test_broker.py` — round-trip + manual-ack-redelivery proof (testcontainers RabbitMQ).
    Collects + **auto-skips without Docker**; runs once Docker is up. Added `testcontainers[rabbitmq]` dep (+pika).
- Verify: `make check` (no-Docker gates) **GREEN** — ruff + mypy --strict (28 files) + 13 unit tests.
- Docker stack: docker-compose.yml + Dockerfiles + init.sh authored, UNVERIFIED (no Docker daemon here yet —
  user will bring Docker up to earn R0.2 + the broker integration test).

## Completed
- [x] uv workspace scaffold: packages/core, services/{gateway,worker}
- [x] core/config.py — env settings w/ spec defaults
- [x] docs/SPEC.md — single source of truth (brief + rubric + mechanisms + §-map)
- [x] CLAUDE.md — instruction-file router (note 4) + session ritual (note 6)
- [x] PROGRESS.md + docs/DECISIONS.md — durable state + decision log (note 6)
- [x] Initialization phase (note 7): docker-compose.yml (6 svc), Dockerfiles, .dockerignore,
      Makefile (DoD runner), mypy --strict config, pytest config, .env.example, init.sh, smoke test
- [x] Rung-0 boot: gateway `/health`, worker connect-and-idle
- [x] .claude harness hooks: block-coauthor, verify-before-commit, check-line-cap, check-wip, check-evidence

## In Progress
- [x] Harness setup walkthrough — **all 14 notes done. Harness setup COMPLETE.**
- Note 13: observability sized to a single-dev harness — `job_id` trace-key convention in CLAUDE.md.
- Note 14: clean-state exit checklist in CLAUDE.md clock-out; ruff T10/T20 enforce "no debug code"
  mechanically; harness-is-living/simplify-periodically principle recorded.

## NEXT SESSION — implement via reference code (user directive 2026-06-24)
- Goal: pull the reference repos (SPEC §7) and **mold them into OUR codebase shape**, not copy wholesale.
  - `kieled/fastapi-aiopika-boilerplate` → bones for gateway↔aio-pika wiring (R0.2/R0.3, R2.1).
  - `py-redis-semaphore` (BLPOP token-list) → R4.1 leased semaphore. Brian Storti backoff → R3.3 DLQ ladder.
- Mold = adapt the PATTERN into core/infra adapters + worker handlers, honoring SPEC §3 boundaries
  (test_architecture.py will reject cross-layer imports) and CLAUDE.md MUST rules (no Celery, ack-last,
  pointers-not-bytes). Start at R0.1 (in_progress) → climb the Rung ladder, TDD per rung.
- Note 11: test-layer scaffolding — pytest markers (integration/e2e), tests/e2e pkg,
  conftest.py auto-skips Docker tests when no daemon; validation hierarchy in CLAUDE.md. Bodies = per-rung TDD.
- Note 12: tests/unit/test_architecture.py — mechanically enforces SPEC §3 boundaries
  (gateway⊥worker, domain purity/no-I/O, no banned orchestrator) inside `make check`.

## Known Issues / Gaps
- **⚠️ RELIABILITY BACKLOG (2026-06-24 arch review):** `BACKLOG.md` (root) lists 22 hardening items —
  5 are **S0 silent-corruption/stall** defects in the design as specified (H-XDEATH, H1, H2, H3, H4) that
  each fail a `kill -9` probe. Fold the linked fix into each rung as it's built; do NOT mark R2–R4
  `passing` until its linked items are addressed. Full traces + research citations:
  `tmp/ARCH-REVIEW-2026-06-24.md`. Several require **SPEC §4 changes** (it currently teaches the bugs).
- [x] FIXED (note 10): check-evidence.py (evidence gate) + verify-before-commit.py (commit gate)
  rewritten self-contained and verified — were dead/false-blocking inherited AMRIT artifacts.
- Docker bring-up unverified: `make dev` / `./init.sh` / `make test-int` need Docker Desktop (verify later)
- minio healthcheck (curl-based) may need adjustment if the image lacks curl — flagged in compose
- No integration/e2e/behavior tests yet (notes 10–11)

## Next Steps
1. **Commit the first slice** (needs user permission): events + broker + worker-wiring + integration test.
   Then flip `feature_list.json`: R0.1 → passing, R1.4 → passing (evidence = commit hash); R0.2 → blocked (Docker).
2. **Once Docker is up:** `uv run pytest -m integration -k broker` green + `docker compose up worker`
   logs "worker connected" → earn R0.2 passing.
3. Migrate the rest piece by piece (reference repo → rung), TDD per rung:
   - `retry-dlx-aiopika` → R2.1 full retry-ladder topology + R3.3 DLQ (extend `broker.declare_minimal`).
   - `fastapi-rmq-pg-glue` → R1.1 models + Alembic + R2.2 ingestion (POST /jobs).
   - `minio-sdk-examples` → R1.3 storage adapter. `redis-lock-semaphore` → R4.1 semaphore + R3.2 idempotency.
   - Native (no repo): R1.2 Job FSM, R2.3 parse fan-out, R4.2 cache+fan-in, R4.3 stitch+webhook, R5.1 /stats.
