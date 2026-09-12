from datetime import date

from app.providers.research_market import OfficialResearchMarketProvider


async def test_twse_daily_quote_joins_same_day_issued_shares(monkeypatch):
    provider = OfficialResearchMarketProvider()

    async def payload(url, params, attempts=3):
        if url == provider.TWSE_CAPITAL_URL:
            return {
                "stat": "OK",
                "fields": ["證券代號", "發行股數"],
                "data": [["2383", "358,321,114"]],
            }, "https://source.test/capital"
        return {
            "stat": "OK",
            "tables": [
                {
                    "fields": [
                        "證券代號",
                        "證券名稱",
                        "成交股數",
                        "成交金額",
                        "收盤價",
                        "漲跌(+/-)",
                        "漲跌價差",
                    ],
                    "data": [
                        ["2383", "台光電", "1,000", "500,000", "500", "<p>+</p>", "5"],
                        ["6213", "聯茂", "0", "0", "--", "<p>-</p>", "--"],
                    ],
                }
            ],
        }, "https://source.test/quote"

    monkeypatch.setattr(provider, "_get_json", payload)

    rows = await provider.twse_quotes(date(2026, 8, 31))

    assert rows[0].issued_shares == 358_321_114
    assert rows[0].trade_value == 500_000
    assert rows[1].trade_value == 0
    assert rows[1].close is None


async def test_tpex_quote_keeps_missing_values_null_and_real_zero(monkeypatch):
    provider = OfficialResearchMarketProvider()

    async def payload(*args, **kwargs):
        return {
            "stat": "ok",
            "tables": [
                {
                    "fields": [
                        "代號",
                        "名稱",
                        "收盤",
                        "漲跌",
                        "成交股數",
                        "成交金額(元)",
                        "發行股數",
                    ],
                    "data": [["6274", "台燿", "--", "-1.5", "0", "0", "299,336,093"]],
                }
            ],
        }, "https://source.test/tpex"

    monkeypatch.setattr(provider, "_get_json", payload)

    row = (await provider.tpex_quotes(date(2026, 9, 1)))[0]

    assert row.close is None
    assert row.raw_change == -1.5
    assert row.trade_volume == 0
    assert row.issued_shares == 299_336_093


async def test_institutional_flows_remain_share_counts(monkeypatch):
    provider = OfficialResearchMarketProvider()

    async def payload(*args, **kwargs):
        return {
            "stat": "OK",
            "fields": [
                "證券代號",
                "投信買賣超股數",
                "外陸資買賣超股數(不含外資自營商)",
                "自營商買賣超股數",
            ],
            "data": [["2383", "-100", "2,000", "300"]],
        }, "https://source.test/flow"

    monkeypatch.setattr(provider, "_get_json", payload)
    row = (await provider.twse_flows(date(2026, 8, 31)))[0]

    assert row.foreign_net_shares == 2_000
    assert row.trust_net_shares == -100
    assert row.dealer_net_shares == 300


async def test_tpex_institutional_flow_uses_repeated_column_positions(monkeypatch):
    provider = OfficialResearchMarketProvider()

    async def payload(*args, **kwargs):
        fields = ["代號", "名稱"] + ["買進股數", "賣出股數", "買賣超股數"] * 7 + ["三大法人買賣超股數合計"]
        values = [
            "6274",
            "台燿",
            "100",
            "80",
            "20",  # 外資（不含自營）
            "0",
            "0",
            "0",
            "100",
            "80",
            "20",
            "50",
            "10",
            "40",  # 投信
            "12",
            "10",
            "2",
            "5",
            "8",
            "-3",
            "17",
            "5",
            "12",  # 自營商合計
            "59",
        ]
        return {"stat": "OK", "tables": [{"fields": fields, "data": [values]}]}, "https://source.test/tpex-flow"

    monkeypatch.setattr(provider, "_get_json", payload)
    row = (await provider.tpex_flows(date(2026, 8, 31)))[0]

    assert row.code == "6274"
    assert row.foreign_net_shares == 20
    assert row.trust_net_shares == 40
    assert row.dealer_net_shares == 12


async def test_total_return_index_parses_roc_date(monkeypatch):
    provider = OfficialResearchMarketProvider()

    async def payload(*args, **kwargs):
        return {
            "stat": "OK",
            "fields": ["日期", "發行量加權股價報酬指數"],
            "data": [["115/08/31", "52,345.67"]],
        }, "https://source.test/index"

    monkeypatch.setattr(provider, "_get_json", payload)
    point = (await provider.taiex_total_return(date(2026, 8, 1)))[0]

    assert point.trade_date == date(2026, 8, 31)
    assert point.value == 52_345.67
    assert point.index_code == "TAIEX_TOTAL_RETURN"
