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
- [ ] Harness setup walkthrough — notes 1–7 done; next note 8 (Scope & WIP=1)

## Known Issues / Gaps
- Docker bring-up unverified: `make dev` / `./init.sh` / `make test-int` need Docker Desktop (verify later)
- minio healthcheck (curl-based) may need adjustment if the image lacks curl — flagged in compose
- feature_list.json (structured WIP=1 ladder, from the Rung plan in docstrings) not yet created (note 9)
- No integration/e2e/behavior tests yet (notes 10–11)

## Next Steps
1. Note 8 — Scope & WIP=1 (verify check-wip.py hook covers it)
2. Note 9 — feature_list.json formalizing the Rung plan (Rung 0–5)
3. Verify Docker stack once Docker Desktop is installed (`./init.sh`)
4. Begin pipeline implementation against the feature ladder (Rung 1+), TDD per note 11
