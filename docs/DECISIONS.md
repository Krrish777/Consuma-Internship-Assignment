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
