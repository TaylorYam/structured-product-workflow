# 0015: 回補股價 CSV 與只匯交易明細／Price CSV Backfill and Trades-Only Import

- 狀態／Status: Accepted
- 日期／Date: 2026-10-22
- 追蹤／Tracking: #112
- 補充／Completes: [ADR 0008](0008-daily-prices-denomination-and-order-dates.md) 的取價日／the price date of ADR 0008

## 繁體中文

### 背景

BBG_Data 的公式抓的是取價當下的最新價格。台灣放假或請假幾天後回頭補資料時，用每日更新匯入只會拿到最新的價格，卻記成要補的那一天，核對條件會算錯。Bloomberg 可以另外匯出某一天的 `MarketData_YYYYMMDD.csv`：UTF-8（有 BOM），欄位是取價日、代碼、公司名稱、收盤價、開盤價、200日波動率(%)、市值(原幣/百萬)、幣別、市值(百萬美元)，整份是同一個取價日。

### 決策（2026-10-22 使用者確認）

- **CSV 的取價日是收盤那一天**：`MarketData_20261016.csv` 的取價日 2026-10-16 是 10/16 的收盤價與開盤價。
- **存成取價日＋1 天**：ADR 0008 的取價日是取價那一天、內容是前一個交易日的價格，所以 CSV 存成下一天（10/16 → 10/17），等同隔天早上用 BBG_Data 取到的價格；「交易日 T 用取價日比 T 晚的第一份股價檔」與假日表確認都不變。同一個取價日已有股價時整天取代，跟 BBG_Data 一樣。
- **欄位**：收盤價、開盤價、200日波動率(%)、市值(原幣/百萬) 對到 `workflow.daily_price` 的同義欄；CSV 沒有 1日前收盤價，存成空白；幣別、市值(百萬美元) 不存。代碼、重複代碼、不是數字的處理跟 BBG_Data 一樣；`NaN`、`Infinity` 也算不是數字（兩種股價檔都一樣）。
- **一次一個 CSV**：「1 每日匯入」的「回補股價」一次選一個；欄名不對、取價日不是日期或不只一天、沒有任何代碼時整份不匯入。也留一筆 `workflow.import_run`，問題寫進 CSV 同資料夾的 `每日匯入錯誤.xlsx`。匯入後重算還沒鎖住的商品。
- **只匯交易明細**：每日匯入加一個勾選，勾了就不讀 BBG_Data、不動股價（沒有這張工作表也可以），回補時交易明細照舊用每日更新匯入。

### 考慮過的替代方案

- **CSV 直接存成它寫的取價日**：要改 ADR 0008 的對應規則，兩種股價檔的日期意義會不一樣。
- **一次選多個 CSV**：使用者選擇一次一個，出錯時比較好判斷是哪一天。
- **另外準備一份沒有 BBG_Data 的每日更新**：使用者偏好在同一份檔案上勾選。

### 後果

- 寄信草稿確認的「最近一次每日匯入」不算回補股價 CSV，仍然顯示最近一份每日更新，因為寄信前要確認的是交易明細。
- 回補前已經用每日更新把最新價格記成某一天的，再匯入那一天的 CSV 會整天取代，核對條件跟著重算。

## English

### Context

BBG_Data formulas fetch the latest prices at the time of pulling. When backfilling after Taiwan holidays or leave, a daily update import only gets the latest prices but stores them under the day being backfilled, which miscalculates check terms. Bloomberg can export a day's `MarketData_YYYYMMDD.csv` instead: UTF-8 (with a BOM), columns price date, code, company name, close, open, 200-day volatility (%), market cap (local currency, millions), currency, and market cap (USD millions), one price date per file.

### Decision (confirmed by the user 2026-10-22)

- **The CSV price date is the closing day**: the 2026-10-16 date in `MarketData_20261016.csv` holds the 10/16 close and open.
- **Stored as price date + 1 day**: ADR 0008's price date is the pulling day with the previous trading day's prices, so a CSV is stored as the next day (10/16 → 10/17), the same as a BBG_Data pull the next morning; the "trade date T uses the first price file after T" rule and the holiday calendar check are unchanged. An existing price date is replaced whole, as with BBG_Data.
- **Columns**: close, open, 200-day volatility (%), and market cap (local, millions) map to the matching `workflow.daily_price` columns; the CSV has no previous close, which is stored blank; currency and USD market cap are not stored. Codes, duplicate codes, and non-numbers are handled as in BBG_Data; `NaN` and `Infinity` also count as non-numbers (for both kinds of price files).
- **One CSV at a time**: "回補股價" on "1 每日匯入" takes one file; wrong headers, a price date that is not a date or spans more than one day, or no codes reject the whole file. It records a `workflow.import_run` row, writes problems to `每日匯入錯誤.xlsx` beside the CSV, and recalculates unlocked products.
- **Trades-only import**: a checkbox on the daily import skips BBG_Data and leaves prices untouched (the sheet may be absent), so backfilled trade rows still come from the daily update.

### Alternatives considered

- **Store the CSV under its own date**: would change ADR 0008's matching rule and give the two kinds of price files different date meanings.
- **Select several CSVs at once**: the user chose one at a time, which makes it clearer which day failed.
- **Prepare a separate daily update without BBG_Data**: the user preferred a checkbox on the same workbook.

### Consequences

- The mail draft's "latest daily import" confirmation skips price CSVs and still shows the latest daily update, since what matters before mailing is the trade rows.
- If the latest prices were already stored under a day by a daily update, importing that day's CSV replaces them and check terms are recalculated.
