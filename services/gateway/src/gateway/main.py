"""FastAPI app entrypoint — gateway (spec §5, rungs R0.1, R2.2a-d).

Lifespan: opens broker connection + DB engine + MinIO bucket once on startup,
stashes on app.state, closes cleanly on shutdown. All handlers share one
connection (not per-request) to avoid flooding the broker.

Ingestion (R2.2):
  POST /jobs: put_text raw/<job>.txt → insert Job(PENDING) + COMMIT →
              publish JobCreated to q.parse → 202 + {job_id}

  The dual-write order is load-bearing (BACKLOG H1 — sweeper closes the gap):
    MinIO write is safe to repeat; DB commit is the durable record; publish is
    the trigger. If we crash between commit and publish, the PENDING-sweeper
    (R3.4) re-publishes. If we crash before commit, the job row never exists
    and nothing happens.

Status (R2.2d):
  GET /status/{job_id}: returns job status or 404.

Run by compose as: uvicorn gateway.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from minio import Minio
from sqlalchemy import select

from core.config import get_settings
from core.domain.events import JobCreated
from core.domain.state import JobStatus
from core.infra import broker
from core.infra.db import Job, get_engine, get_session
from core.infra.logging import bind_job_id, configure_logging, get_logger
from core.infra.queries import job_counts_by_status
from core.infra.storage import ensure_bucket, put_text

from gateway.schemas import CreateJobRequest, JobAccepted, JobStatusResponse, StatsResponse

configure_logging()
log = get_logger("gateway")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    connection = await broker.connect(settings.RABBITMQ_URL)
    channel = await connection.channel()
    exchange = await broker.declare_full(channel)

    engine = get_engine(settings.DATABASE_URL)

    minio_client = Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS,
        secret_key=settings.MINIO_SECRET,
        secure=False,
    )
    await ensure_bucket(minio_client)

    app.state.exchange = exchange
    app.state.engine = engine
    app.state.minio = minio_client
    app.state.settings = settings

    log.info("gateway startup complete")
    yield

    await connection.close()
    await engine.dispose()
    log.info("gateway shutdown complete")


app = FastAPI(title="Consuma Audio Engine — Gateway", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    job_id = request.path_params.get("job_id", "")
    log.exception("unhandled error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "job_id": job_id or None},
    )


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe — used by docker-compose healthcheck and init.sh."""
    return {"status": "ok"}


@app.post("/jobs", response_model=JobAccepted, status_code=202)
async def create_job(body: CreateJobRequest, request: Request) -> JobAccepted:
    """Ingest a manuscript: store → record → publish.

    Order matters (BACKLOG H1):
      1. put_text to MinIO (idempotent key = raw/<job_id>.txt)
      2. INSERT Job(PENDING) + COMMIT (durable record)
      3. THEN publish JobCreated (trigger)
    Crash between 2 and 3 is recovered by the PENDING-sweeper (R3.4).
    """
    job_id = uuid.uuid4().hex
    bind_job_id(job_id)
    minio: Minio = request.app.state.minio
    engine = request.app.state.engine
    exchange = request.app.state.exchange

    manuscript_key = f"raw/{job_id}.txt"
    await put_text(minio, manuscript_key, body.manuscript)

    async with get_session(engine) as session:
        job = Job(
            job_id=job_id,
            status=JobStatus.PENDING,
            manuscript_key=manuscript_key,
            callback_url=body.callback_url,
        )
        session.add(job)
        await session.commit()

    event = JobCreated(job_id=job_id)
    await broker.publish(exchange, event, routing_key=broker.Q_PARSE)

    log.info("job created", extra={"job_id": job_id})
    return JobAccepted(job_id=job_id)


@app.get("/stats", response_model=StatsResponse)
async def stats(request: Request) -> StatsResponse:
    """R5.1 — runtime job counts by status. Read-only; no locks, no writes.

    Counts are computed by B6's SQL ``GROUP BY`` aggregate (never by scanning
    rows in Python), then zero-filled across every FSM state so the response
    shape is stable even when a status has no rows.
    """
    engine = request.app.state.engine

    async with get_session(engine) as session:
        counts = await job_counts_by_status(session)

    jobs = {status.value: counts.get(status.value, 0) for status in JobStatus}
    log.info("stats served", extra={"jobs": jobs})
    return StatsResponse(jobs=jobs)


@app.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_status(job_id: str, request: Request) -> JobStatusResponse:
    """Return current job status. 404 if job_id is unknown."""
    bind_job_id(job_id)
    engine = request.app.state.engine

    async with get_session(engine) as session:
        result = await session.execute(select(Job).where(Job.job_id == job_id))
        job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status.value,
        pending_count=job.pending_count,
        manuscript_key=job.manuscript_key,
        final_key=job.final_key,
    )
