from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import research_router


def _app():
    app = FastAPI()
    app.include_router(research_router.router)
    return app


def _settings(password="correct horse", secret="test-session-secret"):
    return SimpleNamespace(
        dashboard_password=password,
        dashboard_session_secret=secret,
        dashboard_session_hours=12,
        is_vercel=False,
        public_base_url="http://testserver",
    )


def test_private_page_requires_login_and_accepts_owner_password(monkeypatch):
    monkeypatch.setattr(research_router, "settings", _settings())
    with TestClient(_app()) as client:
        redirect = client.get("/research", follow_redirects=False)
        assert redirect.status_code == 303
        assert redirect.headers["location"] == "/research/login?next=/research"

        login_page = client.get("/research/login")
        assert login_page.status_code == 200
        assert "PRIVATE RESEARCH" in login_page.text

        response = client.post(
            "/research/login",
            data={"password": "correct horse", "next": "/research"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "stock_research_session=" in response.headers["set-cookie"]

        dashboard = client.get("/research")
        assert dashboard.status_code == 200
        assert dashboard.headers["cache-control"] == "no-store"
        assert "臺股交易重心觀測" in dashboard.text
        assert 'id="financialCharts"' in dashboard.text
        assert "dashboard.js?v=20260902-financial-2y" in dashboard.text
        assert "dashboard.css?v=20260902-financial-2y" in dashboard.text


def test_overview_api_uses_session_and_preserves_service_shape(monkeypatch):
    monkeypatch.setattr(research_router, "settings", _settings())
    monkeypatch.setattr(
        research_router,
        "overview",
        lambda **kwargs: {"meta": {"window": kwargs["window"]}, "groups": [], "stocks": []},
    )
    with TestClient(_app()) as client:
        client.post("/research/login", data={"password": "correct horse"})
        response = client.get("/api/research/overview?window=5&weight=equal&view=theme")
        assert response.status_code == 200
        assert response.json() == {"meta": {"window": 5}, "groups": [], "stocks": []}


def test_stock_api_reads_goodinfo_snapshot_without_live_request(monkeypatch):
    monkeypatch.setattr(research_router, "settings", _settings())
    monkeypatch.setattr(
        research_router,
        "stock_snapshot",
        lambda code, window=20: {
            "meta": {},
            "stock": {"code": code, "name": "台燿"},
            "product_mix": {"status": "unavailable", "items": [], "sources": []},
        },
    )
    monkeypatch.setattr(
        research_router,
        "read_goodinfo_snapshot",
        lambda code, section: {
            "status": "unavailable",
            "source": "Goodinfo（補充）",
            "source_url": "https://goodinfo.tw/tw/ShowSaleMonProdChart.asp?STOCK_ID=6274",
            "reason": "Goodinfo 顯示公司未申報產品／業務營收拆分",
            "last_checked": "2026-09-01T00:00:00+00:00",
        },
    )

    async def no_network(*args, **kwargs):
        return {"status": "unavailable", "items": [], "sources": []}

    monkeypatch.setattr(research_router.company_research_provider, "financials", no_network)
    monkeypatch.setattr(research_router.company_research_provider, "dividends", no_network)
    monkeypatch.setattr(research_router.company_research_provider, "news", no_network)

    with TestClient(_app()) as client:
        client.post("/research/login", data={"password": "correct horse"})
        response = client.get("/api/research/stocks/6274")

    assert response.status_code == 200
    payload = response.json()
    assert payload["product_mix"]["goodinfo"]["status"] == "unavailable"
    assert payload["product_mix"]["sources"][-1]["type"] == "secondary_html"
