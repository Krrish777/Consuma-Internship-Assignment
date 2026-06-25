# Phase 7 — Infra verification & hygiene

> The compose stack, Dockerfiles, `init.sh`, and Makefile were authored but are marked "unverified" in
> the BOM. These cards prove the harness actually works on a clean machine and close the last two S3
> hygiene holes. They are mostly verification + small fixes, not new subsystems.

---

### I1 — Compose stack: 6 services healthy via `init.sh`   [rung R0.3] [BOM: 10-I1] [scores: reliability]
depends_on: —
files: verify/modify `docker-compose.yml`, `init.sh`
context: `./init.sh` must bring up all six services (postgres, rabbitmq, redis, minio, gateway, worker)
and have every healthcheck pass on a clean checkout. The MinIO healthcheck was flagged as possibly
fragile (image may lack `curl`). Confirm or fix each healthcheck.
reuse: existing compose + init.sh.
steps:
  1. Run `./init.sh` on a clean tree; watch each healthcheck. Fix any flake (e.g. MinIO health → use
     `mc ready` / the `/minio/health/live` endpoint with a tool present in the image).
  2. Confirm `RABBITMQ_ERLANG_COOKIE` is set (Windows fix already present) and volumes persist.
MUST: all six services reach healthy; `init.sh` exits 0.
MUST NOT: rely on a binary the image doesn't ship for a healthcheck.
verify: [L4] `./init.sh` → "all services healthy", exit 0 (already R0.3 evidence — re-confirm after changes).
accept: clean-tree bring-up is green and deterministic.
evidence:

---

### I2 — Dockerfiles build (gateway + worker)   [rung R0.3] [BOM: 10-I2] [scores: reliability]
depends_on: —
files: verify `services/gateway/Dockerfile`, `services/worker/Dockerfile`
context: Both images must build from the uv workspace and run their entrypoints (gateway = uvicorn app;
worker = the consume loop). Confirm the build context includes `packages/core` (workspace member) and
that `uv sync` resolves inside the image.
steps:
  1. `docker compose build gateway worker`; run each; confirm gateway serves `/health` and worker logs
     "connected" then begins consuming (post-Phase-4).
MUST: images build reproducibly and include the `core` workspace package.
verify: [L4] `docker compose build` succeeds; containers start and pass their compose healthchecks.
accept: both app images build and run.
evidence:

---

### I3 — `init.sh` end-to-end smoke   [rung R0.3] [BOM: 10-I3] [scores: reliability]
depends_on: I1, I2
files: verify `init.sh`
context: After bring-up, `init.sh` runs the unit suite and prints the gateway/RabbitMQ/MinIO URLs. Once
the pipeline is built, extend the smoke to submit one job and confirm it COMPLETES (a tiny end-to-end
sanity beyond unit tests).
steps:
  1. Keep the existing `pytest tests/unit` gate.
  2. Optionally append a one-job submit-and-poll smoke (guarded so it's skippable).
MUST: `init.sh` remains the single standard startup path (CLAUDE.md clock-out condition).
verify: [L4] `./init.sh` → unit tests pass + (optional) one job completes.
accept: one-command bring-up + sanity is green.
evidence:

---

### I4 — Worker scaling exercises the global semaphore   [rung R4.1] [BOM: 10-I4] [scores: reliability]
depends_on: R4.1
files: verify `docker-compose.yml` (worker has no host port; is scalable)
context: `docker compose up --scale worker=4` must start 4 workers that share the **one** global Redis
semaphore (proving Constraint A is global, not per-process). This is the deployment shape the R4.1 probe
relies on.
steps:
  1. Confirm the worker service binds no fixed host port (so it scales) and all replicas connect to the
     same Redis/RabbitMQ/Postgres.
  2. Run R4.1 against `--scale worker=4`.
MUST: scaled workers share one semaphore (global limit holds across replicas).
verify: [L4] `make e2e -k semaphore` with 4 workers (same as R4.1).
accept: 4 replicas, one shared limit.
evidence:

---

### H-DANGLE — MinIO object lifetime ≥ cache TTL   [rung R4.2] [BOM: backlog-H-DANGLE] [scores: reliability]
depends_on: R3
files: doc/verify in `core/infra/storage.py` + `core/config.py`; `tests/integration/test_storage.py`
context: H-DANGLE — if a `tts:cache:<hash>` entry outlives its MinIO object, a cache HIT returns a
**dangling key** → a download 404 mid-pipeline. Keep the object lifetime ≥ `CACHE_TTL_S`. Simplest
correct policy: **never expire `tts/` objects** (or expire them strictly longer than the cache), and
document it.
steps:
  1. Confirm no lifecycle rule prunes `tts/` objects sooner than `CACHE_TTL_S`.
  2. Document the invariant in `storage.py` and DOC2.
MUST: object TTL ≥ cache TTL (H-DANGLE) — a HIT must always resolve to a live object.
verify: [L3] a test that sets a cache entry, confirms the object still exists at cache-expiry boundary
  (or asserts the documented "objects don't expire" policy).
accept: no dangling-key window.
evidence:

---

### H-PREFETCH — Prefetch sizing verification   [rung R3.1] [BOM: backlog-H-PREFETCH] [scores: reliability]
depends_on: W1
files: verify `core/config.py` (`PREFETCH`) + per-queue QoS in the worker
context: H-PREFETCH — global `PREFETCH=16` against 3 TTS slots parks 13+ messages unacked on a blocked
`BLPOP` per worker, inflating the crash blast radius and creating back-pressure. Size `q.tts` prefetch
near serviceable concurrency.
steps:
  1. Set `q.tts` consumer prefetch ≈ semaphore size + small headroom (not 16).
  2. Document why parse/stitch may keep a larger prefetch than tts.
MUST: prefetch for `q.tts` sized near concurrency (H-PREFETCH).
verify: [L4] re-run R3.1 and confirm bounded redelivery; [L1] config asserted in a unit test.
accept: small unacked backlog per worker; bounded crash redelivery.
evidence:
