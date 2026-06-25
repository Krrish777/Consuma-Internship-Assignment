# Phase 4 — Worker pipeline body (the engine)

> The worker is currently an **idle skeleton** (`worker/main.py` connects, declares minimal topology,
> then `await asyncio.Future()` forever). This phase wires the consume loop, the dispatch/DI, and the
> three pipeline handlers (parse → tts → stitch), each with its hardening folded in.
>
> **Verified stack:** aio-pika **9.6.2** (all I/O awaitable). Manual-ack pattern:
> `async with message.process(ignore_processed=True):` lets the handler call `await message.ack()` /
> `await message.nack(requeue=False)` itself without a double-process error. The infra `broker.py`
> already provides `connect`, `declare_full`, `publish`, `consume`, `get_retry_count`,
> `route_retry_or_dlq` — handlers call these, they don't re-implement topology.

---

### X1 — Worker entrypoint & run loop   [rung R3.1] [BOM: 12-X1] [scores: reliability]
depends_on: —
files: modify `services/worker/src/worker/main.py`
context: Replace the idle `await asyncio.Future()` with a real run loop: bootstrap dependencies (X3),
register consumers on q.parse/q.tts/q.stitch (X2), then await shutdown. The process must exit cleanly
on SIGTERM (compose `docker stop`) so crash-recovery tests can kill and restart it deterministically.
reuse: `base-aiopika-pattern/src/consumer.py` (run-forever shape) — but NOT its auto-ack/`auto_delete` queue.
api: `await broker.connect(url)` → `channel = await conn.channel()` → register consumers → `await
  asyncio.Future()` guarded by a signal handler that cancels and closes connections.
steps:
  1. Bootstrap (X3) returns wired adapters. Open a channel, set QoS (W1).
  2. Register the three handlers (X2 dispatch).
  3. Install SIGTERM/SIGINT handlers that close the broker connection and cancel the run task.
MUST: shut down cleanly so unacked messages are released back to the broker for redelivery (R3.1).
MUST NOT: swallow SIGTERM or leave connections open (resource leak; breaks `docker kill` semantics).
verify: [L4] covered by R3.1 e2e (kill → redeliver). [L1] mypy/ruff green.
accept: worker starts, consumes, and stops cleanly on signal.
evidence:

---

### X3 — Bootstrap / dependency wiring   [rung R3.1] [BOM: 12-X3] [scores: arch]
depends_on: R1
files: create `services/worker/src/worker/bootstrap.py`
context: One place that constructs and wires every adapter the handlers need — broker, db engine,
redis client (+ `ensure_slots`, X4), storage, logging — from `get_settings()`. Keeps handlers
testable (inject the bundle) and keeps the worker independent of the gateway (CLAUDE.md boundary).
reuse: `structure-reference-only/app_factory.py` lifespan-factory pattern (port the idea, not taskiq).
steps:
  1. `async def build_context() -> WorkerContext:` — a dataclass/dict holding engine, redis client,
     semaphore, storage client, exchange.
  2. Call `configure_logging()` and `semaphore.ensure_slots()` (X4) exactly here, on boot.
MUST: keep gateway and worker mutually independent — worker imports `core`, never `gateway`
  (`test_architecture.py`).
MUST: call the idempotent `ensure_slots` (X4), not a raw RPUSH, so N workers don't make N×slots.
verify: [L2] `uv run pytest tests/unit -k architecture` green; [L3] a smoke test builds the context
  against containers.
accept: a single wired context; semaphore seeded exactly once.
evidence:

---

### X2 — Handler dispatch / DI table   [rung R3.1] [BOM: 12-X2] [scores: arch]
depends_on: X3
files: create `services/worker/src/worker/dispatch.py`
context: Map each queue to its handler, injecting the bootstrap context, so the consume loop is
generic. A single dispatch table makes the choreography legible (queue → handler → next event).
reuse: `base-aiopika-pattern/src/pika/router.py` (type-routed dispatch shape).
api: `Handler = Callable[[AbstractIncomingMessage], Awaitable[None]]`; bind context via closure/partial.
steps:
  1. `def build_handlers(ctx) -> dict[str, Handler]:` → `{Q_PARSE: parse_handler(ctx), Q_TTS: ...}`.
  2. Each handler parses its event from the message body (pydantic `model_validate_json`).
MUST: each handler reads the event as a pydantic contract (pointers only) — never expects bytes in the message.
MUST NOT: hardcode adapters inside handlers — inject via `ctx` (testability + boundary hygiene).
verify: [L2] unit test that the table maps the three queues to callables.
accept: queue→handler table wired from context.
evidence:

---

### W1 — Consume loop with manual-ack + sized prefetch (H-PREFETCH)   [rung R3.1] [BOM: 08-W1] [scores: reliability]
depends_on: X2
files: modify `worker/main.py` (use `broker.consume`); confirm prefetch config
context: The consume registration already exists (`broker.consume(channel, queue, handler, prefetch=)`,
manual ack, `no_ack=False`). This card sets prefetch sensibly: `PREFETCH=16` against only **3** global
TTS slots means 13+ messages park unacked on a blocked `BLPOP` per worker → a large crash-redelivery
blast radius and head-of-line pressure (H-PREFETCH). Size prefetch near serviceable concurrency,
especially for `q.tts`.
reuse: existing `broker.consume`. Do NOT copy the retry-dlx ref repo's `message.process()` auto-ack loop
  (H-REF1) — it acks before downstream publish.
api: `await channel.set_qos(prefetch_count=...)` (awaitable). Per-queue channels allow per-queue prefetch.
steps:
  1. Set `q.tts` prefetch close to the semaphore size (e.g. slots + small headroom), not 16.
  2. Keep manual ack (`no_ack=False`) — ack happens LAST inside each handler (W2).
MUST: ack LAST (after commit + publish); prefetch sized near concurrency for q.tts (H-PREFETCH).
MUST NOT: use the ref repo's auto-ack consume loop (H-REF1).
verify: [L4] R3.1 crash test (bounded redelivery); [L1] config asserted in a unit test.
accept: prefetch sized to concurrency; manual ack retained.
evidence:

---

### X7 — Exception taxonomy (retryable vs poison)   [rung R3.3] [BOM: 12-X7] [scores: reliability]
depends_on: —
files: create `services/worker/src/worker/errors.py` (or co-locate with `_sim.py`)
context: The consume loop must decide, on a raised exception, whether to **retry** (transient → retry
ladder) or **dead-letter immediately** (poison → DLQ). A clear two-class taxonomy drives this and keeps
the W2 skeleton simple. (R2.0's sim already raises `TransientError`/`PoisonError`.)
reuse: from scratch.
steps:
  1. `class TransientError(Exception)` (retryable) and `class PoisonError(Exception)` (non-retryable).
  2. Document: unknown/unexpected exceptions are treated as transient up to MAX_RETRIES, then DLQ'd
     (fail-safe — don't lose a message because of an unclassified bug).
MUST: poison → DLQ immediately (don't waste 3 retries on a deterministic failure); transient → ladder.
MUST NOT: let an unclassified exception silently ack-and-drop the message (that loses work).
verify: [L2] unit test that the loop's classifier routes each class correctly (pair with W2).
accept: two exception classes with documented routing.
evidence:

---

### W2 — Ack-last handler skeleton   [rung R3.1] [BOM: 08-W2] [scores: reliability ⭐]
depends_on: W1, X7
files: create `services/worker/src/worker/handlers/_base.py`
context: The single most important ordering in the system (SPEC §4): **do work → COMMIT Postgres →
PUBLISH next event → ACK message.** Ack dead last. Ack-before-publish + crash = lost event, job stalls.
Publish-then-crash = duplicate, absorbed by idempotency. This skeleton wraps every handler so the
ordering and the transient/poison routing are written once, not three times.
reuse: from scratch (the ref loops all ack too early — H-REF1).
api: `async with message.process(ignore_processed=True):` then explicit `await message.ack()` at the end,
  or `await message.nack(requeue=False)` + `broker.route_retry_or_dlq(...)` on transient, or direct DLQ on poison.
steps:
  1. `def ack_last(do_work)` decorator/wrapper: run `do_work` (which commits + publishes); on success
     `await message.ack()`. On `TransientError` → `route_retry_or_dlq` then ack the original (the copy
     carries the work forward). On `PoisonError` → publish to `q.dlq` then ack. On unknown → treat transient.
  2. Ensure the ack is the LAST awaited call on every path.
MUST: ack LAST on the success path (after commit + publish) — never before publish (R3.1).
MUST: on transient failure, route to the retry ladder (`route_retry_or_dlq`) BEFORE acking the original
  (so the in-flight message isn't lost), then ack.
MUST NOT: ack inside `process()` without `ignore_processed=True` (double-process error), and MUST NOT
  let an exception escape `process()` uncaught with `requeue=True` (infinite hot-queue redelivery — HOL block).
verify: [L4] R3.1 (crash mid-handler → redeliver, no loss) + R3.3 (poison → DLQ, no HOL). [L2] unit test
  the routing branches with a fake message.
accept: every handler acks last; transient→ladder, poison→DLQ, success→ack.
evidence:

---

### W3 — Parse handler (fan-out emitter)   [rung R2.3] [BOM: 08-W3] [scores: state, edge ⭐]
depends_on: W2, D3, R2.0, H15, R4inbox
files: create `services/worker/src/worker/handlers/parse.py`, `tests/integration/test_parse.py`
context: Parse consumes `JobCreated`, "parses" the manuscript (15% sim 500s → retry; poison → DLQ),
splits into N blocks, writes **N Task rows + `pending_count=N` in ONE transaction**, transitions the
job toward GENERATING, and **fans out N `TtsRequested`**. A **0-block manuscript must go straight to
STITCHING** (or COMPLETED) so the job still terminates — a fan-in barrier of 0 must not hang. This is
the most subtle correctness card: parse is a **re-publishable emitter**, never inbox-skipped.
reuse: from scratch (fan-out + ON CONFLICT not in any ref repo).
api: `split_blocks` (D3), `content_hash` (D4), `begin_parse` CAS (H15), dialect `insert(...)
  .on_conflict_do_nothing(index_elements=["job_id","block_index"])`, `broker.publish(exchange,
  TtsRequested(...), Q_TTS)`.
steps:
  1. Load manuscript from MinIO (`raw/<job>.txt`), run `sim_parse` (may raise transient/poison).
  2. `blocks = split_blocks(text)`. If `len(blocks) == 0`: CAS job → STITCHING (then publish StitchReady
     or directly complete in W5) and return — **no hang**.
  3. Else, in ONE tx: `INSERT … ON CONFLICT DO NOTHING` the N Task rows (block_index, block_hash),
     and `begin_parse` CAS sets `pending_count=N` **only on first run** (H15); commit.
  4. **Always** publish all N `TtsRequested` (even on a re-run where rows already existed) — the rows may
     be present but their events un-published from a prior crash (H2). Then ack (W2).
MUST: never inbox-skip parse (H2) — use `ON CONFLICT DO NOTHING` on the task rows + **always re-publish
  all N** TtsRequested, so a redelivery that finds rows already inserted still emits the events.
MUST: set `pending_count` only on the first CAS (H15) — a re-run must not reset it.
MUST: 0-block → STITCHING directly (edge case — fan-in of 0 must terminate, not hang).
MUST: cap/batch block count at `MAX_BLOCKS` (H14) — a huge manuscript must not write millions of rows /
  messages in one unbounded tx.
MUST NOT: write the task rows in one tx and the counter in another (they must be atomic together).
verify: [L3] `uv run pytest tests/integration -k "parse_fanout or zero_block_terminates"` — N-block
  manuscript → N tasks + pending_count=N + N TtsRequested on q.tts; redelivered JobCreated → still N
  tasks (ON CONFLICT) and counter unchanged but events re-published; 0-block → job reaches STITCHING/terminal.
accept: N tasks + N events once-effectively; counter set once; empty manuscript terminates.
evidence:

---

### W4 — TTS handler (cache → slot → generate → fan-in)   [rung R4.2] [BOM: 08-W4] [scores: state, reliability ⭐]
depends_on: W2, R2, R3, B4, H8
files: create `services/worker/src/worker/handlers/tts.py`, `tests/integration/test_tts.py`
context: The heart of both vendor constraints and the fan-in barrier. For each `TtsRequested`:
**check the content cache BEFORE acquiring a semaphore slot** (a hit burns no token — Constraint B and
the slot economy), else acquire a leased slot, synthesize (sim), write `tts/<hash>.wav`, populate the
cache, then run the **atomic fan-in decrement** (B4). Exactly one worker observes `pending_count == 0`
and emits `StitchReady`. Handle the crash-after-decrement-before-publish window (H-EMIT).
reuse: from scratch.
api: `cache_get` (R3) → `Semaphore.acquire/release` (R2) → `sim_tts` (R2.0) → `storage.put_bytes(
  f"tts/{hash}.wav", audio)` → `cache_set` (R3) → `complete_task_and_decrement` (B4) → `broker.publish(
  exchange, StitchReady(job_id), Q_STITCH)`.
steps:
  1. `h = content_hash(block_text)`. `url = await cache_get(h)`. If hit: use it, **skip slot acquire**,
     go straight to the fan-in decrement with `audio_key = cached key`.
  2. If miss: optionally win the in-flight lock (H8); `async with semaphore.acquire()`: `sim_tts` →
     `put_bytes(tts/<h>.wav)` → `cache_set(h, key)`. Release slot.
  3. `remaining = complete_task_and_decrement(job_id, task_id, audio_key)` (B4 — atomic + idempotent).
  4. If `remaining == 0`: publish `StitchReady`. Then ack (W2).
  5. **H-EMIT:** on a redelivery where the conditional claim no-ops (task already DONE), **re-read
     `pending_count`; if it is 0, re-emit `StitchReady`** (stitch is idempotent, W5/H5) — covers the
     crash-after-decrement-before-publish gap. Then ack.
MUST: cache check happens **before** `acquire` (SPEC §4) — a hit must not consume a token.
MUST: fan-in via the atomic B4 op (durable conditional claim + `UPDATE…RETURNING`), never a Redis-only guard (H3).
MUST: on redelivery after the barrier was already crossed in DB, re-emit `StitchReady` if count==0 (H-EMIT).
MUST: use the leased semaphore (R2/X5), never `asyncio.Semaphore`.
MUST NOT: hold a slot across the cache populate for *other* identical blocks (use H8's in-flight lock instead).
verify: [L4] `make e2e -k "cache or fan_in"` — identical block → exactly one vendor synth (2nd is a cache
  hit, no slot); N tasks → exactly one `StitchReady`; kill a worker between decrement and publish →
  redelivery re-emits StitchReady and the job still completes once.
accept: cache-before-slot honored; exactly one StitchReady; crash window covered.
evidence:

---

### W7 — DLQ → fan-in resolver (H4)   [rung R3.3] [BOM: 08-W7] [scores: edge, reliability]
depends_on: W4, B4
files: create `services/worker/src/worker/handlers/dlq.py` (consumer on `q.dlq`), `tests/integration/test_dlq_resolver.py`
context: H4 — a single poison **TTS** block exhausts retries and lands on `q.dlq`, but if it never
decrements `pending_count` the whole job **stalls forever** in GENERATING. The DLQ path for a TTS task
must resolve the barrier: mark that task `FAILED` and still decrement (stitch then skips failed blocks),
**or** atomically fail the whole job. Decide and document the policy (the decision itself scores points).
reuse: from scratch.
api: a consumer on `q.dlq`; reuse B4's decrement but record the task as `FAILED` rather than `DONE`;
  if the job should hard-fail, CAS job → FAILED (H-FSM).
steps:
  1. Consume `q.dlq`. Parse which task/job the poison message belongs to.
  2. Policy (recommended): `UPDATE tasks SET status='FAILED'` for that task + decrement the barrier
     (same atomic shape as B4) so the job can still reach STITCHING; stitch skips FAILED blocks (W5).
     Alternatively CAS job→FAILED and short-circuit. Document the chosen policy in DOC1.
  3. If the decrement reaches 0, emit `StitchReady` (partial drama) or finalize FAILED per policy.
MUST: the DLQ path MUST resolve the fan-in barrier (H4) — a poisoned block must not leave the job hung.
MUST: keep this **off the hot queue** — it consumes `q.dlq`, so healthy `q.tts` traffic is unaffected (no HOL).
MUST NOT: silently drop the DLQ message without touching the barrier (the stall bug).
verify: [L4] `make e2e -k poison_pill` — a poison block → DLQ after 3 backoff attempts AND the job
  converges (FAILED or COMPLETED-with-skipped-block per policy); concurrent healthy jobs still complete.
accept: poisoned block resolves the barrier; no permanent stall; healthy traffic unblocked.
evidence:

---

### W5 — Stitch handler (idempotent finalize)   [rung R4.3] [BOM: 08-W5] [scores: edge, state]
depends_on: W4, H-FSM
files: create `services/worker/src/worker/handlers/stitch.py`, `tests/integration/test_stitch.py`
context: Consume `StitchReady`, gather the job's `tts/*.wav` chunks, concatenate into `out/<job>.mp3`,
set the job `COMPLETED` via FSM compare-and-set, and trigger the webhook (W5b). Must be **idempotent**:
a redelivered `StitchReady` must not double-produce or attempt the illegal `COMPLETED→COMPLETED`
transition (H5) — if already COMPLETED, ack and return.
reuse: `minio-sdk-examples` for list/get/put. **Do NOT use `compose_object` for concat** — see api note.
api: `storage.list_prefix(f"tts/")` filtered by job → ordered chunk keys; **client-side concat**:
  `get_bytes` each chunk, join, `put_bytes(f"out/{job}.mp3", joined)`. **Why not `compose_object`:** MinIO
  server-side compose requires every non-final part ≥ **5 MiB**; simulated TTS chunks are tiny, so
  `compose_object` raises "minimum allowed 5MiB". Download-join-put is correct for small chunks. (MinIO
  SDK is sync → wrap each call in `await asyncio.to_thread(...)`.)
steps:
  1. CAS job → STITCHING (skip if already past it). If already COMPLETED → ack & return (H5).
  2. List the job's chunk keys in block order (skip FAILED-block keys per W7 policy).
  3. Client-side concat → `put_bytes(out/<job>.mp3)`; set `final_key`.
  4. CAS job → COMPLETED. Trigger webhook (W5b). Ack last.
MUST: short-circuit if already COMPLETED (H5) — no double webhook, no illegal self-transition.
MUST: advance status via CAS (H-FSM), treating rowcount-0 as "someone else did it," not an error.
MUST NOT: use `compose_object` on sub-5-MiB chunks (it will raise) — concat client-side.
verify: [L4] `make e2e -k stitch` — full happy path → COMPLETED + `out/<job>.mp3` exists; redelivered
  StitchReady → still one asset, one COMPLETED, webhook fired once.
accept: final asset produced; idempotent under redelivery.
evidence:

---

### W5b — Webhook notify (failure ≠ job failure)   [rung R4.3] [BOM: 08-W5] [scores: edge]
depends_on: W5, H-SSRF
files: extend `worker/handlers/stitch.py` (or `worker/notify.py`), extend `test_stitch.py`
context: After COMPLETED, notify the user via the client `callback_url` (or just log if none / not
allowlisted). **A webhook failure MUST NOT fail the job** (SPEC §3 edge case + MUST #8) — the job is
already COMPLETED; the notification is best-effort.
reuse: `fastapi-rmq-pg-glue` httpx client shape.
api: `async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_S, follow_redirects=False) as c:
  await c.post(url, json=payload)` — `follow_redirects=False` is httpx's default and the SSRF-safe posture.
steps:
  1. If `callback_url` is set and passes the H-SSRF guard, POST a small JSON payload (job_id, status, final_key).
  2. Wrap in try/except — log any failure, **do not** re-raise, **do not** change job status.
MUST: webhook failure is swallowed (logged) — the job stays COMPLETED (MUST #8).
MUST: run the URL through the H-SSRF guard before calling it.
MUST NOT: retry the webhook on the pipeline retry ladder (that would ride a completed job into the DLQ — H5).
verify: [L4] `make e2e -k webhook_failure_still_completed` — point callback at a failing/unreachable URL →
  job is still COMPLETED with its asset; log records the webhook failure.
accept: notification is best-effort; job completion is independent of it.
evidence:

---

### H-SSRF — Callback URL guard   [rung R4.3] [BOM: backlog-H-SSRF] [scores: reliability, security]
depends_on: —
files: create `services/worker/src/worker/ssrf.py`, `tests/unit/test_ssrf.py`
context: H-SSRF — a client-supplied `callback_url` handed to `httpx.post` is a Server-Side Request
Forgery vector (hit internal services / metadata endpoints). Guard it: resolve the host, **block
private/loopback/link-local/reserved/multicast ranges**, enforce an **allowlist** (`WEBHOOK_ALLOWLIST`),
**no redirects**, hard **timeout**.
reuse: stdlib `ipaddress` + `socket` (verified building blocks).
api: `socket.getaddrinfo(host, None)` → for each resolved IP, `ipaddress.ip_address(ip)` and reject if
  `.is_private or .is_loopback or .is_link_local or .is_reserved or .is_multicast`; also reject `0.0.0.0`/`::`.
steps:
  1. `def is_allowed(url: str, allowlist: tuple[str,...]) -> bool:` — parse host; if allowlist non-empty,
     host must be in it; resolve ALL A/AAAA records and reject if any is in a blocked range.
  2. Pair with `follow_redirects=False` (W5b) so each hop is controlled.
MUST: block private/loopback/link-local/reserved/multicast + `0.0.0.0`/`::`; honor the allowlist; no redirects; timeout (H-SSRF).
MUST: resolve and check **every** returned address (a host can resolve to multiple IPs).
MUST NOT: trust the hostname string alone (DNS can point a public name at a private IP).
verify: [L2] `uv run pytest tests/unit -k ssrf` — `http://169.254.169.254/...` blocked, `http://localhost`
  blocked, a private 10.x host blocked, an allowlisted public host allowed, non-allowlisted public host
  blocked when allowlist set.
accept: internal/metadata targets rejected; only allowlisted public hosts pass.
evidence:
