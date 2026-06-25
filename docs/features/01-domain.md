# Phase 1 — Domain pure logic (no Docker)

> Pure, unit-testable building blocks. Nothing here touches I/O, so every card verifies at L2
> (unit) without a container. These feed the worker handlers in Phase 4.

---

### D3 — `split_blocks` manuscript splitter   [rung R2.3] [BOM: 02-D3] [scores: edge]
depends_on: —
files: create `packages/core/src/core/domain/text.py`, `tests/unit/test_text.py`
context: Parse turns a manuscript string into N "blocks", each becoming one Task + one TTS call.
The splitter is pure logic and the source of the fan-out width — so its edge behavior (empty
manuscript → 0 blocks, single line → 1 block) directly drives the 0-block/1-block termination
edge cases the grader probes. Keep it deterministic.
reuse: from scratch — no ref repo has this.
steps:
  1. `def split_blocks(manuscript: str) -> list[str]:` — split on blank-line boundaries
     (paragraph = block); strip whitespace; drop empties.
  2. Empty/whitespace-only manuscript → `[]` (0 blocks). One paragraph → `[that]` (1 block).
  3. Document the rule in the docstring (graders read intent).
MUST: be a pure function — no I/O, no randomness, no global state (`test_architecture.py` bans I/O in domain).
MUST: return `[]` for empty input (the 0-block path that W3 routes straight to STITCHING).
MUST NOT: raise on empty/huge input — bounding is W3/H14's job, not the splitter's.
verify: [L2] `uv run pytest tests/unit -k text` — cases: empty→[], whitespace→[], 1 para→1,
  3 paras→3, trailing/leading blank lines ignored.
accept: deterministic block list; 0- and 1-block inputs handled.
evidence:

---

### D4 — `content_hash` (sha256) for cache/idempotency keys   [rung R4.2] [BOM: 02-D4] [scores: state]
depends_on: —
files: create `packages/core/src/core/domain/hash.py`, `tests/unit/test_hash.py`
context: TWO different idempotency keys must never be conflated (SPEC §4): the **vendor-call cache**
keys on `sha256(text)` (dedupe identical blocks → no 2nd vendor hit, MinIO object key = hash), while
the **fan-in decrement** keys on `task_id` (two identical blocks are still two tasks that each
decrement). This card provides only the content hash; the task_id key is the DB's job.
reuse: from scratch.
steps:
  1. `def content_hash(text: str) -> str:` → `hashlib.sha256(text.encode("utf-8")).hexdigest()`.
  2. Stable across processes/runs (pure stdlib, no salt).
MUST: hash the block **text**, never the task_id (conflating cache and counter is the named junior trap).
MUST: be the single canonical hasher — W4 (cache + `tts/<hash>.wav` key) and W3 (`Task.block_hash`) both call it.
verify: [L2] `uv run pytest tests/unit -k hash` — same text→same hash; different text→different;
  known vector for a fixed string.
accept: deterministic 64-char hex; reused by W3 and W4.
evidence:

---

### R2.0 — Vendor simulation & fault injection   [rung R2.0] [BOM: 08-W6] [scores: reliability, edge]
depends_on: D3 (split_blocks), D4 (content_hash)
files: `packages/core/src/core/domain/vendor.py`, `tests/unit/test_fault_injection.py`
context: The AI is simulated (SPEC §1) — this module IS the probe substrate. Parse must inject a
**15% transient 500-rate** (exercises retry) and a deterministic **poison manuscript that always
fails** (exercises DLQ-after-3); TTS produces deterministic fake audio (feeds the R4.2 cache).
Failure must be **seedable** so unit tests are deterministic and e2e tests reproducible. The pure
fault logic lives in `core/domain` (architecture boundary); the worker handler wraps it with
`asyncio.sleep` for latency — there is no separate `_sim.py`.
reuse: composes D3 `split_blocks` + D4 `content_hash` (single source of truth per primitive).
api: `random.Random(seed)` instance (NOT module-global `random`) for isolation; the handler adds
  `asyncio.sleep` latency at call time.
steps:
  1. `def simulate_parse(text, *, failure_rate=PARSE_FAILURE_RATE, rng=None) -> list[str]:` — if the
     text contains the poison marker (`"__POISON__"`) raise `VendorError`; else with prob
     `failure_rate` raise `VendorError`; on success return `split_blocks(text)` (D3).
  2. `def tts_fake_audio(text) -> bytes:` — deterministic bytes keyed on `content_hash(text)` (D4) so
     stitch has something to concat and the cache dedup works.
  3. Define a SINGLE retryable exception `VendorError` here.
MUST: be seedable — `rate=0.0` never fails, `rate=1.0` always fails, fixed seed reproducible,
  poison manuscript ALWAYS raises regardless of rate (deterministic DLQ trigger).
MUST: model poison as a **consistently-failing** input that exhausts the retry ladder → DLQ after 3
  (SPEC §1) — the SAME retryable `VendorError` as a transient 500, NOT a separate non-retryable type.
  (See docs/DECISIONS.md 2026-06-25 R2.0: SPEC overrides the earlier "PoisonError → straight to DLQ"
  wording, which contradicted SPEC §1 and R3.3's "DLQ after 3 attempts".)
MUST NOT: use module-global `random` (bleeds state across concurrent jobs); duplicate the D3/D4
  primitives; add a fail-fast non-retryable path (out of spec).
verify: [L2] `uv run pytest tests/unit -k fault_injection` — rate=0 never raises; rate=1 always raises
  `VendorError`; poison always raises `VendorError` (same type); same seed → same outcome; parse
  returns D3 blocks; tts keyed on D4 hash.
accept: deterministic, seedable failure behavior; single retryable class; primitives reused from D3/D4.
evidence: uv run pytest tests/unit -k fault_injection -> green; vendor.py composes D3/D4; commit 0aff00f anchor.

---

### H-FSM — Compare-and-set transition contract helper   [rung R1.2] [BOM: 02-D2] [scores: state]
depends_on: —
files: modify `packages/core/src/core/domain/state.py` (add the contract doc + a tiny pure helper);
  the actual SQL CAS lives in Phase 3/4 handlers.
context: `can_transition()` is a pure predicate, but applying it as read-then-write lets two workers
race the `status` column (H-FSM). The fix is a **compare-and-set UPDATE**:
`UPDATE jobs SET status=:next WHERE job_id=:id AND status=:expected` — rowcount 0 means "lost the race,
someone else advanced it." This card makes the contract explicit so every status write in Phases 3–4
goes through CAS, not read-modify-write.
reuse: existing `can_transition(current, next)` in `state.py`.
api: SQLAlchemy `update(Job).where(Job.job_id==id, Job.status==expected).values(status=next)`;
  `result.rowcount == 0` ⇒ lost race (caller decides: ack as already-handled, or re-read).
steps:
  1. Add a module docstring section "Applying transitions" documenting the CAS rule + rowcount semantics.
  2. Optionally add `def expected_for(next: JobStatus) -> set[JobStatus]:` returning legal predecessors,
     so handlers can build the `WHERE status IN (...)` guard from one source of truth.
  3. Keep it pure — no SQLAlchemy import in `domain` (that import lives in the handler).
MUST: the contract MUST state that a rowcount-0 CAS is a **normal concurrent outcome**, not an error
  (H-FSM). Handlers treat it as "already advanced" and proceed idempotently.
MUST NOT: add any I/O to `domain/state.py` (architecture test).
verify: [L2] `uv run pytest tests/unit -k state_machine` still green; new test on `expected_for` if added.
accept: CAS contract documented; predecessors derivable purely; domain stays I/O-free.
evidence:
