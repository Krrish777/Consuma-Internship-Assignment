# Consuma Audio Engine

An async pipeline that turns a text manuscript into a (simulated) produced audio drama:
split the script into blocks, synthesise each block's audio in parallel, then stitch the
blocks back into one final track.

The point of the system is **not** the happy path — it's reliability. The services are
**choreographed, with no central orchestrator**: each stage reacts to a broker event and
publishes the next. Correctness under crashes, duplicate deliveries, and poison input comes
from where state is placed and how each "commit-then-publish" boundary is made safe, not from
a coordinator. The design rationale lives in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Architecture

![Architecture](docs/assets/architecture.png)

```
client → gateway → q.parse → (fan-out) q.tts → (fan-in) q.stitch → COMPLETED
                                  │
                              q.dlq (poison, after 3 retries)
```

- **Gateway** (FastAPI) ingests a manuscript, persists the job, and publishes `JobCreated`.
- **Parse** splits the manuscript into N blocks and fans out N `TtsRequested` events.
- **TTS** synthesises each block (capped at 3 concurrent via a leased Redis semaphore, with a
  content cache so identical blocks are synthesised once). The last block to finish converges
  the fan-in and emits `StitchReady`.
- **Stitch** concatenates the blocks into the final asset, marks the job `COMPLETED`, and fires
  the optional webhook.

**State placement (the golden rule):** Postgres = durable truth (job/task state, the fan-in
counter) · Redis = ephemeral coordination (semaphore, cache) · MinIO = bytes (manuscript, audio)
· RabbitMQ = pointers, never payloads.

## Run

**Prerequisites:** Docker (Desktop) and [`uv`](https://docs.astral.sh/uv/). Python 3.13.

```bash
./init.sh
```

One shot: syncs dependencies, builds and starts the 6-service stack
(Postgres · RabbitMQ · Redis · MinIO · gateway · worker), waits for health, and runs a unit +
one-job end-to-end smoke test. Or bring up the stack directly:

```bash
docker compose up --build
docker compose up --scale worker=4   # scale workers to exercise the global semaphore
```

**API** (gateway on `http://localhost:8000`):

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/jobs` | Submit a manuscript → returns a `job_id` |
| `GET`  | `/status/{job_id}` | Current job status |
| `GET`  | `/stats` | Job counts by status |

```bash
# submit a job
curl -X POST http://localhost:8000/jobs \
  -H 'Content-Type: application/json' \
  -d '{"manuscript": "A single produced block of dialogue."}'

# poll its status
curl http://localhost:8000/status/<job_id>
```

**Consoles:** RabbitMQ — http://localhost:15672 (`guest`/`guest`) · MinIO — http://localhost:9001
(`minioadmin`/`minioadmin`).

## Verify

```bash
make check       # no Docker: ruff lint + format, mypy --strict, unit tests
make check-all   # adds integration (testcontainers) + end-to-end tests (needs Docker)
```

The reliability guarantees are demonstrable end-to-end:

```bash
make demo-crash      # kill a worker mid-job → the job is redelivered and still completes
make demo-poison     # a poison block exhausts its retry ladder → routed to the DLQ off the hot queue
make demo-duplicate  # a duplicate delivery is absorbed (idempotent effect, no double work)
make demo            # all three, narrated, for a recording
```

## Where to read more

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — the design defense: data-placement, the atomic fan-in,
  the four crash seams, and "exactly-once *effect*, not delivery."
