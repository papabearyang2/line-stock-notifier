import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    line_channel_secret: str = ""
    line_channel_access_token: str = ""
    public_base_url: str = "http://localhost:8000"
    finmind_token: str = ""
    cron_secret: str = ""
    dashboard_password: str = ""
    dashboard_session_secret: str = ""
    dashboard_session_hours: int = Field(default=12, ge=1, le=168)
    blob_read_write_token: str = ""
    goodinfo_enabled: bool = False
    goodinfo_min_interval_seconds: float = Field(default=8.0, ge=3.0)
    goodinfo_cache_hours: int = Field(default=24, ge=1, le=168)
    goodinfo_browser_enabled: bool = False
    goodinfo_browser_executable: str = ""
    goodinfo_browser_profile_dir: Path = Path("./data/goodinfo-browser-profile")
    goodinfo_backfill_enabled: bool = False
    # Goodinfo is a manual/local backfill source. Keep this false so the web
    # service never starts a Goodinfo browser job unless explicitly requested.
    goodinfo_backfill_scheduled: bool = False
    goodinfo_backfill_scope: Literal["research", "market"] = "research"
    goodinfo_backfill_interval_minutes: int = Field(default=5, ge=5, le=1440)
    goodinfo_backfill_max_pages: int = Field(default=30, ge=1, le=60)
    goodinfo_pilot_stock_code: str = "6274"
    goodinfo_rollout_enabled: bool = True
    database_url: str = ""
    postgres_url: str = ""
    timezone: str = "Asia/Taipei"
    enable_scheduler: bool = True
    daily_digest_hour: int = Field(default=8, ge=0, le=23)
    daily_digest_minute: int = Field(default=0, ge=0, le=59)
    research_refresh_hour: int = Field(default=20, ge=0, le=23)
    research_refresh_minute: int = Field(default=0, ge=0, le=59)
    news_lookback_days: int = Field(default=3, ge=1, le=14)
    artifact_dir: Path = Path("./data/artifacts")
    log_level: str = "INFO"
    vercel: bool = False

    @field_validator("public_base_url")
    @classmethod
    def strip_public_url(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def is_vercel(self) -> bool:
        return self.vercel or bool(os.getenv("VERCEL"))

    @property
    def resolved_database_url(self) -> str:
        value = self.database_url or self.postgres_url
        if not value:
            if self.is_vercel:
                temp_db = (Path(tempfile.gettempdir()) / "stockbot.db").as_posix()
                value = f"sqlite:///{temp_db}"
            else:
                value = "sqlite:///./data/stockbot.db"
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg://", 1)
        if value.startswith("postgresql://") and "+psycopg" not in value:
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    @property
    def resolved_artifact_dir(self) -> Path:
        if self.is_vercel and self.artifact_dir == Path("./data/artifacts"):
            return Path(tempfile.gettempdir()) / "stockbot-artifacts"
        return self.artifact_dir

    @property
    def has_persistent_database(self) -> bool:
        return not (
            self.is_vercel and self.resolved_database_url.startswith("sqlite")
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
