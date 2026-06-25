#!/usr/bin/env bash
# init.sh — one-shot environment bring-up + readiness check (harness note 7, "clock in").
# Brings up the 6-service stack, waits for health, runs the no-Docker smoke test.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not found on PATH. Install Docker Desktop and retry." >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found on PATH. Install uv (https://docs.astral.sh/uv/) and retry." >&2
  exit 1
fi

echo "==> Syncing workspace dependencies..."
uv sync

echo "==> Building & starting the 6-service stack..."
docker compose up -d --build

echo "==> Waiting for services to report healthy (up to ~120s)..."
deadline=$((SECONDS + 120))
while (( SECONDS < deadline )); do
  # Count services whose Health is neither 'healthy' nor empty (empty = no healthcheck).
  not_ready=$(docker compose ps --format '{{.Health}}' | grep -cvE '^(healthy|)$' || true)
  if [ "${not_ready}" -eq 0 ]; then
    echo "==> All services healthy."
    break
  fi
  sleep 3
done

echo "==> Running no-Docker smoke test..."
uv run pytest -q tests/unit

# One-job end-to-end smoke (I3): submit a tiny manuscript through the LIVE gateway and
# confirm it reaches COMPLETED — a sanity beyond unit tests that the wired pipeline
# (gateway -> q.parse -> worker parse/tts/stitch -> Postgres/MinIO) actually works.
# Guarded so it's skippable: set INIT_SMOKE=0 to skip. Bounded (60s) so a broken
# pipeline fails loudly with a non-zero exit instead of hanging the bring-up.
if [ "${INIT_SMOKE:-1}" = "1" ]; then
  echo "==> Running one-job end-to-end smoke (set INIT_SMOKE=0 to skip)..."
  resp=$(curl -fsS -X POST http://localhost:8000/jobs \
    -H 'Content-Type: application/json' \
    -d '{"manuscript":"init.sh smoke: a single produced block."}')
  job_id=$(printf '%s' "$resp" | grep -o '"job_id":"[^"]*"' | sed 's/.*:"//;s/"$//')
  if [ -z "${job_id}" ]; then
    echo "ERROR: smoke job submission failed (response: ${resp})" >&2
    exit 1
  fi
  echo "    submitted job ${job_id}; polling /status (up to 60s)..."
  smoke_deadline=$((SECONDS + 60))
  status=""
  while (( SECONDS < smoke_deadline )); do
    status=$(curl -fsS "http://localhost:8000/status/${job_id}" \
      | grep -o '"status":"[^"]*"' | sed 's/.*:"//;s/"$//')
    if [ "${status}" = "COMPLETED" ]; then
      echo "==> Smoke OK: job ${job_id} reached COMPLETED."
      break
    fi
    if [ "${status}" = "FAILED" ]; then
      echo "ERROR: smoke job ${job_id} reached FAILED." >&2
      exit 1
    fi
    sleep 2
  done
  if [ "${status}" != "COMPLETED" ]; then
    echo "ERROR: smoke job ${job_id} did not COMPLETE within 60s (last status: ${status:-none})." >&2
    exit 1
  fi
fi

cat <<'EOF'
==> Ready.
    Gateway health : http://localhost:8000/health
    RabbitMQ admin : http://localhost:15672   (guest / guest)
    MinIO console  : http://localhost:9001    (minioadmin / minioadmin)
    Stop with:  make down
EOF
