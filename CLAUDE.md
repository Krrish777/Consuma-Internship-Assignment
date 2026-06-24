# CLAUDE.md — Consuma Audio Engine

Agent landing page. This is a **router**, not the spec. Read the linked docs on demand.
**Single source of truth for requirements & decisions: [`docs/SPEC.md`](docs/SPEC.md).**

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
- No "done" without runnable proof. A passing suite is the only evidence that counts.

## Session ritual (state lives in git, not in your head)
- **Clock in:** read `PROGRESS.md` (+ `docs/DECISIONS.md` if touching design); run `make check` to
  confirm the repo is in a consistent state; continue from PROGRESS "Next Steps".
- **Clock out:** update `PROGRESS.md`; log any new design choice in `docs/DECISIONS.md`; run
  `make check`; commit each atomic unit of completed work (one logical change = one commit).

## Work rules (WIP = 1)
- Work on **one** feature at a time. Start the next only after the current one **passes its
  verification** (the `verify` command in `feature_list.json`). Enforced by `check-wip.py`.
- Don't "also refactor" B while implementing A. No starting many things and finishing none.
- "Done" = behavior verification passes, never "the code looks fine".

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

## State placement (golden rule)
Postgres = durable truth · Redis = ephemeral coordination · MinIO = bytes · broker = pointers.
If durable truth ends up in Redis, or coordination in Postgres, it is misplaced.

## Git
Never commit/push without explicit permission. Never add a Claude co-author trailer.
