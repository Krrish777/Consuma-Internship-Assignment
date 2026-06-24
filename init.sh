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

cat <<'EOF'
==> Ready.
    Gateway health : http://localhost:8000/health
    RabbitMQ admin : http://localhost:15672   (guest / guest)
    MinIO console  : http://localhost:9001    (minioadmin / minioadmin)
    Stop with:  make down
EOF
