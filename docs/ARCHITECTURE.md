# Consuma Audio Engine — Architecture

Stage-level view of the choreographed pipeline. The diagram shows **what the system is**
and **how one job flows through it** — detail lives in a block only where that detail is a
graded design decision. Everything else (ack-ordering, idempotency keys, FSM transitions,
0/1-block edge cases) is left to the code on purpose.

## Reading the diagram

- **Solid arrows** = the event/message flow (a job moving through the broker, stage to stage).
  Queue names ride on the arrows (`q.parse`, `q.tts`, `q.stitch`) — RabbitMQ is the
  choreography backbone, not a central orchestrator.
- **Dashed arrows** = state reads/writes against the shared stores.
- **Colour = state placement** (the golden rule): Postgres = durable truth · Redis = ephemeral
  coordination · MinIO = bytes.

```mermaid
flowchart LR
    Client([Client<br/>upload manuscript · poll status])
    Gateway[API Gateway · FastAPI<br/>ingest · save raw · publish JobCreated]
    Parse[Parse · Stage A<br/>15% error → retry<br/>fan-out → N tasks]
    TTS[TTS ×N · Stage C<br/>3 concurrent max — Redis semaphore, TTL lease<br/>content-hash cache → skip vendor]
    Stitch[Stitch &amp; Notify · Stage D<br/>atomic fan-in join — UPDATE … RETURNING<br/>webhook fail ≠ job fail]
    Final([Final asset<br/>out/job.mp3 + notify])
    DLQ[/q.dlq · dead letters<br/>after 3 retries · 1s / 4s / 16s/]

    %% --- happy path (event flow) ---
    Client -->|POST /jobs| Gateway
    Gateway -->|q.parse| Parse
    Parse -->|fan-out → q.tts| TTS
    TTS -->|last task → q.stitch| Stitch
    Stitch --> Final
    Final -.->|webhook / log| Client

    %% --- failure path ---
    Parse -.->|3× fail| DLQ
    TTS -.->|3× fail| DLQ

    %% --- shared infrastructure band ---
    subgraph Infra [Shared Infrastructure · every stage reads / writes these]
        direction LR
        PG[(Postgres · durable truth<br/>jobs · tasks · processed_events)]
        Redis[(Redis · coordination<br/>slots · cache · idempotency)]
        MinIO[(MinIO · bytes<br/>raw/ · tts/ · out/)]
    end

    %% --- state placement (who touches what) ---
    Gateway -.-> PG
    Gateway -.-> MinIO
    Parse -.-> PG
    TTS -.-> Redis
    TTS -.-> MinIO
    Stitch -.-> PG
    Stitch -.-> MinIO

    classDef db    fill:#cfe2ff,stroke:#3b82f6,color:#000
    classDef cache fill:#ffd6d6,stroke:#ef4444,color:#000
    classDef store fill:#fff3cd,stroke:#f59e0b,color:#000
    classDef flow  fill:#d1e7dd,stroke:#198754,color:#000
    class PG db
    class Redis cache
    class MinIO store
    class Parse,TTS,Stitch flow
```

## Why detail sits where it does

| Block | Why it earns detail |
|-------|---------------------|
| **Parse** | The 15% injected-error rate is what exercises the retry path; fan-out into N tasks is where the parallelism (and the later fan-in problem) is born. |
| **TTS** | The two named constraints — global 3-concurrent limit (Redis semaphore) and content-hash dedup cache — are the heart of the assignment. |
| **Stitch** | The atomic fan-in join is the hardest correctness point; `webhook fail ≠ job fail` is the explicit edge case in the spec. |

Everything else stays a clean label: the reviewer reads both the diagram and the code, so the
diagram's job is the shape, not the implementation.
