"""FastAPI app entrypoint (spec §5).

Rung 0 boot: app + GET /health so the gateway is runnable under compose and proves
the service starts. Job endpoints (POST /jobs, GET /status) arrive in later rungs.

Run by compose as: uvicorn gateway.main:app
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Consuma Audio Engine — Gateway")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe — used by docker-compose healthcheck and the init.sh wait loop."""
    return {"status": "ok"}
