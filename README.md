# 台股研究 LINE Bot

以 Python、FastAPI 建立的台股研究 LINE Bot。

## 功能

在 LINE 對 Bot 輸入：

```text
新增 2330 2454
新增 台積電 聯發科
清單
刪除 2454
新聞
新聞 2330
新聞 台積電
毛利率 2330
本益比 2330
產業 2330
獲利 2330
訂閱摘要
取消摘要
```

- 觀察清單依 LINE 對話空間分開：私聊每位使用者一份，群組與多人聊天室各自共用一份。
- 「新聞」整理清單內股票最近三天的標題、來源與連結。
- 「訂閱摘要」每天定期推播新聞，並以資料庫唯一鍵避免重複寄送。
- 毛利率圖與本益比河流圖優先使用 FinMind；研究頁的缺漏段落可由本機手動執行 Goodinfo
  低頻補庫，補入後在頁面與圖表標示來源。Web 服務本身只讀資料庫，不啟動 Goodinfo。
- 同業依 TWSE／TPEx 產業分類，上下游依可維護的產業對照資料。
- 主要獲利來源採人工覆核資料優先，Goodinfo 是預設關閉的補充來源。

### 私人台股研究頁

`/research` 是單一擁有者使用的日頻研究頁，登入後可查看：

- 上市櫃普通股的官方產業熱力圖，以及可切換的人工覆核研究主題熱力圖。
- 1／5／20／60 日原始報酬、相對 TAIEX 含息報酬、成交值與交易重心變化。
- 同一張可排序個股表；市值加權與等權重只改變報酬聚合，不改變固定的上市櫃普通股分母。
- 個股頁的多維標籤、供應鏈位置與同層代表公司、EPS／TTM、現金流、月營收、PE 歷史、除權息事件及近期新聞。

標籤分成官方產業、產品／材料、技術、終端應用及供應鏈角色。只有具名且可追溯的公司、交易所或年報證據才會標為
`actual_business`；市場題材或產品規劃會保留不同關係狀態。頁面每段資料都顯示來源與查核日期。

法人「資金」欄位是三大法人買賣超股數乘以當日成交均價的估算，不是交易所公布的實際金額；任一法人缺值時總額保持空白。
除權息參考價、填權息進度及未揭露的 AI Server／Switch 營收比例不會由模型猜測。

資料僅供研究，不構成投資建議。

## 部署邊界

這個 repository 只維護應用程式、測試、資料定義與不含真實值的環境變數範例。
部署由擁有者在本機透過 Codex／CLI 管理，不使用 GitHub Actions 或 Git 平台自動部署。

`vercel.json`、`.vercelignore`、`.vercel/`、Docker 設定及所有實際密鑰都不進版本控制。
`.env.example` 只列出程式接受的設定名稱；共同維護者必須各自在本機建立 `.env`。

## 本機開發

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

本機預設使用 SQLite 與 `data/artifacts/`，FastAPI 會由 `/artifacts` 提供圖檔。
若要讓手動補庫結果寫入共用環境，請在自己的 `.env` 設定相同的 PostgreSQL `DATABASE_URL`；
若保留 SQLite，資料只會存在這台電腦。

## 資料來源

- 公司與產業：TWSE OpenAPI、TPEx OpenAPI、FinMind 股票清單。
- 研究頁日行情：TWSE `MI_INDEX`、TPEx `dailyQuotes`；上市每日發行股數由 TWSE `MI_QFIIS` 取得，日市值以收盤價乘發行股數衍生。
- 三大法人：TWSE `T86`、TPEx `dailyTrade`，保留買賣超股數與來源網址。
- 市場基準：TWSE TAIEX 價格指數與發行量加權股價報酬指數。
- 本益比、價格、財務報表：FinMind。
- 新聞：Google News RSS。
- 研究標籤、上下游與產品組合人工覆核資料：`data/research_taxonomy.yml`。
- 舊版 LINE 查詢使用的上下游／公司資料：`data/supply_chains.yml`、`data/company_profiles.yml`。
- Goodinfo：預設關閉；只在你明確執行本機補庫腳本時啟動 raw Chrome，透過 CDP 由 Playwright 控制並限速抓取，結果保存到
  `research_goodinfo_snapshots`。研究頁只讀資料庫，不在載入頁面時重新請求，也不繞過驗證或拒絕存取。

啟用 Goodinfo 前，請自行確認當下服務條款及 robots 規範：

```dotenv
GOODINFO_ENABLED=true
GOODINFO_BROWSER_ENABLED=true
GOODINFO_BROWSER_EXECUTABLE=
GOODINFO_BROWSER_PROFILE_DIR=./data/goodinfo-browser-profile
```

補庫會先掃描資料庫，不開網路也能列出缺漏。預設只處理研究標的與觀察清單，
由你手動執行腳本；retry 狀態會保存，下一次執行時會從到期項目續接，不會在網頁服務中自行啟動。
真正的瀏覽器會啟動 raw Chrome，再透過 CDP 由 Playwright 控制。6274 的五個頁面已在
3 秒間隔下 5／5 成功，正式補庫採 4.5 秒安全間隔，並已將第一批其他標的 2345、2356、2382、2383
的月營收保存到資料庫。瀏覽器模式失敗時依 1 分鐘、5 分鐘、15 分鐘、1 小時、4 小時退避：

```dotenv
GOODINFO_BACKFILL_ENABLED=true
GOODINFO_BACKFILL_SCHEDULED=false
GOODINFO_BACKFILL_SCOPE=research
GOODINFO_BACKFILL_MAX_PAGES=30
GOODINFO_PILOT_STOCK_CODE=6274
GOODINFO_ROLLOUT_ENABLED=true
```

`GOODINFO_BACKFILL_SCHEDULED` 請保持 `false`；它只保留給需要本機常駐排程的情境，
不是 Web 服務的一部分。手動執行時不需要開啟它。

可先盤點缺口及耗時，不會連線 Goodinfo：

```powershell
.\.venv\Scripts\python.exe scripts\sync_goodinfo.py --audit-only --scope research
```

手動執行一次試點／校準／擴展程序：

```powershell
.\.venv\Scripts\python.exe scripts\sync_goodinfo.py --scope research --years 5
```

只有除錯時才用 `--direct-cycle` 跳過試點閘門；一般排程與操作不要使用。

`--scope market` 會盤點全部上市櫃普通股，頁面數可能接近萬筆，不適合直接頻繁補抓。
加上 `--include-pages` 可輸出完整待補網址；未加時只顯示前 20 筆，避免終端輸出過大。
執行結果會記錄在 `research_refresh_logs`；輸出的 `browser_export_queue` 是仍需由一般瀏覽器匯出的頁面清單。
電腦關機時不會補庫；下次手動執行會依資料庫中的 retry 與待辦狀態接續處理。

## 測試

```bash
pytest
python scripts/smoke_test.py
```

`smoke_test.py` 會實際連接外部資料來源並產生兩張測試圖；單元測試不依賴外部網路。
目前驗證結果以實際執行 `pytest -q` 為準。

研究頁首次建立歷史資料可在本機執行（會連線到官方端點，逐日保留失敗狀態）。程式會先處理
`--end` 當天以前最近的交易日，再依日期由新到舊往回補；今天不是交易日時會自動跳過：

```powershell
.\.venv\Scripts\python.exe scripts\backfill_research.py --calendar-days 190 --pause 0.35
```

建議手動順序是先跑這個官方日行情補庫，再跑前面的 `sync_goodinfo.py` 補充個股財務與產品資料；
兩者都可中斷後重跑，資料庫會保留已完成的日期與區段。

約 190 個日曆日可涵蓋 60 日比較所需的前後期間。若某日來源暫時失敗，該日不會以 0 補入，
總覽會採最近一個上市與上櫃行情都完整的日期；`/api/research/status` 可查看最後一次更新結果。
若要分批往前補，可用 `--start YYYY-MM-DD --end YYYY-MM-DD` 指定區間；每次重跑都是可重入的。
若只需修復既有行情日期的法人資料，可執行：

```powershell
.\.venv\Scripts\python.exe scripts\backfill_research_flows.py --calendar-days 190
```

個股財務、月營收、PE、股利政策與產品營收拆分也可針對單一股票執行；每次只處理一檔，
先保存最新期間，再保留較早期間。已有未過期結果的區段會跳過，避免重複請求：

```powershell
.\.venv\Scripts\python.exe scripts\backfill_goodinfo.py --stock-code 6274 --years 5
```

Goodinfo 手動 Chrome 的正式安全間隔為 4.5 秒；`--force` 僅在需要重新核對時使用。若來源要求驗證或暫時拒絕，
程式會把區段保存為 `unavailable` 並記錄查核時間與原因，不以估計值代替。

手動補庫以 raw Chrome + CDP／Playwright 為主要流程；直接 HTTP 抓取只保留為低頻、盡力而為的方式。
這條路線需要在一般 Windows 使用者工作階段啟動 Chrome；受限的沙箱工作階段可能在 GPU 子程序啟動階段終止，這與 Goodinfo 的限速判定無關。
若自動流程無法取得頁面，仍可使用下列人工備援：

1. 在一般瀏覽器開啟對應 Goodinfo 頁面，等待安全驗證完成。
2. 在詳細資料表的「輔助」選單選擇「匯出HTML」，或將完整頁面另存為 HTML。
3. 以離線匯入工具解析；工具會核對股票代號、拒絕空表，並保存內容 SHA-256、來源網址與查核時間。

```powershell
.\.venv\Scripts\python.exe scripts\import_goodinfo_html.py --stock-code 6274 --page monthly_revenue --file C:\Downloads\6274-monthly.html --years 5
.\.venv\Scripts\python.exe scripts\import_goodinfo_html.py --stock-code 6274 --page cash_flow --file C:\Downloads\6274-cash-flow.html --years 5
```

`cash_flow` 會同時補 EPS 與現金流。其他 `--page` 選項為 `dividends`、`pe`、`product_mix`。
匯入只讀 HTML，不執行其中的 JavaScript，也不保存或重放瀏覽器 Cookie。
