"""Application configuration loaded from environment / .env."""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+psycopg2://aie:aie@127.0.0.1:5433/aie"

    # LLM (provider-agnostic)
    llm_provider: str = ""  # openai | anthropic | gemini
    llm_model: str = ""
    llm_api_key: str = ""

    # Universe / market data
    market_index: str = "^NSEI"  # Nifty 50 (reliably available on Yahoo)

    # Ingestion
    poll_interval_seconds: int = 60
    backfill_days: int = 90
    retention_days: int = 15
    feed_max_days: int = 90
    attachment_retention_days: int = 7
    purge_enabled: bool = False
    nse_ingest_enabled: bool = True
    nse_proxy_url: str = ""  # e.g. http://user:pass@host:port — for cloud/datacenter egress
    ingest_on_startup: bool = False  # worker: poll/backfill announcements on start
    # When false, backfill ingests are stored for the live feed but not queued for LLM analysis.
    analyze_backfill: bool = False
    # Only analyze announcements first ingested (fetched_at) on or after this time.
    # Clears any restored/pre-deploy pending backlog on worker startup.
    analyze_from: dt.datetime | None = None

    # Worker
    analyze_batch_size: int = 50

    # Event study
    estimation_window_days: int = 120

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000"

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        """Render/Heroku provide postgres:// — SQLAlchemy needs postgresql+psycopg2://."""
        if isinstance(value, str) and value.startswith("postgres://"):
            return "postgresql+psycopg2://" + value[len("postgres://") :]
        return value

    @field_validator("analyze_from", mode="before")
    @classmethod
    def _parse_analyze_from(cls, value: dt.datetime | str | None) -> dt.datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, dt.datetime):
            parsed = value
        else:
            parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=dt.timezone.utc)
        return parsed

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
