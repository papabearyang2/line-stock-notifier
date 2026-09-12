from app.providers.goodinfo_browser import GoodinfoBrowser


async def test_browser_restarts_once_after_transient_control_error(monkeypatch):
    browser = GoodinfoBrowser()
    calls = 0

    async def fetch_once(url):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("browser_error")
        return "<html>Goodinfo!</html>"

    async def close():
        return None

    async def no_sleep(_):
        return None

    monkeypatch.setattr(browser, "_fetch_once", fetch_once)
    monkeypatch.setattr(browser, "_close_unlocked", close)
    monkeypatch.setattr("app.providers.goodinfo_browser.asyncio.sleep", no_sleep)

    assert "Goodinfo!" in await browser.fetch("https://goodinfo.tw/")
    assert calls == 2
