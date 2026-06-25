# Phase 3 — DB query layer (the fan-in barrier lives here)

> The schema (`Job`/`Task`/`ProcessedEvent`) already exists in `core/infra/db.py`. This phase adds
> the **atomic query operations** the handlers call — above all the fan-in barrier, the
> highest-weight state-across-boundaries mechanism in the whole project.
>
> **Verified stack:** SQLAlchemy **2.0.51**, async ORM. `UPDATE … RETURNING` via
> `update(...).returning(...)` then `(await session.execute(stmt)).scalar_one()`. ON CONFLICT via
> `from sqlalchemy.dialects.postgresql import insert` (the dialect insert — generic `sqlalchemy.insert`
> has no `.on_conflict_do_nothing`).

---

### B4 — Atomic fan-in decrement, guarded in-transaction (H3)   [rung R4.2] [BOM: 03-B4] [scores: state ⭐]
depends_on: R4inbox
files: create `packages/core/src/core/infra/queries.py` (or extend `db.py`), extend `tests/integration/test_models.py`
context: **The single most grade-bearing mechanism.** Knowing when all N parallel TTS tasks are done
(the fan-in join) must be done with an atomic `UPDATE … RETURNING`, never a Python counter — exactly
one worker may see `0` and emit `StitchReady`. The **idempotency guard must be durable and in the same
transaction** as the decrement (H3): a conditional `UPDATE tasks SET status='DONE' WHERE task_id=:id
AND status<>'DONE'` — if its rowcount is 0 this task was already counted, so **skip the decrement**.
Guarding with Redis SETNX instead (ephemeral, evictable) → eviction + redelivery → double-decrement →
early StitchReady → an incomplete drama marked COMPLETED. That is the corruption this card prevents.
reuse: from scratch — no ref repo has UPDATE…RETURNING fan-in.
api:
```python
from sqlalchemy import update
# 1) durable, idempotent claim of THIS task (same tx as the decrement):
claim = (update(Task).where(Task.task_id == tid, Task.status != "DONE")
         .values(status="DONE", audio_key=key))
if (await session.execute(claim)).rowcount == 0:
    # already counted on a prior delivery — do NOT decrement again
    await session.commit(); return  # idempotent no-op
# 2) atomic barrier decrement:
dec = (update(Job).where(Job.job_id == jid)
       .values(pending_count=Job.pending_count - 1)
       .returning(Job.pending_count))
remaining = (await session.execute(dec)).scalar_one()
await session.commit()
# caller: if remaining == 0 -> emit StitchReady (see W4 / H-EMIT)
```
steps:
  1. `async def complete_task_and_decrement(session, job_id, task_id, audio_key) -> int | None:` —
     do the conditional claim; if already DONE return a sentinel (e.g. `None`) meaning "no decrement";
     else decrement and return `remaining`.
  2. Commit inside this operation so the claim+decrement are one atomic unit (ack happens after, in W4).
MUST: the duplicate guard is the **conditional `tasks.status` UPDATE in the SAME tx** as the decrement
  (H3) — never a Redis SETNX as the authority.
MUST: decrement via `pending_count = pending_count - 1` at the SQL level (atomic), returning the new value.
MUST: exactly one caller observes `remaining == 0` even under concurrent redelivery of the same task.
MUST NOT: read `pending_count` into Python, subtract, and write back (lost-update race).
verify: [L3] `uv run pytest tests/integration -k "models or fan_in"` — N tasks decrement to exactly 0
  once; the SAME task delivered twice decrements once (claim rowcount 0 on the 2nd); concurrent
  decrements never lose an update.
accept: exactly-once barrier crossing; duplicate task delivery is a no-op on the counter.
evidence:

---

### H15 — Set `pending_count` only on first PENDING→GENERATING CAS   [rung R2.3] [BOM: backlog-H15] [scores: state]
depends_on: B4
files: extend `core/infra/queries.py`, extend `test_models.py`
context: Corollary of H2/parse-re-runnability. If parse is redelivered, it must **not** reset
`pending_count=N` (that would resurrect already-decremented tasks and the job would never finish). The
counter is initialized **only on the first** compare-and-set transition out of PENDING; a re-run finds
the job already advanced and skips the counter set.
reuse: H-FSM CAS contract (Phase 1).
api: `update(Job).where(Job.job_id==jid, Job.status==PENDING).values(status=PARSING/GENERATING,
  pending_count=N)` — rowcount 1 = first time (counter set); rowcount 0 = re-run (counter untouched).
steps:
  1. `async def begin_parse(session, job_id, n_blocks) -> bool:` — CAS PENDING→(next) setting
     `pending_count=N`; return True only if rowcount==1 (first run).
  2. On rowcount 0 (re-run), the caller skips counter init and just re-publishes the N TtsRequested (W3/H2).
MUST: set `pending_count` exactly once, gated on the first CAS (H15) — a parse re-run never resets it.
MUST NOT: unconditionally `UPDATE jobs SET pending_count=N` on every parse delivery.
verify: [L3] `uv run pytest tests/integration -k "models or counter_once"` — calling `begin_parse` twice
  sets the counter once; the second call returns False and leaves the (possibly-decremented) counter intact.
accept: counter initialized once; re-run is a no-op on the counter.
evidence:

---

### B6 — Status / queue count query for `/stats`   [rung R5.1] [BOM: 03-B6] [scores: observability]
depends_on: —
files: extend `core/infra/queries.py`, extend `test_models.py`
context: `GET /stats` (R5.1) is the runtime observability view. It needs cheap aggregate counts of jobs
by status (and optionally tasks by status). Pure read query; powers G7.
reuse: from scratch.
api: `select(Job.status, func.count()).group_by(Job.status)` → `(await session.execute(stmt)).all()`.
steps:
  1. `async def job_counts_by_status(session) -> dict[str, int]:` — group-by aggregate.
  2. Optionally `task_counts_by_status` for richer stats.
MUST: be a read-only aggregate (no locks, no writes).
MUST NOT: load all rows into Python and count (use SQL `GROUP BY`).
verify: [L3] `uv run pytest tests/integration -k "models or stats"` — seed jobs in mixed states →
  returned dict matches expected counts.
accept: accurate per-status counts via one grouped query.
evidence:
