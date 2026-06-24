"""FastAPI app entrypoint (spec §5).

STUB. Endpoints arrive across Rung 0-5:
  GET  /health            (0.2)
  POST /jobs              (0.4, 1.3) -> 202 {job_id}; claim-check manuscript to MinIO
  GET  /status/{job_id}   (0.4)
  GET  /stats             (5.1) observability

Run by compose as: uvicorn gateway.main:app
"""
