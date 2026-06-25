# DECISIONS — append-only log

> Dated record of decisions made **during the work**. Format: what · why · rejected · dimension.
> Spec-mandated architecture invariants live in `SPEC.md §4`; this log is for choices made as we go.
> Newest at the bottom. Never rewrite history — supersede with a new dated entry.

---

### 2026-06-24 · Single source of truth = `docs/SPEC.md`
- **What:** Folded the original assignment brief + curated essentials from the author's `tmp/00-07`
  notes into one tracked file; refer only to it.
- **Why:** The code cited a `spec §5–§10` that had vanished from the tree; `tmp/00-07` are personal,
  verbose, untracked notes.
- **Rejected:** Referencing `tmp/` directly (unstable, not the source of truth, bloats context).

### 2026-06-24 · Project instructions in repo-root `CLAUDE.md` (router, not encyclopedia)
- **What:** A ~40-line landing page pointing to `docs/SPEC.md`, with run/verify commands + MUST rules.
- **Why:** Claude Code auto-loads `CLAUDE.md`; keeps the entry file lean (note 4/5).
- **Rejected:** `AGENTS.md` (chosen `CLAUDE.md` for auto-load); a single large instruction file (bloat).

### 2026-06-24 · Type checker = `mypy --strict`
- **What:** Added as a Definition-of-Done gate.
- **Why:** Idiomatic for async SQLAlchemy 2.0 / pydantic; both ship mypy plugins.
- **Rejected:** pyright/basedpyright (Node dependency; less idiomatic in a pure-Python uv toolchain).

### 2026-06-24 · Durable state split: `PROGRESS.md` (git-tracked) + `.remember/` (ephemeral)
- **What:** `PROGRESS.md` at root is the durable cross-session handoff; `.remember/` stays the live scratch.
- **Why:** Note 6 durability — handoff must be git-tracked, not in-head or gitignored. Opus 4.8 handles
  context well, so no heavy context-reset scaffolding needed.
- **Rejected:** Relying on `.remember/` alone (gitignored = not durable).

### 2026-06-24 · Root dev env depends on all 3 workspace members
- **What:** `[dependency-groups].dev` lists `core`/`gateway`/`worker` (via `[tool.uv.sources] workspace=true`);
  `make setup` uses `uv sync --all-packages`.
- **Why:** Root is `package=false`, so a plain `uv sync` installs neither the members nor their transitive
  deps (pydantic, fastapi, aio-pika) → `import core` and the mypy pydantic plugin both failed.
- **Rejected:** Passing `--all-packages` on every ad-hoc `uv run` (easy to forget; env drifts).

### 2026-06-24 · Initialization phase = full 6-service compose + Rung-0 boot only (note 7)
- **What:** Authored docker-compose.yml (6 svc), Dockerfiles, Makefile, mypy/pytest config, init.sh, and a
  smoke test. Entrypoints boot only (gateway `/health`, worker connect-and-idle) — no pipeline logic.
- **Why:** Note 7 — initialization is walled off from implementation; produce a *runnable* env + 1 passing
  test, not features. Docker unavailable locally, so `make check` = no-Docker gates; `make check-all` adds
  Docker gates. Dimension: Architecture (the compose topology is itself a graded decision — SPEC §2).
- **Rejected:** Infra-only compose (assignment needs all 6 via compose); deferring compose entirely.

### 2026-06-24 · Rewrote check-evidence.py + verify-before-commit.py self-contained (note 10)
- **What:** Evidence gate blocks any feature marked `passing` lacking a commit hash in `evidence` +
  non-empty `verification`. Commit gate runs ruff -> mypy --strict -> unit tests and blocks on RED,
  failing OPEN only when a gate can't launch (no false-block). Both verified with crafted payloads.
- **Why:** The inherited AMRIT versions referenced an unimported `validate_feature_list` and a
  non-existent `verify.py` — the evidence gate was dead and the commit gate would false-block every
  agent commit. Dimension: Reliability (the harness must actually enforce, not appear to).
- **Rejected:** Reinstating a separate single-source `verify.py` indirection — inlined the gates
  instead (fewer moving parts, no path-math fragility).

### 2026-06-24 · Session hygiene made mechanical (note 14)
- **What:** Clock-out is now a clean-state completion condition (CLAUDE.md). Enabled ruff `T10`+`T20`
  (extend-select) so leftover `breakpoint()`/`print` fail the lint gate. Recorded "harness is living —
  simplify periodically" principle.
- **Why:** Lehman's laws — agents copy existing patterns, so drift compounds without mechanical guards;
  a rule the model can't trip over beats prose. Fast-merge philosophy is explicitly NOT adopted (single-dev,
  low-throughput → careful review is correct here).
- **Rejected:** Mechanically banning TODO/FIXME (the Rung-stub docstrings are legitimate placeholders).

### 2026-06-24 · Next phase = mold reference code into our shape (user directive)
- **What:** Implementation will adapt SPEC §7 reference repos (kieled boilerplate, py-redis-semaphore,
  Storti backoff) into our core/infra adapters + worker handlers — pattern transfer, not wholesale copy.
- **Why:** No single repo matches the full assignment; the integration IS the assignment. Our boundaries
  (test_architecture.py) and MUST rules constrain HOW the borrowed patterns land. Climb the Rung ladder TDD.

### 2026-06-25 · H-XDEATH — retry counter authority = x-retry-count message header (F0.4)
- **What:** `x-retry-count` custom header (integer, incremented by `route_retry_or_dlq` on each
  requeue) is the **sole gating authority** for retry logic. `x-death.count` is permanently banned.
  `Task.attempts` Postgres column is **deferred** — not added unless `/stats` (R5.1) needs it.
- **Why:** On RabbitMQ ≥3.13 (and 4.x), `x-death.count` is frozen at 1 for every redelivery; using
  it for retry gating means the DLQ threshold is never reached (messages retry forever). This is a
  silent correctness failure, not a performance issue. The custom header travels with the message,
  survives broker restarts under persistent delivery, and is correctly incremented by the existing
  `infra/broker.py` implementation.
- **Why deferred (Task.attempts):** A durable column would enable retry-count queries after message
  expiry (e.g., for `/stats` forensics), but `/stats` (R5.1) is the last rung and its exact needs
  are not yet known. Adding the column now without a consumer is speculative schema. The header is
  sufficient for all current gating requirements.
- **Constraint:** If `Task.attempts` is ever added, the header WINS for gating; the column is
  read-only telemetry and must never diverge into a second authority.
- **Rejected:** `x-death.count` (broken ≥3.13); Python-side in-memory counter (lost on crash);
  eager `Task.attempts` column (no consumer yet, violates YAGNI).
- **Dimension:** Reliability (retry gating is a hard boundary — silent failure = messages loop forever).

### 2026-06-24 · First slice molded from `base-aiopika-pattern`: broker adapter + event contracts
- **What:** `core/infra/broker.py` (connect/declare_minimal/publish/consume) + `core/domain/events.py`
  (R1.4 pydantic contracts) + worker rewired to the adapter + integration test (testcontainers RabbitMQ).
  Scoped with the user to broker+events; the rest (retry ladder, ingestion, storage, semaphore) backlogged.
- **Why (the load-bearing deviations from the skeleton — molding, not copying):**
  - **Manual ack-LAST** instead of the skeleton's `async with message.process()` auto-ack. Auto-ack acks on
    handler return, so a crash between work and downstream-publish loses the event. Dimension: Reliability.
  - **Durable named `pipeline` exchange + durable `q.parse`** instead of default-exchange + `auto_delete`
    queue. Choreography topology must survive a broker restart. Dimension: State across boundaries.
  - **Publisher confirms ON** + **PERSISTENT** messages (skeleton had confirms off) — don't lose a publish.
  - **Events carry pointers only** (str-keyed pydantic models, frozen, defaulted `event_id`); bytes live in
    MinIO. Enforced structurally by a unit test asserting every event field is `str`. Dimension: Architecture.
  - Kept pydantic-settings v2 (skeleton used pydantic v1 `BaseSettings`).
- **Scope seam:** `declare_minimal` is Rung-0 (exchange + q.parse) only; the 1/4/16s retry ladder + q.dlq is
  R2.1, molded later from `retry-dlx-aiopika`. Left a clean seam rather than half-stubbing the ladder now.
- **Rejected:** copying the skeleton's `message.process()` consume loop (violates ack-LAST); declaring the
  full retry topology now (premature — belongs to its own reference repo + rung).
- **Verified:** `make check` green (ruff + mypy --strict 28 files + 13 unit tests). Broker integration test
  auto-skips without Docker; its real proof + R0.2 stack proof are deferred until Docker is available.
