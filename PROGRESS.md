# PROGRESS — Consuma Audio Engine

> Durable cross-session state. Read at session start; update before session end.
> Structured feature status lives in `feature_list.json` (see CLAUDE.md). Decisions: `docs/DECISIONS.md` + `docs/SPEC.md §4, §6`.

## Current State
- Latest commit: cb5cdb9 (core, gateway, worker scaffold) — initialization work below is UNCOMMITTED
- Phase: **harness setup** (walking the harness-engineering notes, 1 at a time) — notes 1–7 done
- Source: domain/infra/handler modules still STUBs; gateway/worker have Rung-0 boot only
  (gateway GET /health, worker connect-and-idle)
- Verify: `make check` (no-Docker gates) **GREEN** — ruff + mypy --strict + 1 unit test pass
- Docker stack: docker-compose.yml + Dockerfiles + init.sh **authored, UNVERIFIED here** (no Docker daemon)

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
- [x] FIXED (note 10): check-evidence.py (evidence gate) + verify-before-commit.py (commit gate)
  rewritten self-contained and verified — were dead/false-blocking inherited AMRIT artifacts.
- Docker bring-up unverified: `make dev` / `./init.sh` / `make test-int` need Docker Desktop (verify later)
- minio healthcheck (curl-based) may need adjustment if the image lacks curl — flagged in compose
- No integration/e2e/behavior tests yet (notes 10–11)

## Next Steps
1. Note 10 — rewrite check-evidence.py + verify-before-commit.py (self-contained, match schema); pass-state gating
2. Note 11 — E2E/behavior test layer (the `make e2e` suite referenced in feature_list.json)
3. Verify Docker stack once Docker Desktop is installed (`./init.sh`)
4. Begin pipeline implementation against the feature ladder (Rung 1+, R0.1 in_progress), TDD per note 11
