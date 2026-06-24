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
- [ ] Harness setup walkthrough — notes 1–11 done; next note 12 (Architectural Boundaries)
- Note 11: test-layer scaffolding done — pytest markers (integration/e2e), tests/e2e pkg,
  conftest.py auto-skips Docker tests when no daemon; validation hierarchy in CLAUDE.md. Bodies = per-rung TDD.

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
