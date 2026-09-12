"""Human-reviewed theme, tag, supply-chain and product-mix evidence."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "research_taxonomy.yml"


@lru_cache
def load_research_catalog() -> dict[str, Any]:
    if not CATALOG_PATH.exists():
        return {"themes": {}, "stocks": {}}
    with CATALOG_PATH.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    return payload if isinstance(payload, dict) else {"themes": {}, "stocks": {}}


def theme_definitions() -> dict[str, dict[str, Any]]:
    themes = load_research_catalog().get("themes", {})
    return themes if isinstance(themes, dict) else {}


def stock_profile(stock_code: str) -> dict[str, Any]:
    stocks = load_research_catalog().get("stocks", {})
    if not isinstance(stocks, dict):
        return {}
    profile = stocks.get(str(stock_code), {})
    return profile if isinstance(profile, dict) else {}


def formal_theme_codes(theme_key: str) -> list[str]:
    result: list[str] = []
    for code, profile in (load_research_catalog().get("stocks", {}) or {}).items():
        if not isinstance(profile, dict):
            continue
        memberships = profile.get("themes", []) or []
        for membership in memberships:
            if not isinstance(membership, dict) or membership.get("key") != theme_key:
                continue
            if membership.get("relation_state") == "actual_business":
                result.append(str(code))
                break
    return list(dict.fromkeys(result))


def stock_tags(stock_code: str) -> list[dict[str, Any]]:
    tags = stock_profile(stock_code).get("tags", []) or []
    return [tag for tag in tags if isinstance(tag, dict)]
