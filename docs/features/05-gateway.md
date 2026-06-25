# Phase 5 — Gateway completion

> The gateway head is built (`/health`, `POST /jobs`, `GET /status`). Three cards remain: the stats
> endpoint, the PENDING-sweeper that closes the gateway's dual-write seam, and the ingestion size guard.
>
> **Verified stack:** FastAPI **0.136.1** — use the `lifespan` context manager (the existing gateway
> already does); `@app.on_event` is deprecated. The lifespan is also where a background sweeper task
> is launched and cancelled.

---

### G7 — `GET /stats` observability endpoint   [rung R5.1] [BOM: 07-G7] [scores: observability]
depends_on: B6
files: modify `services/gateway/src/gateway/main.py`, `gateway/schemas.py`; `tests/integration/test_stats.py`
context: R5.1 — the runtime view of the system: counts of jobs by status (and optionally queue depths).
`job_id` is the trace key across logs; `/stats` is the aggregate companion. Read-only.
reuse: existing gateway route + `app.state` wiring; B6's `job_counts_by_status`.
api: FastAPI route returning a pydantic `StatsResponse`; uses the db session from `app.state`.
steps:
  1. `StatsResponse{jobs: dict[str,int], (optional) queues: dict[str,int]}`.
  2. `GET /stats` → call B6's grouped query; optionally read RabbitMQ queue depths via the management
     API or `queue.declare(passive=True)` message counts (keep it simple — job counts are the core).
MUST: be read-only (no locks/writes); carry `job_id` is N/A here but keep structured logging.
MUST NOT: scan all rows in Python (use the SQL aggregate from B6).
verify: [L3] `uv run pytest tests/integration -k stats` — seed mixed-status jobs → `/stats` returns
  correct per-status counts.
accept: `/stats` returns accurate aggregate counts.
evidence:

---

### G8 — PENDING-sweeper / reconciler (closes the dual-write seam, H1)   [rung R3.4] [BOM: 07-G8] [scores: state ⭐]
depends_on: W3
files: modify `gateway/main.py` (launch task in lifespan); create `gateway/sweeper.py`; `tests/integration/test_sweeper.py`
context: **The gateway's own consistency gap.** `POST /jobs` does MinIO → DB commit → publish — but
commit-then-publish is not atomic. A crash between them leaves an **orphaned PENDING job whose
`JobCreated` was never published** → it never progresses. "Ack last" is a *consumer* rule and cannot
cover the *producer*. Fix: a periodic sweeper that re-publishes `JobCreated` for any job stuck in
PENDING past a generous timeout. **The Job row is its own outbox**, and idempotent parse (H2: ON
CONFLICT + always-republish) makes re-publishing safe.
reuse: from scratch (this is the outbox-via-state pattern).
api: launched as an `asyncio.Task` in the FastAPI lifespan; `select(Job).where(Job.status==PENDING,
  Job.created_at < now()-PENDING_TIMEOUT_S)`; `broker.publish(exchange, JobCreated(job_id), Q_PARSE)`.
steps:
  1. `async def sweep_once(ctx) -> int:` — find stale PENDING jobs, re-publish `JobCreated` for each, return count.
  2. Loop every `SWEEP_INTERVAL_S` in a lifespan-managed task; cancel cleanly on shutdown.
  3. (Optional) fold in H10 `processed_events` retention here as a second periodic chore.
MUST: re-publish is safe **only because** parse is idempotent and re-publishable (H2) — do not "fix"
  this by making the gateway transactional with the broker (it can't be).
MUST: use a generous timeout (≫ normal parse latency) so the sweeper never races a healthy in-flight job.
MUST NOT: change job status in the sweeper — it only re-publishes; the consumer advances the state.
verify: [L3] `uv run pytest tests/integration -k sweeper` — insert `Job(PENDING)` with **no** event
  published → after one `sweep_once`, `JobCreated` is on `q.parse` and (with a worker) the job progresses.
accept: orphaned PENDING jobs get re-driven; healthy jobs untouched.
evidence:

---

### H13 — Manuscript max-size guard at ingestion   [rung R2.2] [BOM: backlog-H13] [scores: reliability]
depends_on: —
files: modify `services/gateway/src/gateway/main.py` (POST /jobs), extend `tests/integration/test_ingestion.py`
context: H13 — an unbounded manuscript body buffered in gateway memory is a DoS vector. Enforce
`MAX_MANUSCRIPT_BYTES` (F0.2): reject oversized bodies with a clean `413`, or stream to MinIO. Keep the
error machine-readable (R2.2c error contract).
reuse: existing ingestion handler + error handlers.
api: check `Content-Length` / measure the body; raise an HTTPException(413) with structured JSON.
steps:
  1. Before `put_text`, validate size against `MAX_MANUSCRIPT_BYTES`; on exceed → `413` JSON body.
  2. Keep the dual-write order intact for the accepted path (MinIO → DB commit → publish).
MUST: reject oversized input cleanly (413 JSON), not by OOM-ing the process (H13).
MUST: preserve the load-bearing ingestion order for accepted requests.
MUST NOT: read an unbounded body fully into memory before checking size.
verify: [L3] `uv run pytest tests/integration -k ingestion` — a body over the cap → 413 structured JSON;
  a normal body → 202 as before.
accept: oversized manuscripts rejected; normal path unchanged.
evidence:
