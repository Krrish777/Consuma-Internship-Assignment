# Phase 0 — Foundation & reconciliation

> Clean the stale state and lay the config substrate the later hardening cards need.
> These are small, low-risk, do-them-first cards. No card here touches the pipeline body.

---

### F0.1 — Reconcile stale `feature_list.json` + refresh `PROGRESS.md`   [rung R2.2x] [scores: reliability]
depends_on: —
files: modify `feature_list.json`, `PROGRESS.md`
context: The gateway features R2.2a/R2.2b/R2.2c/R2.2/R2.2d are **already coded and integration-tested**
(`services/gateway/src/gateway/{main,schemas}.py`, `tests/integration/test_ingestion.py`,
`tests/unit/test_schemas.py`, `tests/unit/test_error_handlers.py`) but their `feature_list.json`
status is still `not_started`, and the `verification` strings reference `-k lifespan`/`-k ingestion`
while the test file is `test_ingestion.py`. `PROGRESS.md` predates the db/storage/gateway/topology work.
This card makes the tracker tell the truth so the scheduler picks the right next card.
reuse: —
steps:
  1. Run each R2.2x verification against the real test names; capture output.
  2. For each that passes, set `status: "passing"` and write `evidence` = the command + "N passed" + the
     commit hash that introduced it (find via `git log --oneline -- tests/integration/test_ingestion.py`).
  3. Fix the `verification` `-k` selectors to match actual test names/markers.
  4. Rewrite `PROGRESS.md` "Completed / In progress / Next" to reflect the true HEAD (core/infra done
     except redis; gateway done; worker is an idle skeleton; next = Phase 1).
MUST: keep exactly **one** `in_progress` after this card (WIP=1, `check-wip.py`).
MUST: never mark `passing` without a real commit hash in `evidence` (`check-evidence.py`).
MUST NOT: invent evidence or flip a feature whose test you didn't actually run green.
verify: [L1] `python .claude/scripts/check-wip.py` and `check-evidence.py` exit 0; [L2] the R2.2x
  `-k` selectors each run green: `uv run pytest tests/unit -k "schemas or error_handlers"` and
  `uv run pytest tests/integration -k ingestion`.
accept: `feature_list.json` shows R2.2x `passing` with commit-hash evidence; hooks pass; PROGRESS reflects HEAD.
evidence:

---

### F0.2 — Add missing config knobs (hardening substrate)   [rung R0] [scores: arch]
depends_on: —
files: modify `packages/core/src/core/config.py`
context: Five later hardening cards (H13 size guard, H-SSRF, H14 block cap, H10 retention, H6 lease,
R3 cache TTL, G8 sweeper) need tunables that don't exist yet. Add them now, env-injected with spec
defaults, so each consuming card just reads `get_settings()`. Centralizing them is itself an
architectural signal (config as the single env seam, SPEC §10).
reuse: existing `Settings` class in `config.py` — extend it, keep the `lru_cache` accessor.
api: pydantic-settings 2.x — `SettingsConfigDict(env_file=".env", extra="ignore")` already in place;
  use a `@field_validator` (mode="after") to parse comma-separated strings into tuples if needed.
steps:
  1. Add fields with defaults: `MAX_MANUSCRIPT_BYTES: int = 1_000_000` (H13),
     `MAX_BLOCKS: int = 10_000` (H14), `WEBHOOK_ALLOWLIST: str = ""` (comma-sep hosts; empty = log-only, H-SSRF),
     `WEBHOOK_TIMEOUT_S: float = 5.0`, `SWEEP_INTERVAL_S: int = 30` + `PENDING_TIMEOUT_S: int = 120` (G8/H1),
     `LEASE_TTL_S: int = 30` (H6), `CACHE_TTL_S: int = 86_400` (R3/H-DANGLE),
     `PROCESSED_EVENTS_RETENTION_S: int = 604_800` (H10).
  2. Add a helper to parse `RETRY_DELAYS` and `WEBHOOK_ALLOWLIST` into typed tuples (the existing
     `RETRY_DELAYS: str = "1,4,16"` is still a raw string — give it a parsed accessor).
  3. Keep mypy --strict clean (annotate everything).
MUST: every new value MUST be overridable by an env var of the same name (compose injects them).
MUST NOT: hardcode any of these inside a handler later — read them from settings.
verify: [L1] `uv run mypy --strict`; [L2] `uv run pytest tests/unit -k smoke` (settings load with defaults)
  + a new test asserting an env override changes the value and the allowlist parses to a tuple.
accept: `get_settings()` exposes all new knobs; overriding via env changes them; ruff+mypy green.
evidence:

---

### F0.3 — Remove the dead `domain/models.py` stub   [rung R1] [scores: arch]
depends_on: —
files: delete `packages/core/src/core/domain/models.py` (or reduce to a re-export note)
context: `domain/models.py` is a placeholder docstring claiming it holds Job/Task/ProcessedEvent, but
those ORM models actually live in `core/infra/db.py` (correctly — SQLAlchemy is I/O, and the
architecture test bans it from `domain`). The dead stub is a trap: a future engineer may "implement"
models there and trip `test_architecture.py`. Remove it; if anything imports it, re-point to `infra.db`.
reuse: —
steps:
  1. `grep` for `domain.models` / `from core.domain.models` imports — there should be none.
  2. Delete the file (or replace its body with a one-line module docstring pointing to `core.infra.db`).
  3. Confirm `SPEC.md` §5 section-map row for `models.py` still resolves (it points to
     `core/domain/models.py` + `state.py`; note in DOC1 that models relocated to infra).
MUST: keep `core/domain` import-clean — `test_architecture.py` must still pass.
verify: [L2] `uv run pytest tests/unit -k architecture` green; `uv run mypy --strict`.
accept: no dead stub; no import references it; architecture test green.
evidence:

---

### F0.4 — Settle the retry-counter source of truth (H-XDEATH)   [rung R2.1] [scores: state, reliability]
depends_on: —
files: modify `docs/DECISIONS.md`; conditionally modify `packages/core/src/core/infra/db.py` +
  add an Alembic migration **only if** the analysis says so.
context: `broker.py` already tracks retries in a custom `x-retry-count` **message header** (H-XDEATH-safe —
`x-death.count` is frozen on RabbitMQ ≥3.13/4.x, **confirmed**). The open question: is a header durable
enough, or do we also need a `Task.attempts` Postgres column? A header travels with the message and
**survives RabbitMQ restarts** (persistent message) and redelivery, so for *transport-level* retry gating
the header is sufficient. A durable column is only needed if we want attempt history queryable after the
message leaves the system (e.g. for `/stats` or forensics).
reuse: existing `broker.get_retry_count` / `route_retry_or_dlq` in `infra/broker.py`.
api: RabbitMQ persistent messages preserve headers across broker restart; `x-death.count` NOT incremented ≥3.13.
steps:
  1. Write a short DECISIONS.md entry: **header is the source of truth for retry gating** (it is durable
     under persistent delivery); a `Task.attempts` column is OPTIONAL telemetry, deferred unless `/stats`
     needs it.
  2. If (and only if) you decide telemetry is in-scope now: add `attempts: Mapped[int] = mapped_column(
     Integer, default=0)` to `Task` and an Alembic migration; otherwise record the deferral.
MUST: do NOT reintroduce any gating on `x-death.count` (H-XDEATH).
MUST NOT: duplicate the retry counter in two authorities that can disagree (header vs column) without a
  clear rule for which wins — the header wins for gating.
verify: [L2] `uv run pytest tests/integration -k topology` still green (retry routing unchanged);
  if a column/migration was added, `uv run pytest tests/integration -k models` green.
accept: DECISIONS.md records the chosen authority; no x-death.count gating anywhere; tests green.
evidence:
