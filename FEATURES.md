# FEATURES.md — the executable roadmap to finish the Consuma Audio Engine

> **What this is.** The single, ordered, fine-grained backlog that takes the engine from
> *"gateway + infra adapters built"* to *"passes every resilience probe a grader will run."*
> It consolidates five previously-scattered planning docs — `feature_list.json` (rung ladder),
> `BACKLOG.md` (22 hardening holes), the `snippets/` BOM (~55 cards), `DISPATCH-M1.md`, and the
> stale `PROGRESS.md` — into **one card spine**.
>
> **Who executes it.** A fleet of Sonnet sessions, **one card at a time (WIP = 1)**. Each card is
> written to be handed to a single engineer who *cannot see the other cards*: it carries its own
> context, files, reuse pointer, step-by-step how, the hardening it must fold in, a runnable
> verification command, and an observable acceptance test.
>
> **The prime directive.** This is a **distributed-systems reliability test wearing an audio-drama
> costume** (`docs/SPEC.md` §2). The happy path is worth almost nothing. Build each card *correct
> the first time* — the corrected mechanism for every known failure mode is folded into the card
> that owns it, so nobody ever ships the naïve version and "fixes it later."

---

## 0. How to use this file

1. **Pick the next card.** Cards are ordered by dependency (Phase 0 → 8). Respect `depends_on`;
   never start a card whose prerequisites aren't `passing`.
2. **Read the whole card.** The `MUST` block is not optional polish — it is the difference between
   passing and silently failing a `kill -9` probe.
3. **Build it test-first.** Write the `verify` command's test, watch it fail, implement, watch it pass.
4. **Earn `passing`.** A card is done only when its `verify` runs green **and** `evidence` records
   the proof (commit hash + command output). Never hand-edit a feature to `passing` —
   `check-evidence.py` enforces this.
5. **One card = one logical commit.** Don't "also refactor" a neighbour. WIP = 1 is enforced by
   `check-wip.py`.

> **Absorption into `feature_list.json`.** Each card here maps to one feature entry in the rung
> tracker. When a card is promoted to work, copy its `id`, `behavior` (the title + context), and
> `verification` into `feature_list.json`, set it `in_progress`, and proceed.

---

## 1. What's already built (do not rebuild)

| Layer | State |
|---|---|
| `core/config.py`, `core/domain/{events,state}.py` | ✅ done + unit-tested |
| `core/infra/{db,broker,storage,logging}.py` | ✅ done + integration-tested |
| `core/infra/broker.py` retry ladder | ✅ already H-XDEATH + H-TTLHOL correct (custom `x-retry-count` header, one delay queue per delay) |
| `services/gateway` (`/health`, `POST /jobs`, `GET /status`) | ✅ code + integration tests exist (but `feature_list.json` statuses are **stale** — Phase 0 reconciles) |
| `tests/unit/test_architecture.py` | ✅ enforces gateway⊥worker, domain I/O-free, no banned orchestrators |

**Genuinely unbuilt** (this file's scope): the entire worker pipeline body, `core/infra/redis.py`
(semaphore/cache/inbox), `domain/text.py`, the content-hash helper, `GET /stats`, the PENDING-sweeper,
and **the entire L4 e2e/behavior probe suite — where the grade lives.**

---

## 2. The rubric every card is scored against (`docs/SPEC.md` §2)

| Dim | Name | What it rewards | Cards that carry the weight |
|---|---|---|---|
| **arch** | Architectural choices | *Did you choose, or copy?* Each primitive's boundary defensible in one sentence. | DOC1, DOC2, F0.2, F0.3 |
| **state** | **State across boundaries (highest weight)** | State that can race / disagree / be interrupted: svc↔svc, worker↔worker, broker↔DB, Redis↔PG, before↔after crash. | **B4, W3, W4, G8, R3.2, X4, H-FSM** |
| **edge** | Edge-case handling | 0/1-block jobs, cache-hit-meets-fan-in, parse-crash-mid-write, webhook-fail ≠ job-fail. | W3, W7, W5b, E-EDGE |
| **reliability** | System reliability | `kill -9` at any line → converge correctly. No loss, exactly-once *effect*, no head-of-line block, no leak. | R3.1, R3.3, R4.1, W1, X5, H-PREFETCH |

Every card is tagged `[scores: …]`. When you finish the suite, the four dimensions should each
have multiple green cards behind them.

---

## 3. Global invariants — every card obeys these (CLAUDE.md / SPEC §3–§4)

**State-placement golden rule:** Postgres = durable truth · Redis = ephemeral coordination
(safe to lose) · MinIO = bytes · RabbitMQ = pointers/keys, **never payloads**.

**The 8 MUSTs:**
1. **Ack dead last:** do work → **COMMIT Postgres → PUBLISH next event → ACK message.** Never ack first.
2. **Pointers, never bytes** in a broker message.
3. **Fan-in join via atomic `UPDATE … RETURNING`**, never a Python counter.
4. **3-concurrent TTS limit via a Redis leased semaphore (TTL)**, never `asyncio.Semaphore`.
   **Check the content cache BEFORE acquiring a slot.**
5. **Poison pill → DLQ after 3 retries (exp backoff 1/4/16s) off the hot queue** — no head-of-line block.
6. **`core/domain` stays I/O-free; gateway and worker stay mutually independent.**
7. **Idempotency layers:** event-id inbox (`INSERT … ON CONFLICT DO NOTHING`) · `task:done` fast-path ·
   content cache (`sha256(text)`) · object key = hash.
8. **Webhook/notification failure MUST NOT fail the job** — it is still `COMPLETED`.

**MUST NOT:** managed orchestrators (Celery/Temporal/Airflow/Taskiq/ARQ/RQ/Dramatiq/Prefect) —
`@app.task`/`@shared_task`/`Flower` = wrong path. No `print`/`breakpoint` (ruff T10/T20).

---

## 4. Card schema

```
### <ID> — <title>            [rung Rx.y] [BOM: <card>] [scores: arch|state|edge|reliability]
depends_on: <prerequisite IDs, or "—">
files:    create/modify <paths>
context:  one paragraph — why this exists, which boundary/race it owns
reuse:    <ref-repo file> — copy <X>; do NOT copy <Y>   (or "from scratch — no ref has this")
api:      verified current signatures this card relies on (June 2026)
steps:    1..n concrete implementation steps
MUST:     hardening folded in as imperatives, each tagged (H-id) with the corrected mechanism + how
MUST NOT: the junior-tell anti-patterns to avoid
verify:   exact command — [L1 ruff+mypy] [L2 unit] [L3 integration/testcontainers] [L4 e2e/compose]
accept:   the observable proof
evidence: <filled on completion: commit hash + verify output>
```

`verify` levels map to SPEC note 11: **L1** static → **L2** unit → **L3** integration → **L4** e2e.
A lower level failing blocks the higher. Any cross-component card MUST reach L4 before `passing`.

---

## 5. Phases & dependency DAG

```
Phase 0  Foundation / reconcile        F0.1  F0.2  F0.3  F0.4
              │
Phase 1  Domain pure logic (no Docker) D3  D4  R2.0  H-FSM
              │
Phase 2  Redis coordination layer      R1 → R2 → X4 → X5 ;  R3 → H8 ;  R4inbox
              │                              └────────────┐
Phase 3  DB query layer                B4 (needs R4inbox) ;  H15 ;  B6
              │
Phase 4  Worker pipeline body          X1 X3 X2 W1 X7 W2 → W3 → W4 (needs R2,R3,B4) → W7 → W5 → W5b ; H-SSRF
              │
Phase 5  Gateway completion            G7 (needs B6)  G8 (needs W3 idempotent)  H13
              │
Phase 6  L4 e2e / behavior probes      T1 → {R3.1 R3.2 R3.3 R4.1 R4.2 R4.3 E-EDGE} → T-BEHAVIOR
              │
Phase 7  Infra verification            I1 I2 I3 I4 ; H-DANGLE ; H-PREFETCH
              │
Phase 8  Architecture-defense docs     DOC1  DOC2
```

Full card text lives in the phase files:

- [`docs/features/00-foundation.md`](docs/features/00-foundation.md)
- [`docs/features/01-domain.md`](docs/features/01-domain.md)
- [`docs/features/02-redis.md`](docs/features/02-redis.md)
- [`docs/features/03-db-queries.md`](docs/features/03-db-queries.md)
- [`docs/features/04-worker.md`](docs/features/04-worker.md)
- [`docs/features/05-gateway.md`](docs/features/05-gateway.md)
- [`docs/features/06-e2e.md`](docs/features/06-e2e.md)
- [`docs/features/07-infra.md`](docs/features/07-infra.md)
- [`docs/features/08-docs.md`](docs/features/08-docs.md)

---

## 6. Traceability — nothing dropped

### 6.1 Every `not_started` `feature_list.json` item → card(s)

| feature_list id | Behavior | Card(s) |
|---|---|---|
| R2.0 | Vendor sim & fault injection | **R2.0** |
| R2.2a/b/c/R2.2/R2.2d | Gateway lifespan/schemas/CORS/ingestion/status (built; status stale) | **F0.1** (reconcile + record evidence) |
| R2.3 | Parse fan-out, 0-block terminates | **W3** |
| R3.1 | Crash recovery (ack-last, redelivery) | **W1**, **W2**, **R3.1** |
| R3.2 | Idempotent consumers (inbox + task:done) | **R4inbox**, **R3.2** |
| R3.3 | DLQ after 3 backoff, no HOL block | **W7**, **R3.3** |
| R3.4 | PENDING-sweeper/reconciler | **G8** |
| R4.1 | Global TTS leased semaphore | **R2**, **X4**, **X5**, **R4.1** |
| R4.2 | TTS cache-before-slot + atomic fan-in | **R3**, **B4**, **W4**, **R4.2** |
| R4.3 | Stitch & notify, webhook-fail ≠ job-fail | **W5**, **W5b**, **R4.3** |
| R5.1 | `GET /stats` | **B6**, **G7** |

### 6.2 Every BACKLOG hole → card that folds in the fix

| Hole | Sev | Folded into |
|---|---|---|
| H-XDEATH | S0 | already in `broker.py`; re-asserted in **F0.4**, **R3.3** |
| H1 | S0 | **G8** (PENDING-sweeper) |
| H2 | S0 | **W3** (ON CONFLICT, never inbox-skip parse, always re-publish N) |
| H3 | S0 | **B4** (conditional `tasks.status` UPDATE in the decrement tx) |
| H4 | S0 | **W7** (DLQ path resolves the barrier) |
| H-EMIT | S1 | **W4** (re-read count + re-emit StitchReady on redelivery) |
| H5 | S1 | **W5** (stitch short-circuits if already COMPLETED) |
| H6 | S1 | **X5** (heartbeat-renew + atomic Lua reclaim; soft limit) |
| H-FSM | S1 | **H-FSM** card (compare-and-set status UPDATE) |
| H8 | S1 | **H8** card (per-hash in-flight lock or documented simplification) |
| H-TTLHOL | S2 | already in `broker.py`; re-asserted in **R3.3** |
| H14 | S2 | **W3** (block-count cap / batch) |
| H15 | S2 | **H15** card (counter set only on first CAS) |
| H13 | S2 | **H13** card (manuscript max-size guard) |
| H-SSRF | S2 | **H-SSRF** card (allowlist, block private ranges, no redirects, timeout) |
| H-PREFETCH | S2 | **W1** / **H-PREFETCH** card (prefetch sized to concurrency) |
| H10 | S3 | **R4inbox** (processed_events retention) |
| H-DANGLE | S3 | **H-DANGLE** card (object lifetime ≥ cache TTL) |
| H-REF1 | S3 | **W1** (don't copy ref auto-ack loop — noted in reuse) |
| H-REF2 | S3 | n/a (ladder already built from scratch in `broker.py`) |
| H-REF3 | S3 | **R2** (port lock patterns, not code) |
| H-MODELS-IO | S3 | done (models live in `infra/db.py`); **F0.3** removes the dead stub |

### 6.3 The 7 SPEC §4 corrections → **DOC1**

Drop x-death gating · fan-in via conditional `tasks.status` UPDATE · parse re-publishable ·
PENDING-sweeper · DLQ↔fan-in rule · stitch idempotency + FSM CAS · SSRF/size/block-count notes.

---

*Phase files follow. Each is independently executable; this index is the map.*
