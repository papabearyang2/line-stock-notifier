from pathlib import Path
import tempfile

from app.config import Settings


def test_vercel_defaults_use_temporary_paths():
    settings = Settings(_env_file=None, vercel=True)
    temp_dir = Path(tempfile.gettempdir())
    expected_db = f"sqlite:///{(temp_dir / 'stockbot.db').as_posix()}"
    assert settings.resolved_database_url == expected_db
    assert settings.resolved_artifact_dir == temp_dir / "stockbot-artifacts"
    assert not settings.has_persistent_database


def test_postgres_urls_use_psycopg_driver():
    settings = Settings(
        _env_file=None,
        vercel=True,
        database_url="postgres://user:password@example.test/db?sslmode=require",
    )
    assert settings.resolved_database_url.startswith("postgresql+psycopg://")
    assert settings.has_persistent_database


def test_goodinfo_backfill_is_manual_by_default():
    settings = Settings(
        _env_file=None,
        goodinfo_enabled=True,
        goodinfo_backfill_enabled=True,
    )
    assert settings.goodinfo_backfill_scheduled is False

    scheduled = Settings(
        _env_file=None,
        goodinfo_enabled=True,
        goodinfo_backfill_enabled=True,
        goodinfo_backfill_scheduled=True,
    )
    assert scheduled.goodinfo_backfill_scheduled is True
