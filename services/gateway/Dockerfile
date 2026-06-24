# Build context is the repo ROOT (see docker-compose.yml: build.context = .).
# uv workspace: resolve from root, install only the `gateway` package tree (core + fastapi).
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Manifests first for layer caching; the workspace needs every member's pyproject present.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/core/pyproject.toml packages/core/pyproject.toml
COPY services/gateway/pyproject.toml services/gateway/pyproject.toml
COPY services/worker/pyproject.toml services/worker/pyproject.toml

# Sources
COPY packages ./packages
COPY services ./services

RUN uv sync --frozen --no-dev --package gateway

EXPOSE 8000
CMD ["uv", "run", "--no-dev", "--package", "gateway", \
     "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
