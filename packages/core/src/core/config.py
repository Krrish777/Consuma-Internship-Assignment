"""Env-driven settings (spec §10).

Pure configuration boilerplate — every value is injected via docker-compose at
runtime. The tuning knobs (TTS_CONCURRENCY, PARSE_FAILURE_RATE, MAX_RETRIES,
RETRY_DELAYS, PREFETCH) carry the spec defaults so the engine has correct
behavior even before a full .env exists.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Connection strings (no safe defaults; supplied by compose) ---
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/consuma"
    RABBITMQ_URL: str = "amqp://guest:guest@rabbitmq:5672/"
    REDIS_URL: str = "redis://redis:6379/0"

    # --- MinIO / object store ---
    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_ACCESS: str = "minioadmin"
    MINIO_SECRET: str = "minioadmin"

    # --- Engine tuning (spec defaults) ---
    TTS_CONCURRENCY: int = 3
    PARSE_FAILURE_RATE: float = 0.15
    MAX_RETRIES: int = 3
    RETRY_DELAYS: str = "1,4,16"  # seconds, parsed into the retry ladder later
    PREFETCH: int = 16


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so the env is read once per process."""
    return Settings()
