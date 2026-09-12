from pathlib import Path

import yaml

from app.providers.market import Company, market_catalog
from app.research_supplements import read_goodinfo_snapshot

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


async def industry_research(stock_code: str) -> str:
    goodinfo_snapshot = read_goodinfo_snapshot(stock_code, "product_mix")
    goodinfo = (goodinfo_snapshot or {}).get("summary")
    company = await market_catalog.get(stock_code)
    if not company:
        if goodinfo:
            return (
                f"{stock_code}｜產業與業務摘要\n"
                f"資料來源：Goodinfo\n{goodinfo}\n\n"
                "重要決策請回查公司年報。"
            )
        return f"找不到 {stock_code} 的上市櫃公司或 Goodinfo 資料。"
    peers = await market_catalog.peers(stock_code)
    profiles = _load_yaml(DATA_DIR / "company_profiles.yml")
    chains = _load_yaml(DATA_DIR / "supply_chains.yml")
    profile = profiles.get(stock_code, {})
    chain = chains.get(company.industry, {})
    upstream = profile.get("upstream") or chain.get("upstream") or []
    downstream = profile.get("downstream") or chain.get("downstream") or []
    peer_text = "、".join(f"{item.code} {item.name}" for item in peers) or "暫無"
    source = (
        "Goodinfo（業務摘要）＋交易所產業分類"
        if goodinfo
        else "交易所產業分類（Goodinfo 暫無資料）"
    )
    result = (
        f"{stock_code} {company.name}｜{company.market}／{company.industry or '未分類'}\n"
        f"資料來源：{source}\n"
        f"同產業公司：{peer_text}\n"
        f"上游：{'、'.join(upstream) or '尚待人工補充'}\n"
        f"下游：{'、'.join(downstream) or '尚待人工補充'}\n\n"
        "註：同業依交易所產業分類；上下游為產業層級對照，不代表實際客戶或供應商。"
    )
    if goodinfo:
        result += f"\n\nGoodinfo 業務摘要：\n{goodinfo}"
    return result


async def profit_sources(stock_code: str) -> str:
    company: Company | None = await market_catalog.get(stock_code)
    stock_label = f"{stock_code} {company.name}" if company else stock_code
    goodinfo_snapshot = read_goodinfo_snapshot(stock_code, "product_mix")
    goodinfo = (goodinfo_snapshot or {}).get("summary")
    if goodinfo:
        return (
            f"{stock_label}｜主要獲利來源\n資料來源：Goodinfo\n{goodinfo}\n\n"
            "HTML 摘要可能因網頁改版而不完整，重要決策請回查公司財報。"
        )

    profiles = _load_yaml(DATA_DIR / "company_profiles.yml")
    profile = profiles.get(stock_code, {})
    reviewed = profile.get("profit_sources") or []
    if reviewed:
        sources = "\n".join(f"• {item}" for item in reviewed)
        return f"{stock_label}｜主要獲利來源（人工覆核備援）\n{sources}"

    if not company:
        return f"找不到 {stock_code} 的上市櫃公司或 Goodinfo 資料。"
    business = company.business.strip() if company.business else "交易所資料未提供說明"
    return (
        f"{stock_code} {company.name}｜主要營業項目\n{business}\n\n"
        "目前沒有可驗證的產品別獲利占比。可在 data/company_profiles.yml "
        "補上年報覆核資料。"
    )
