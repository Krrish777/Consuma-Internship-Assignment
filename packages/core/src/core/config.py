"""Env-driven settings.

Pure configuration boilerplate — every value is injected via docker-compose at
runtime. The tuning knobs carry the spec defaults so the engine has correct
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

    # --- Engine tuning ---
    TTS_CONCURRENCY: int = 3
    PARSE_FAILURE_RATE: float = 0.15
    MAX_RETRIES: int = 3
    RETRY_DELAYS: str = "1,4,16"  # seconds; use .retry_delays for the parsed tuple
    PREFETCH: int = 16

    # --- Input guards (size, block cap) ---
    MAX_MANUSCRIPT_BYTES: int = 1_000_000
    MAX_BLOCKS: int = 10_000

    # --- Webhook ---
    WEBHOOK_ALLOWLIST: str = ""  # comma-sep hosts; empty = log-only mode
    WEBHOOK_TIMEOUT_S: float = 5.0

    # --- Sweeper / pending-timeout ---
    SWEEP_INTERVAL_S: int = 30
    PENDING_TIMEOUT_S: int = 120

    # --- Redis TTLs ---
    LEASE_TTL_S: int = 30  # semaphore lease
    RESEED_INTERVAL_S: int = 30  # worker re-seeds tts:slots if Redis is wiped
    REAP_INTERVAL_S: int = 30  # worker reclaims crashed holders' orphaned slots
    CACHE_TTL_S: int = 86_400  # content cache
    PROCESSED_EVENTS_RETENTION_S: int = 604_800  # inbox retention (7 days)

    @property
    def retry_delays(self) -> tuple[int, ...]:
        """Parse RETRY_DELAYS comma string into a typed tuple of ints."""
        return tuple(int(d.strip()) for d in self.RETRY_DELAYS.split(",") if d.strip())

    @property
    def webhook_allowlist(self) -> tuple[str, ...]:
        """Parse WEBHOOK_ALLOWLIST comma string into a tuple of host strings."""
        return tuple(h.strip() for h in self.WEBHOOK_ALLOWLIST.split(",") if h.strip())


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so the env is read once per process."""
    return Settings()
