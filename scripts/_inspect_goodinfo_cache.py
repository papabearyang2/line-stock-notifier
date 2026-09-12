import sqlite3

from bs4 import BeautifulSoup


codes = ("3035", "3081", "3363", "3380", "3443")
pages = {
    "cash_flow": "/StockCashFlow.asp?STOCK_ID={}",
    "product_mix": "/ShowSaleMonProdChart.asp?STOCK_ID={}",
}
connection = sqlite3.connect("data/stockbot.db")
cursor = connection.cursor()
for page, template in pages.items():
    print("PAGE", page)
    for code in codes:
        path = template.format(code)
        row = cursor.execute(
            "select html from goodinfo_cache where path=?", (path,)
        ).fetchone()
        if not row:
            print(code, "CACHE_MISSING")
            continue
        soup = BeautifulSoup(row[0], "html.parser")
        text = " ".join(soup.get_text(" ", strip=True).split())
        print(code, "bytes", len(row[0].encode("utf-8")), "title", soup.title.get_text(strip=True) if soup.title else "")
        print("TEXT", text[:500])
        for index, table in enumerate(soup.find_all("table")):
            table_text = " ".join(table.get_text(" ", strip=True).split())
            if page == "cash_flow" and ("營業活動" in table_text or "EPS" in table_text):
                rows = [[" ".join(cell.get_text(" ", strip=True).split()) for cell in tr.find_all(["th", "td"])] for tr in table.find_all("tr")[:4]]
                print("TABLE", index, "len", len(table_text), "rows", rows, "TEXT", table_text[:1800])
            if page == "product_mix" and any(marker in table_text for marker in ("產品", "業務", "比重", "佔比")):
                print("TABLE", index, "len", len(table_text), "TEXT", table_text[:1800])
connection.close()
