# CLAUDE.md — Consuma Audio Engine

Agent landing page. This is a **router**, not the spec. Read the linked docs on demand.
**Single source of truth for requirements & decisions: [`docs/SPEC.md`](docs/SPEC.md).**

> **After compaction / new session:** read [`.claude/BRIEFING.md`](.claude/BRIEFING.md) first — it
> contains persona, the 8 MUSTs, methodology, current card state, and the immediate next steps.
> Then read [`.remember/remember.md`](.remember/remember.md) for the latest session handoff.

## What this is
Core async engine: text manuscript → simulated produced audio drama, via **choreographed**
microservices (no central orchestrator). It is a distributed-systems reliability test — the
grade is in how failure is handled, not the happy path. See `docs/SPEC.md` §1–§2.

## Stack
Python 3.13 · uv workspace · FastAPI (gateway) · aio-pika + RabbitMQ · SQLAlchemy 2.0 (async)
+ asyncpg + Alembic (Postgres) · redis-py 8 (`redis.asyncio`) · MinIO · pydantic 2 /
pydantic-settings. Tests: pytest + pytest-asyncio + testcontainers + httpx. Lint: ruff.
Types: mypy --strict.

## Layout
- `packages/core` — shared lib. `domain/` = PURE logic, no I/O (unit-testable without Docker);
  `infra/` = swappable adapters (`db`, `redis`, `broker`, `storage`).
- `services/gateway` — FastAPI ingestion. Depends on `core`, **never** on `worker`.
- `services/worker` — aio-pika consumer running the pipeline. Depends on `core`, **never** on `gateway`.
- `docs/SPEC.md` — requirements truth. `PROGRESS.md` — current work state (read at session start).

## Run  (targets are part of harness setup — see PROGRESS.md for build status)
- `./init.sh` — one-shot: bring up the 6-service docker-compose stack + wait for health.
- `docker compose up --build` — stack only. Scale workers: `docker compose up --scale worker=4`.

## Verify — Definition of Done (MUST pass all before claiming "done")
- `make check` — gates runnable **without Docker**: `ruff check` + `ruff format --check` →
  `mypy --strict` → `pytest tests/unit`.
- `make check-all` — full DoD: adds `make test-int` (testcontainers) + `make e2e`
  (docker-kill / poison-pill / duplicate) + behavior/functional tests. **Needs a Docker daemon.**
- Validation hierarchy (note 11): L1 static (ruff+mypy) → L2 unit → L3 integration → L4 e2e.
  A lower level failing blocks the higher ones; **skipping a required level = not complete**.
  Any cross-component change (broker/DB/Redis/MinIO interplay) MUST pass e2e before `passing`.
- No "done" without runnable proof. A passing suite is the only evidence that counts.

## Session ritual (state lives in git, not in your head)
- **Clock in:** read `PROGRESS.md` (+ `docs/DECISIONS.md` if touching design); run `make check` to
  confirm the repo is in a consistent state; continue from PROGRESS "Next Steps".
- **Clock out (clean state is a completion condition):** `make check` green · `feature_list.json`
  updated · no debug code left (no `print`/`breakpoint` — enforced by ruff) · standard startup path
  (`./init.sh` / `make dev`) intact · update `PROGRESS.md` + `docs/DECISIONS.md` · commit each
  atomic unit (one logical change = one commit). Don't leave mess for "next time" — entropy compounds.
- The harness is living, not fixed: each rule patches a model limitation. Periodically simplify it
  as models improve (note 14) — delete scaffolding that's become pure overhead.

## Work rules (WIP = 1) & feature list
- Scope surface: `feature_list.json` (root). It is the single source of "what's done" — the
  Rung ladder R0→R5. Read it to pick the next task; don't contradict it from memory.
- Work on **one** feature at a time (exactly one `in_progress`; enforced by `check-wip.py`).
  Start the next only after the current one **passes its `verification`**.
- Don't "also refactor" B while implementing A. No starting many things and finishing none.
- "Done" = the feature's `verification` command runs green AND `evidence` records the proof
  (commit hash). Never hand-edit a feature to `passing` — pass-state is earned, not declared.

## Hard constraints (MUST / MUST NOT)
- **MUST NOT** use a managed orchestrator (Temporal/Airflow/Step Functions/**Celery**). Raw
  broker choreography only. Seeing `@app.task` / `@shared_task` / `Flower` = wrong path.
- **MUST NOT** put payload bytes in a broker message — messages carry pointers/keys only.
- **MUST** ack the broker message **last**: do work → COMMIT Postgres → PUBLISH next → ACK.
- **MUST** do the fan-in join with atomic `UPDATE ... RETURNING`, never a Python counter.
- **MUST** enforce the 3-concurrent TTS limit via a **Redis** semaphore (leased w/ TTL),
  never `asyncio.Semaphore`. Check the content cache **before** acquiring a slot.
- **MUST** route poison pills to a DLQ after 3 retries (exp backoff 1/4/16s) **off** the hot
  queue — no head-of-line blocking.
- **MUST** keep `core/domain` free of I/O. **MUST** keep gateway and worker mutually independent.
- Webhook/notification failure **MUST NOT** fail the job — it is still `COMPLETED`.

## Observability (job_id is the trace key)
- Every log line carries `job_id` (and `task_id` in TTS) so one job is followable across
  gateway → broker → worker → DB, like a distributed trace. Emit structured logs.
- `GET /stats` (R5.1) is the runtime view; RabbitMQ UI :15672 + MinIO console :9001 for infra.
- E2E/behavior tests may assert on these signals (e.g. "after docker kill, job redelivered").

## State placement (golden rule)
Postgres = durable truth · Redis = ephemeral coordination · MinIO = bytes · broker = pointers.
If durable truth ends up in Redis, or coordination in Postgres, it is misplaced.

## Git
Never commit/push without explicit permission. Never add a Claude co-author trailer.
