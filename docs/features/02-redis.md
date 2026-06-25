# Phase 2 — Redis coordination layer

> `core/infra/redis.py` is the last unbuilt infra adapter. It owns three ephemeral-coordination
> jobs: the **global TTS semaphore** (Constraint A), the **content cache** (Constraint B), and the
> **idempotency fast-path** (exactly-once effect). Everything here is "safe to lose / rebuildable"
> by the golden rule — durable truth stays in Postgres.
>
> **Verified stack (June 2026):** redis-py **8.0.1**, `import redis.asyncio as redis`.
> `redis.from_url(url)` is **NOT awaited** (returns a client). Close with `await client.aclose()`.
> `aioredis` is dead — merged into redis-py; never import it.

---

### R1 — Async Redis client adapter   [rung R4.1] [BOM: 06-R1] [scores: arch]
depends_on: —
files: create `packages/core/src/core/infra/redis.py`, `tests/integration/test_redis.py`
context: One place that builds the `redis.asyncio` client from `REDIS_URL`, so semaphore/cache/inbox
all share connection config. Mirrors the shape of the other infra adapters (`db.py`, `broker.py`).
reuse: structural mirror of `infra/db.py`'s `get_engine`/`get_session`.
api: `import redis.asyncio as redis` · `client = redis.from_url(url, decode_responses=False)` (NOT awaited)
  · `await client.ping()` · `await client.aclose()`.
steps:
  1. `def get_redis(url: str) -> redis.Redis:` returning `redis.from_url(url, decode_responses=False)`
     (keep bytes — we store/compare hashes and tokens; document the choice).
  2. Add an async health helper `async def ping(client) -> bool`.
  3. testcontainers `RedisContainer` fixture in `test_redis.py` (declare `redis` dep; the
     `testcontainers[redis]` extra no longer bundles drivers — same caveat as Postgres).
MUST: use `redis.asyncio` (NOT the sync `redis` client, NOT `aioredis`) — async worker (H-REF3).
MUST: close via `aclose()` on shutdown (8.x renamed from `close()`).
MUST NOT: create a client per operation — build once, share via the worker bootstrap (X3).
verify: [L3] `uv run pytest tests/integration -k redis` — ping round-trips against a real container.
accept: a shared async client connects and pings.
evidence:

---

### R2 — Leased N-token semaphore (the global TTS limit)   [rung R4.1] [BOM: 06-R2] [scores: state, reliability]
depends_on: R1
files: modify `core/infra/redis.py` (add `Semaphore`), extend `tests/integration/test_redis.py`
context: Constraint A — only **3 concurrent TTS requests globally across ALL workers** (SPEC §1).
Must be a **Redis** distributed semaphore, never `asyncio.Semaphore` (that's per-process). The
mechanism: a Redis list pre-seeded with N tokens; **`BLPOP` to acquire** (blocks without busy-poll),
**`RPUSH` to release**. Each held token is also recorded as a **lease with TTL** so a crashed
holder's slot auto-reclaims (X5) — otherwise the pool of 3 silently shrinks to 0 → deadlock.
reuse: `tmp/Consuma-Reference-Repos/redis-lock-semaphore` — port the **BLPOP-on-signal-list + TTL-lease
  + owner-id-checked Lua unlock** *patterns* only. Do NOT copy the code: it's sync (`threading`) and a
  single-token mutex (H-REF3). You generalize to async + N tokens.
api: `await r.rpush(key, *tokens)` · `key_b, token = await r.blpop([slots_key], timeout=...)` (returns
  a `(key, value)` tuple of bytes, or `None` on timeout) · `await r.lpush(slots_key, token)` to release ·
  `await r.set(f"tts:lease:{token}", owner, nx=True, ex=LEASE_TTL_S)` to record the lease.
steps:
  1. `class Semaphore` with `slots_key="tts:slots"`, `slots:int`, `lease_ttl:int`.
  2. `async def acquire(self, owner) -> str:` — `BLPOP tts:slots` (blocks), then SET a lease key with
     TTL for the popped token; return the token (used as a context handle).
  3. `async def release(self, token) -> None:` — delete the lease key, `RPUSH` the token back.
  4. Provide an `async with` wrapper (acquire on enter, release on exit, release even on exception).
MUST: acquire via blocking `BLPOP` (no busy-poll) and treat each token as a TTL **lease** (H6) so a
  killed holder's slot returns automatically.
MUST: **check the content cache BEFORE calling `acquire`** (that's W4's order) — a cache hit must not
  burn a token (SPEC §4).
MUST NOT: use `asyncio.Semaphore` or any in-process counter (would not bound across workers/containers).
verify: [L3] `uv run pytest tests/integration -k redis` — with slots=2, a 3rd `acquire` blocks until a
  `release`; never more than `slots` tokens out at once.
accept: at most N concurrent holders against a real Redis; release returns the token.
evidence:

---

### X4 — Semaphore init idempotency (the 3×N tokens bug)   [rung R4.1] [BOM: 12-X4] [scores: state, reliability]
depends_on: R2
files: modify `core/infra/redis.py` (add `ensure_slots`), extend `test_redis.py`
context: **The named footgun.** Every worker boots and wants to seed the semaphore. If each naïvely
`RPUSH`es N tokens, M workers create M×N tokens → the "3-concurrent" limit silently becomes 3×M.
Initialization must be **exactly-once across all workers** and converge to exactly N tokens regardless
of how many workers call it or how many times.
reuse: from scratch (no ref repo addresses multi-initializer convergence).
api: atomic Lua via `script = r.register_script(lua)` (register is sync, the call is awaited):
  check `LLEN slots_key` + a one-time init flag, seed only the shortfall under a single atomic script.
steps:
  1. `async def ensure_slots(self) -> None:` running a Lua script that, atomically: if an init-marker
     key is unset, `DEL slots_key`, `RPUSH` exactly N tokens, set the marker; else no-op.
  2. Call it once from the worker bootstrap (X3) on every worker — the Lua atomicity makes concurrent
     calls safe and convergent.
  3. Token identity: use distinct token values (`"0".."N-1"`) so leases (X5) can be attributed.
MUST: converge to **exactly N tokens** no matter how many workers call `ensure_slots` concurrently
  (atomic Lua, not read-then-RPUSH).
MUST NOT: `RPUSH` N tokens unconditionally on boot (the 3×N bug).
verify: [L3] `uv run pytest tests/integration -k redis` — call `ensure_slots` from 5 simulated workers
  concurrently → `LLEN tts:slots == N` exactly.
accept: N concurrent initializers leave exactly N tokens.
evidence:

---

### X5 — Lease reaper: heartbeat renew + atomic reclaim   [rung R4.1] [BOM: 12-X5] [scores: reliability]
depends_on: R2, X4
files: modify `core/infra/redis.py`, extend `test_redis.py`
context: H6 — a slow-but-alive holder can outlive its lease TTL → its token gets reclaimed → **>3
concurrent TTS** (breaks the hard limit). Fix per the `python-redis-lock` pattern: **heartbeat-renew**
the lease at ~⅓ TTL while work proceeds, set TTL ≫ p99 TTS time, and make reclaim a **single atomic
Lua step** (owner-checked `GETDEL`-then-`RPUSH`) so two reapers can't double-return one token. Treat
the limit as **soft/best-effort** and log breaches (a distributed semaphore cannot be perfectly hard
without consensus — say so).
reuse: `redis-lock-semaphore` `auto_renewal` + owner-id-checked Lua unlock patterns (port, don't copy).
api: renewal task using `await asyncio.sleep(ttl/3)` + `await r.set(lease, owner, xx=True, ex=ttl)`
  (XX = renew only if still held); reclaim Lua checks owner before `RPUSH`.
steps:
  1. In `acquire`, spawn a heartbeat task that re-`SET ... XX EX` the lease every `ttl/3`; cancel on release.
  2. `async def reap(self) -> int:` — scan for expired-but-not-returned tokens; atomically (Lua) verify
     the lease is gone and `RPUSH` the token back exactly once; return count reclaimed.
  3. Document "soft limit, best-effort; breaches logged" in the module docstring (honesty scores arch points).
MUST: reclaim atomically (Lua) so concurrent reapers return a token at most once (H6).
MUST: renew the lease while alive so a healthy-but-slow holder is never reclaimed.
MUST NOT: claim a perfectly hard global limit — log breaches and call it best-effort.
verify: [L3] `uv run pytest tests/integration -k redis` — a holder that stops heartbeating has its token
  reclaimed after TTL; a heartbeating holder keeps its token; two reapers reclaim a token once.
accept: crashed holder's slot returns; live holder's slot persists; no double-return.
evidence:

---

### R3 — Content-hash cache (Constraint B)   [rung R4.2] [BOM: 06-R3] [scores: state]
depends_on: R1
files: modify `core/infra/redis.py`, extend `test_redis.py`
context: Constraint B / cost-idempotency — identical text sent twice must **not** re-hit the vendor
(SPEC §1). Cache maps `tts:cache:<sha256(text)>` → the prior MinIO object URL/key, with a TTL. Checked
**before** acquiring a semaphore slot (a hit burns no token).
reuse: from scratch (the structure-reference cache is resource-id keyed, not content-hash — different).
api: `await r.setex(name, seconds, value)` — **arg order is (name, seconds, value)**, seconds is the
  middle arg · `await r.get(name)` → bytes or None.
steps:
  1. `async def cache_get(self, h: str) -> str | None:` → `GET tts:cache:{h}` (decode).
  2. `async def cache_set(self, h: str, url: str) -> None:` → `SETEX tts:cache:{h} CACHE_TTL_S url`.
  3. Keys use the canonical `content_hash` (D4) — never the task_id (don't conflate cache with counter).
MUST: key on `sha256(text)` (D4), TTL'd; consulted before slot acquire (W4 order).
MUST: keep `CACHE_TTL_S` ≤ the MinIO object lifetime so a HIT never returns a dangling key (H-DANGLE).
MUST NOT: cache by task_id, and MUST NOT let the cache become durable truth (it's rebuildable from MinIO).
verify: [L3] `uv run pytest tests/integration -k redis` — set then get returns the url; missing hash → None;
  entry expires after TTL.
accept: hash→url round-trips with TTL; miss returns None.
evidence:

---

### H8 — Cache stampede in-flight lock   [rung R4.2] [BOM: backlog-H8] [scores: reliability]
depends_on: R3
files: modify `core/infra/redis.py`, extend `test_redis.py`
context: H8 — N concurrent identical blocks all MISS the cache simultaneously → N vendor calls + N
slots burned, defeating Constraint B's cost goal. Fix: a per-hash **in-flight lock**
(`SET tts:inflight:<hash> NX EX`) so only the first synthesizes; the others wait for the cache to
populate, then read it. *Alternatively*, if you judge the added complexity not worth it, **document it
as a deliberate simplification** (the grader rewards a conscious, defended choice over an unconsidered gap).
reuse: from scratch.
api: `await r.set(f"tts:inflight:{h}", owner, nx=True, ex=...)` → `None` if already held (someone else
  is synthesizing); poll `cache_get` with backoff or BLPOP a per-hash signal.
steps:
  1. `async def acquire_inflight(self, h) -> bool:` → True if this caller won the NX (it synthesizes).
  2. Losers wait (bounded) for `cache_get(h)` to return, then use it — no vendor call, no slot.
  3. Release the in-flight lock after cache_set (or let it expire).
MUST: ensure at most one vendor call per identical-block burst (H8), OR document the simplification
  explicitly in the module + DOC2.
MUST NOT: hold a TTS semaphore slot while waiting on the in-flight lock (would deadlock the pool).
verify: [L3] `uv run pytest tests/integration -k redis` — K concurrent `acquire_inflight` for one hash →
  exactly one True; the rest observe the populated cache. (If simplified: a test asserting the documented
  behavior + a DOC2 note.)
accept: one synthesizer per identical-block burst (or a documented, tested simplification).
evidence:

---

### R4inbox — Idempotency fast-path + inbox helpers   [rung R3.2] [BOM: 06-R4 / 03-B5] [scores: state]
depends_on: R1
files: modify `core/infra/redis.py` (`seen_once`), modify `core/infra/db.py` (inbox insert helper),
  extend `test_redis.py` + `tests/integration/test_models.py`
context: Exactly-once **effect** = at-least-once delivery + idempotent processing. Two layers (SPEC §4):
the durable **`processed_events` inbox** (`INSERT … ON CONFLICT DO NOTHING`) is the authority; a Redis
`task:done` `SETNX` is only a **fast-path** to short-circuit obvious duplicates cheaply. The durable
guard, not Redis, is what protects the fan-in counter (that's B4/H3) — Redis here is just an optimization.
reuse: existing `ProcessedEvent` model in `infra/db.py`.
api: durable: `from sqlalchemy.dialects.postgresql import insert` (the **dialect** insert, which has
  `.on_conflict_do_nothing`) → `insert(ProcessedEvent).values(event_id=eid).on_conflict_do_nothing()`;
  rowcount tells you first-vs-duplicate. fast-path: `await r.set(f"task:done:{tid}", "1", nx=True, ex=...)`
  → `None` if already seen.
steps:
  1. DB helper `async def mark_event(session, event_id) -> bool:` — returns True if newly inserted
     (first time), False if conflict (duplicate). Caller commits in its own tx.
  2. Redis helper `async def seen_once(self, task_id) -> bool:` — SETNX fast-path.
  3. H10 retention: a periodic `DELETE FROM processed_events WHERE consumed_at < now() - retention`
     (wire into the sweeper or a small reaper).
MUST: the **durable inbox** is the authority; Redis `task:done` is a non-authoritative fast-path (H3 —
  never let ephemeral Redis be the thing protecting the counter).
MUST: NEVER inbox-skip the **parse** handler (H2) — parse is a fan-out emitter; skipping it drops the
  un-published TtsRequested. (Parse uses ON CONFLICT on the task rows + always re-publishes; see W3.)
MUST: add retention for `processed_events` (H10) — it grows unbounded otherwise.
MUST NOT: rely on Redis surviving — it's "safe to lose"; on a cold Redis the durable inbox still holds.
verify: [L3] `uv run pytest tests/integration -k "models or redis"` — `mark_event` twice for one id →
  True then False; `seen_once` twice → True then False; retention deletes aged rows.
accept: duplicate event ids are absorbed durably; fast-path short-circuits; old inbox rows pruned.
evidence:
