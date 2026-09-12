from types import SimpleNamespace

from app import scheduler as scheduler_module


def test_goodinfo_scheduler_stays_off_in_manual_mode(monkeypatch):
    job_ids = []

    class FakeScheduler:
        running = True

        def __init__(self, **_kwargs):
            pass

        def add_job(self, _func, _trigger, **kwargs):
            job_ids.append(kwargs["id"])

        def start(self):
            pass

        def shutdown(self, wait=False):
            self.running = False

    settings = SimpleNamespace(
        enable_scheduler=True,
        timezone="Asia/Taipei",
        daily_digest_hour=8,
        daily_digest_minute=0,
        research_refresh_hour=20,
        research_refresh_minute=0,
        goodinfo_enabled=True,
        goodinfo_backfill_enabled=True,
        goodinfo_backfill_scheduled=False,
        goodinfo_backfill_interval_minutes=5,
    )
    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", FakeScheduler)
    monkeypatch.setattr(scheduler_module, "get_settings", lambda: settings)

    scheduler_module.start_scheduler()
    try:
        assert "goodinfo-backfill" not in job_ids
    finally:
        scheduler_module.stop_scheduler()
