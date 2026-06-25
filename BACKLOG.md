# BACKLOG — reliability & correctness hardening

> Findings from the strict distributed-systems architecture review (2026-06-24). Full reasoning,
> failure traces, and research citations: **`tmp/ARCH-REVIEW-2026-06-24.md`**.
>
> This backlog is the *actionable index*. `feature_list.json` is the Rung ladder (what to build);
> this is the *hardening* list (what each rung must get right, or it silently fails a `kill -9` probe).
> Each item names the **rung it attaches to** — fold the fix in when that rung is implemented; do not
> mark the rung `passing` until the linked items are addressed (or consciously deferred with a note).
>
> Severity: **S0** = silent corruption / permanent stall (loses the grade) · **S1** = duplicate effect /
> invariant violation · **S2** = scale / security / load · **S3** = hygiene / reference-repo trap.

---

## S0 — must fix before claiming "passing" on Rungs 2–4

- [ ] **H-XDEATH** (R2.1, R3.3) — `x-death.count` is **not incremented on RabbitMQ ≥3.13** (we pin
  `rabbitmq:4`). Gating retries on it loops forever or DLQs early. **Fix:** track attempts in a durable
  Postgres counter (or a re-stamped custom header), not `x-death.count`. Consider quorum queues'
  `x-delivery-count`/`delivery-limit`. Use **separate delay queues per TTL** (see H-TTLHOL).
- [ ] **H1** (R2.2) — gateway dual-write: `COMMIT Job(PENDING)` then `publish JobCreated` is not atomic →
  crash between = orphaned `PENDING` job, unrecoverable. **Fix:** add a **`PENDING`-sweeper** coroutine that
  re-publishes `JobCreated` for jobs stuck in `PENDING` past a timeout (the Job row is its own outbox).
- [ ] **H2** (R2.3, R3.2) — parse is a fan-out emitter; an **inbox-skip drops the un-published
  `TtsRequested`** on redelivery → stall in `GENERATING`. **Fix:** never inbox-skip parse; make it
  re-runnable — `INSERT tasks … ON CONFLICT (job_id, block_index) DO NOTHING` + **always re-publish all N**.
- [ ] **H3** (R4.2, R3.2) — fan-in decrement idempotency in Redis `SETNX` (ephemeral, "safe to lose") →
  eviction + redelivery → **double-decrement → early `StitchReady` → incomplete drama marked `COMPLETED`.**
  **Fix:** guard the decrement with a **conditional `UPDATE tasks SET status='DONE' WHERE … status<>'DONE'`
  in the SAME transaction** as the `UPDATE jobs … pending_count-1 RETURNING`. Redis `task:done` becomes a
  fast-path only.
- [ ] **H4** (R3.3, R4.2) — a single poison TTS block goes to `q.dlq` but **never decrements
  `pending_count`** → whole job stalls forever. **Fix:** the DLQ path for a TTS task must resolve the
  barrier — decrement-as-`FAILED` (stitch skips failed blocks) **or** atomically fail the job. Decide & document.

## S1 — duplicate effects / invariant violations

- [ ] **H-EMIT** (R4.2) — crash after decrement returns 0, before `publish StitchReady`+ack → barrier crossed
  in DB, event lost → stall. **Fix:** on redelivery, after the H3 conditional-UPDATE no-ops, **re-read
  `pending_count`; if 0, re-emit `StitchReady`** (make stitch idempotent). Or outbox the stitch event in the tx.
- [ ] **H5** (R4.3, R1.2) — stitch redelivery double-fires the webhook and attempts `COMPLETED→COMPLETED`
  (illegal) → completed job rides the retry ladder into the DLQ. **Fix:** stitch short-circuits — *if already
  `COMPLETED`, ack & return.*
- [ ] **H6** (R4.1) — leased semaphore **over-issues**: slow-but-alive holder + non-atomic reclaim → >3
  concurrent TTS (breaks the named hard limit). **Fix:** heartbeat-renew the lease at ~⅓ TTL (port
  `python-redis-lock`'s `auto_renewal`) + TTL ≫ p99 TTS time; make reclaim a single atomic Lua step
  (`GETDEL`-then-`RPUSH`). Treat the limit as **soft/best-effort**, log breaches.
- [ ] **H-FSM** (R1.2, R4.x) — FSM applied as read-then-write → two workers race the `status` column. **Fix:**
  compare-and-set: `UPDATE jobs SET status=:next WHERE job_id=:id AND status=:expected`; rowcount 0 = lost race.
- [ ] **H8** (R4.2) — cache stampede: N concurrent identical blocks all MISS → N vendor calls + all slots
  burned (defeats Constraint B's cost goal). **Fix:** per-hash in-flight lock (`SET tts:inflight:<hash> NX EX`)
  so only the first synthesizes — **or** accept and document as a deliberate simplification.

## S2 — scale / security / load

- [ ] **H-TTLHOL** (R2.1) — single mixed-TTL retry queue: 1s message stuck behind 16s (per-queue TTL is
  head-only). **Fix:** one delay queue per delay value, uniform TTL each.
- [ ] **H14** (R2.3) — unbounded fan-out: huge manuscript → millions of rows in one tx + millions of messages.
  **Fix:** cap block count / batch inserts+publishes / backpressure.
- [ ] **H15** (R2.3) — parse re-run must **not** reset `pending_count=N` (corollary of H2) → set the counter
  only on the first `PENDING→GENERATING` CAS transition.
- [ ] **H13** (R2.2) — unbounded manuscript body buffered in gateway memory → DoS. **Fix:** max-size guard /
  stream to MinIO.
- [ ] **H-SSRF** (R4.3) — client-supplied `callback_url` → worker `httpx.post` → SSRF. **Fix:** allowlist host,
  block private/link-local ranges, no redirects, timeout. Flag in README.
- [ ] **H-PREFETCH** (R3.1, R4.1) — `PREFETCH=16` vs 3 global slots → 13+ msgs parked unacked on `BLPOP` per
  worker → large crash redelivery blast radius. **Fix:** size prefetch near serviceable concurrency for `q.tts`.

## S3 — hygiene & reference-repo traps

- [ ] **H10** (R3.2) — `processed_events` grows unbounded → add retention/cleanup.
- [ ] **H-DANGLE** (R4.2) — keep MinIO object lifetime ≥ `tts:cache` TTL so a HIT never returns a dangling key.
- [ ] **H-REF1** (R3.1) — retry reference repo is **Celery-flavored + auto-acks** via `message.process()`. Do
  **not** copy its consume loop; mine only the DLX argument shapes.
- [ ] **H-REF2** (R2.1) — that repo ships a **single fixed-delay** retry queue, not the 1/4/16 ladder → build the
  ladder from scratch. (Its custom `x-retries` header approach is H-XDEATH-safe — consider keeping it.)
- [ ] **H-REF3** (R4.1) — the "semaphore" reference is **`python-redis-lock` (sync single mutex)**, not an async
  3-slot semaphore. Port its `auto_renewal` + owner-id-checked Lua unlock *patterns*, not the code.
- [ ] **H-MODELS-IO** (R1.1) — `models.py` imports SQLAlchemy but `core/domain` must be I/O-free → relocate to
  `core/infra/` before R1.1.

---

## Spec changes this implies (do alongside the fixes)

`docs/SPEC.md §4` currently *teaches* several of these bugs. Update it: (1) drop `x-death.count` gating; (2) move
the fan-in idempotency guard from Redis `SETNX` to the conditional `tasks.status` UPDATE; (3) mark parse as a
re-publishable emitter (never inbox-skipped); (4) add the `PENDING`-sweeper; (5) add the DLQ↔fan-in rule; (6) add
stitch idempotency + FSM compare-and-set; (7) add the SSRF/size/block-count security notes. Detail in
`tmp/ARCH-REVIEW-2026-06-24.md §7`.
