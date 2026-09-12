from bs4 import BeautifulSoup

from app.providers.goodinfo import GoodinfoProvider
from app.providers.goodinfo_browser import goodinfo_browser


def test_parse_goodinfo_gross_margin_table():
    soup = BeautifulSoup(
        """
        <table>
          <tr><th>獲利能力</th><th>2025Q2</th><th>2025Q1</th><th>2024Q4</th></tr>
          <tr><td>營業毛利率</td><td>58.62</td><td>58.79</td><td>59.00</td></tr>
          <tr><td>營業利益率</td><td>49.63</td><td>48.51</td><td>49.02</td></tr>
        </table>
        """,
        "html.parser",
    )

    frame = GoodinfoProvider._parse_gross_margin_table(soup)

    assert frame["gross_margin"].tolist() == [59.0, 58.79, 58.62]
    assert frame["date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2024-12-31",
        "2025-03-31",
        "2025-06-30",
    ]


def test_parse_goodinfo_valuation_table():
    soup = BeautifulSoup(
        """
        <table>
          <tr>
            <th>交易月份</th><th>收盤價</th><th>漲跌價</th>
            <th>漲跌幅</th><th>近四季 EPS</th><th>目前 PER</th>
          </tr>
          <tr><td>25M05</td><td>967</td><td>+59</td><td>+6.5%</td><td>56.31</td><td>17.78</td></tr>
          <tr><td>25M04</td><td>908</td><td>-2</td><td>-0.22%</td><td>56.31</td><td>17.31</td></tr>
        </table>
        """,
        "html.parser",
    )

    frame = GoodinfoProvider._parse_valuation_table(soup)

    assert frame["close"].tolist() == [908.0, 967.0]
    assert frame["pe"].tolist() == [17.31, 17.78]
    assert frame["date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2025-04-30",
        "2025-05-31",
    ]


def test_parse_goodinfo_cash_flow_history():
    soup = BeautifulSoup(
        """
        <table>
          <tr><th>現金流量</th><th>營業活動</th><th>EPS</th></tr>
          <tr><td>2025</td><td>28.1</td><td>53</td><td>170</td><td>494</td>
            <td>324</td><td>190.6</td><td>45.2</td><td>34.1</td><td>5.96</td>
            <td>-65</td><td>48.9</td><td>-0.97</td><td>-11.2</td><td>-59.1</td>
            <td>62.8</td><td>51.6</td><td>4.07</td><td>12.13</td></tr>
        </table>
        """,
        "html.parser",
    )

    rows = GoodinfoProvider._parse_cash_flow_history(soup, 5)

    assert rows == [
        {
            "period": "2025",
            "operating": 5.96,
            "investing": -65,
            "financing": 48.9,
            "eps": 12.13,
        }
    ]


def test_parse_goodinfo_cash_flow_history_with_split_header_cells():
    soup = BeautifulSoup(
        """
        <table>
          <tr><th>現金流量</th><th>營業</th><th>活動</th><th>EPS</th></tr>
          <tr><td>2025</td><td>28.1</td><td>53</td><td>170</td><td>494</td>
            <td>324</td><td>190.6</td><td>45.2</td><td>34.1</td><td>5.96</td>
            <td>-65</td><td>48.9</td><td>-0.97</td><td>-11.2</td><td>-59.1</td>
            <td>62.8</td><td>51.6</td><td>4.07</td><td>12.13</td></tr>
        </table>
        """,
        "html.parser",
    )

    rows = GoodinfoProvider._parse_cash_flow_history(soup, 5)

    assert rows[0]["period"] == "2025"
    assert rows[0]["operating"] == 5.96
    assert rows[0]["eps"] == 12.13


def test_parse_goodinfo_monthly_revenue_history():
    soup = BeautifulSoup(
        """
        <table>
          <tr><th>月別</th><th>每月營收詳細資料</th><th>營收</th></tr>
          <tr><td>2026/07</td><td>1710</td><td>1125</td><td>1840</td><td>1060</td>
            <td>-555</td><td>-33.04</td><td>59.08</td><td>+20.7</td><td>+121.5</td>
            <td>302.6</td></tr>
        </table>
        """,
        "html.parser",
    )

    rows = GoodinfoProvider._parse_monthly_revenue_history(soup, 12)

    assert rows == [
        {
            "month": "2026-07",
            "revenue": 5_908_000_000,
            "yoy_pct": 121.5,
            "mom_pct": 20.7,
            "unit": "source_reported_億元_converted_to_NTD",
        }
    ]


def test_parse_goodinfo_dividend_policy():
    soup = BeautifulSoup(
        """
        <table>
          <tr><th>股東股利</th><th>現金股利</th></tr>
          <tr><td>2025</td><td>2024</td><td>6.504</td><td>0</td><td>6.504</td>
            <td>0</td><td>0</td><td>0</td><td>6.504</td></tr>
        </table>
        """,
        "html.parser",
    )

    rows = GoodinfoProvider._parse_dividend_policy(soup, 5)

    assert rows[0]["year"] == 2025
    assert rows[0]["cash_dividend_per_share"] == 6.504
    assert rows[0]["stock_dividend_per_share"] == 0


def test_parse_goodinfo_product_mix_marks_unreported_data():
    soup = BeautifulSoup(
        """
        <table><tr><th>產品/業務</th><th>營收佔比</th></tr>
        <tr><td colspan="2">無申報資料</td></tr></table>
        """,
        "html.parser",
    )

    result = GoodinfoProvider._parse_product_mix_summary(soup)

    assert result == {
        "status": "unavailable",
        "reason": "Goodinfo 顯示公司未申報產品／業務營收拆分",
    }


async def test_goodinfo_uses_cached_html_before_network(monkeypatch):
    provider = GoodinfoProvider()

    monkeypatch.setattr(
        provider,
        "_read_cache",
        lambda path: "<html><body><table>cached</table></body></html>",
    )

    async def fail(_):
        raise AssertionError("network should not be called")

    monkeypatch.setattr(provider, "_get", fail)
    monkeypatch.setattr("app.providers.goodinfo.get_settings", lambda: type(
        "Settings",
        (),
        {"goodinfo_enabled": True},
    )())

    soup = await provider._fetch_soup("/cached")

    assert soup and soup.get_text(strip=True) == "cached"


async def test_goodinfo_does_not_cache_cloudflare_challenge(monkeypatch):
    provider = GoodinfoProvider()
    monkeypatch.setattr(provider, "_read_cache", lambda path, allow_stale=False: None)
    monkeypatch.setattr(provider, "_write_cache", lambda path, html: (_ for _ in ()).throw(
        AssertionError("challenge must not be cached")
    ))

    async def challenge(_):
        return "<html><title>Just a moment...</title><body>Enable JavaScript and cookies to continue</body></html>"

    monkeypatch.setattr(provider, "_get", challenge)
    monkeypatch.setattr(
        "app.providers.goodinfo.get_settings",
        lambda: type("Settings", (), {"goodinfo_enabled": True, "goodinfo_min_interval_seconds": 3.0})(),
    )

    assert await provider._fetch_soup("/challenge") is None


async def test_goodinfo_browser_mode_uses_browser_backend(monkeypatch):
    provider = GoodinfoProvider()
    calls = []

    async def fetch(url):
        calls.append(url)
        return "<html><title>6274 台燿 - Goodinfo!</title></html>"

    monkeypatch.setattr(goodinfo_browser, "fetch", fetch)
    monkeypatch.setattr(
        "app.providers.goodinfo.get_settings",
        lambda: type("Settings", (), {"goodinfo_browser_enabled": True})(),
    )

    html = await provider._get("/ShowSaleMonChart.asp?STOCK_ID=6274")

    assert "6274 台燿" in html
    assert calls == [
        "https://goodinfo.tw/tw/ShowSaleMonChart.asp?STOCK_ID=6274"
    ]


def test_goodinfo_recognizes_chinese_security_page():
    text = "goodinfo.tw 正在執行安全驗證 請稍候"

    assert GoodinfoProvider._is_challenge("<html></html>", text)
