# Consuma Audio Engine

An async pipeline that turns a text manuscript into a (simulated) produced audio drama. It
splits the script into blocks, synthesises each block's audio in parallel, then stitches the
blocks back into one final track.

The focus here is reliability rather than the happy path. The services are choreographed with
no central orchestrator: each stage reacts to a broker event and publishes the next one.
Correctness under crashes, duplicate deliveries, and bad input comes from where state is kept
and how each "commit then publish" boundary is made safe, not from a coordinator. The reasoning
behind the design is in [ARCHITECTURE.md](ARCHITECTURE.md).

## Architecture

![Architecture](docs/assets/architecture.png)

```
client -> gateway -> q.parse -> (fan-out) q.tts -> (fan-in) q.stitch -> COMPLETED
                                    |
                                q.dlq (poison, after 3 retries)
```

- The gateway (FastAPI) takes a manuscript, saves the job, and publishes `JobCreated`.
- Parse splits the manuscript into N blocks and fans out N `TtsRequested` events.
- TTS synthesises each block. At most 3 run at once, enforced by a leased Redis semaphore, and
  a content cache means identical blocks are only synthesised once. Whichever block finishes
  last closes the fan-in and emits `StitchReady`.
- Stitch concatenates the blocks into the final asset, marks the job `COMPLETED`, and fires the
  optional webhook.

Where state lives, and why:

- Postgres holds the durable truth: job and task state, plus the fan-in counter.
- Redis handles ephemeral coordination: the semaphore and the cache.
- MinIO stores the bytes: the manuscript, the per-block audio, and the final file.
- RabbitMQ carries pointers, never the audio itself.

## Run

You'll need Docker (Desktop) and [uv](https://docs.astral.sh/uv/). Python 3.13.

```bash
./init.sh
```

That does everything in one go: syncs dependencies, builds and starts the six-service stack
(Postgres, RabbitMQ, Redis, MinIO, gateway, worker), waits for health, and runs a unit plus
one-job end-to-end smoke test. If you'd rather bring the stack up yourself:

```bash
docker compose up --build
docker compose up --scale worker=4   # more workers, to exercise the global semaphore
```

The gateway listens on `http://localhost:8000`:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/jobs` | Submit a manuscript, get back a `job_id` |
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

The management consoles are handy while a job runs: RabbitMQ at http://localhost:15672
(guest/guest) and MinIO at http://localhost:9001 (minioadmin/minioadmin).

## Verify

```bash
make check       # no Docker: ruff lint + format, mypy --strict, unit tests
make check-all   # also runs integration (testcontainers) and end-to-end tests (needs Docker)
```

The reliability behaviour can be shown end to end:

```bash
make demo-crash      # kill a worker mid-job; the job is redelivered and still completes
make demo-poison     # a poison block exhausts its retries and lands in the DLQ, off the hot queue
make demo-duplicate  # a duplicate delivery is absorbed, with no double work
make demo            # all three, narrated, for a recording
```

## Where to read more

- [ARCHITECTURE.md](ARCHITECTURE.md) covers the design: where data lives, the atomic fan-in,
  the four crash points, and why this is exactly-once in effect rather than in delivery.
- [docs/SPEC.md](docs/SPEC.md) is the requirements, and the source of truth.
- [docs/DECISIONS.md](docs/DECISIONS.md) is the decision log and the known limits.
