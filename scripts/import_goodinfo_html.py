"""Import a Goodinfo page exported by a normal browser into the research database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.research_supplements import GOODINFO_IMPORT_PAGES, import_goodinfo_html


MAX_HTML_BYTES = 10 * 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock-code", required=True, help="四碼台股代號，例如 6274")
    parser.add_argument("--page", required=True, choices=GOODINFO_IMPORT_PAGES)
    parser.add_argument("--file", required=True, type=Path, help="Goodinfo 匯出或另存的 HTML")
    parser.add_argument("--years", type=int, default=5)
    return parser.parse_args()


def decode_html(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("無法辨識 HTML 編碼")


def run(arguments: argparse.Namespace) -> None:
    if not arguments.stock_code.isdigit() or len(arguments.stock_code) != 4:
        raise ValueError("stock-code 必須是四碼數字")
    raw = arguments.file.read_bytes()
    if not raw or len(raw) > MAX_HTML_BYTES:
        raise ValueError("HTML 檔必須介於 1 byte 與 10 MB")
    result = import_goodinfo_html(
        arguments.stock_code,
        arguments.page,
        decode_html(raw),
        years=arguments.years,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    run(parse_args())
