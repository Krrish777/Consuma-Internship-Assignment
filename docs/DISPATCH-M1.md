# DISPATCH — Milestone M1 · "The end stages come alive (in isolation)"

> Senior-authored work order for **two parallel Sonnet sessions**. Each lane is file-disjoint
> from the other — you can both run flat out without colliding. Read your lane only.
> Deep how-to already exists in `snippets/` cards — this doc fences the files, fixes the order,
> and names the proof. **One source of truth for behavior: `docs/SPEC.md`.**

## Mission
Bring the **head** (parse) and **tail** (stitch + /stats) of the pipeline to life, each proven in
**isolation** (call the handler directly against real testcontainers — no consume loop needed yet).
The middle (TTS + semaphore) and all resilience (consume-loop, idempotency, DLQ, sweeper) are **M2**
— do NOT touch them.

## Global rules (BOTH sessions — breaking one of these is the only way to collide)
1. **Stay in your fence.** Only write the files listed under *Files you may write*. Never the other
   lane's files, never `core/infra/*`, never `worker/main.py`.
2. **Do NOT touch harness/shared files:** `feature_list.json`, `tests/conftest.py`, `PROGRESS.md`,
   `BACKLOG.md`, `docker-compose.yml`, `worker/handlers/__init__.py`. The senior owns these.
3. **You do not flip `feature_list.json`.** Implement → run your verification → paste the green
   output and STOP. The senior validates and records `passing`.
4. **No `print` / `breakpoint`** — ruff T10/T20 fail the build. Use `core.infra.logging.get_logger`,
   bind `job_id` (see R0.4). Every line a job touches carries `job_id`.
5. **`mypy --strict` + `ruff` must pass** on your files before you report (`make check`).
6. One logical change = one commit. Commit on your branch only. Do not merge to master.
7. If you think you must edit a fenced file to finish — **STOP and report**, don't reach across.

## Reviewer gate (what happens when you report green)
The senior switches to **strict reviewer**: re-runs your verification, reads your diff against
`docs/SPEC.md` §3/§4 + your card's MUST-rules (ack-order isn't in M1, but atomic-tx, FSM legality,
webhook≠failure, SSRF guard all are). Only then does the feature flip to `passing` and M2 open.
Code-complete-but-e2e-pending is an expected M1 end-state for stitch (full `make e2e` lands in M2).

---

## LANE A — Session 1 · branch `feat/parse-stage`
**Worktree:** `../consuma-parse-stage`  ·  **Theme:** sim substrate + parse fan-out (pipeline head)

### Files you may write (your fence)
- `packages/core/src/core/domain/text.py`  *(new — `split_blocks`)*
- `services/worker/src/worker/handlers/_sim.py`  *(new — vendor sim)*
- `services/worker/src/worker/handlers/parse.py`
- `tests/unit/test_fault_injection.py`  *(new)*
- `tests/unit/test_text.py`  *(new)*
- `tests/integration/test_parse.py`  *(new)*

### Task A1 — `R2.0` Vendor sim & fault injection  ·  card **W6**  ·  ⭐ pure unit, no Docker
- **Target:** `worker/handlers/_sim.py`.
- **Build:** `maybe_fail(rate: float, *, rng=random) -> None` → `if rng.random() < rate: raise SimVendorError`.
  `async def sim_latency(...) -> None` → `await asyncio.sleep(...)`. Define `class SimVendorError(Exception)`.
  Make the RNG **injectable** (a `random.Random` param) so tests are deterministic.
- **MUST:** the AI is simulated on purpose (SPEC §1) — no real model. Seedable.
- **Verify:** `uv run pytest tests/unit -k fault_injection`
- **Accept:** `rate=0.0` never raises; `rate=1.0` always raises; a fixed-seed `Random` gives a
  reproducible pass/fail sequence.

### Task A2 — `split_blocks` domain helper  ·  card **02-domain D3**  ·  ⭐ pure unit, no Docker
- **Target:** `core/domain/text.py` (PURE — no I/O; `core/domain` is unit-testable without Docker).
- **Build:** `split_blocks(manuscript: str) -> list[str]` — deterministic split into ordered blocks
  (decide the rule: blank-line paragraphs is fine). **Empty manuscript → `[]`** (the 0-block case
  parse must still terminate on).
- **Verify:** `uv run pytest tests/unit -k text`
- **Accept:** known text → expected ordered blocks; `""` → `[]`; stable/deterministic.

### Task A3 — `R2.3` Parse handler  ·  card **W3** (read it fully — it has 3 MUST-patches)  ·  ⭐⭐ integration
- **Target:** `worker/handlers/parse.py::handle`.
- **Build (exact):** download `raw/<job>.txt`; **inject 15% failure** (`maybe_fail(PARSE_FAILURE_RATE)`
  → raise → ladder); `split_blocks`; in **ONE transaction** write N `Task` rows via
  `pg_insert(...).on_conflict_do_nothing()` **and** set `pending_count=N` + advance `Job→GENERATING`
  **only on the first PENDING→GENERATING** (CAS); then **fan-out `TtsRequested ×N` for ALL blocks**.
  **0 blocks → emit `StitchReady` / go STITCHING directly** (must not hang).
- **MUST (from W3):** parse is a *re-publishable fan-out emitter* — **NOT** behind an inbox-skip.
  `ON CONFLICT` stops dup rows; **always re-publish all N**; **never blindly re-set `pending_count`**
  (CAS guard). Tasks + pending_count = one atomic tx.
- **Verify:** `uv run pytest tests/integration -k parse`
- **Accept:** normal manuscript → N tasks + `pending_count==N` + N events on `q.tts`;
  **run parse twice → still N rows, `pending_count==N` (not 2N), all N re-published**; **0-block → terminates.**
- **Test in isolation:** seed `raw/<job>.txt` in MinIO + insert `Job(PENDING)`, construct a
  `JobCreated`, call `handle()` directly, assert DB rows + drain `q.tts`. No consume loop required.

**Lane A done =** A1+A2+A3 verifications green on `feat/parse-stage`, output pasted, STOP.

---

## LANE B — Session 2 · branch `feat/stitch-stats`
**Worktree:** `../consuma-stitch-stats`  ·  **Theme:** stitch+notify + /stats (pipeline tail + observability)

### Files you may write (your fence)
- `services/worker/src/worker/handlers/stitch.py`
- `services/gateway/src/gateway/main.py`  *(append `GET /stats` only — do not alter existing routes)*
- `tests/integration/test_stitch.py`  *(new)*
- `tests/integration/test_stats.py`  *(new)*

### Task B1 — `R4.3` Stitch & notify  ·  card **W5** (read it fully — SSRF + idempotency patches)  ·  ⭐ integration
- **Target:** `worker/handlers/stitch.py::handle`.
- **Build (exact):** **idempotency short-circuit FIRST** — `if job.status == COMPLETED: ack & return`
  (no re-concat/re-fire/re-transition); else concat `tts/<job>/*` in **`block_index` order** →
  `out/<job>.mp3` (storage); set `Job→COMPLETED` via **compare-and-set** (`WHERE status=:expected`);
  commit; **then** fire webhook in a try/except that **logs a warning but NEVER flips status**.
- **MUST:** webhook/notification failure **MUST NOT** fail the job (SPEC §3) — COMPLETED+commit
  *before* the webhook. **SSRF guard:** `callback_url` is client-supplied → before `httpx.post`,
  reject hosts resolving to private/loopback/link-local (`ipaddress.is_private` etc.); use
  `httpx.post(url, timeout=WEBHOOK_TIMEOUT, follow_redirects=False)`.
- **Verify (M1, isolated):** `uv run pytest tests/integration -k stitch`
- **Accept:** seed fake `tts/<job>/*.wav` + `Job(STITCHING)` → `handle()` → Job COMPLETED +
  `out/<job>.mp3` present; **webhook 500 → still COMPLETED**; **redelivered StitchReady on a
  COMPLETED job → no 2nd webhook, no error**; callback to a private IP → refused.
- **Note:** full `make e2e -k stitch` (whole pipeline) is **M2** — for M1 prove it isolated (seeded).

### Task B2 — `R5.1` `GET /stats`  ·  card **G7 / 03-db B6**  ·  ⭐ integration
- **Target:** append `GET /stats` to `gateway/main.py`.
- **Build:** `count_by_status` over `jobs` (one `GROUP BY status` query via the db adapter);
  optional queue depths. Return JSON `{ "jobs": { "PENDING": n, ... } }`.
- **MUST:** read-only; reuse the lifespan-provided db engine on `app.state` (don't open per-request).
- **Verify:** `uv run pytest tests/integration -k stats`
- **Accept:** seed jobs across statuses → `/stats` returns correct grouped counts.

**Lane B done =** B1+B2 verifications green on `feat/stitch-stats`, output pasted, STOP.

---

## Why these two lanes never collide (the proof)
| | writes | reads (frozen) |
|---|---|---|
| Lane A | `domain/text.py`, `handlers/_sim.py`, `handlers/parse.py`, 3 test files | core models/storage/broker/events/config |
| Lane B | `handlers/stitch.py`, `gateway/main.py`(+/stats), 2 test files | core storage/models/FSM(`state.py`), httpx, config |

Write-sets share **no file**. The only common ground is *reading* frozen, already-`passing` core
modules. Shared harness files are senior-owned. → safe to run both at full throttle.
