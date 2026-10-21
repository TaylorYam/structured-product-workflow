# 0010: Order 條件改從起單建檔讀取／Read Order Terms from the Daily Order Sheet

- 狀態／Status: Accepted；「明日開盤價」的處理由 [ADR 0011](0011-strike-date.md) 取代（記成開盤價＋Strike date）／Accepted; handling of 明日開盤價 superseded by ADR 0011 (stored as 開盤價 plus Strike date)
- 日期／Date: 2026-10-21
- 追蹤／Tracking: #97
- 部分取代／Partly supersedes: [ADR 0006](0006-new-product-data-from-order.md) 與 #40 的「條件以我方寄出的 `(Order)` 原信為準、從商品資料夾的 `.msg` 讀」；[ADR 0009](0009-confirm-order-terms-in-window.md) 的「匯入資料庫並匯出 Excel」與匯出的 `Order條件清單`／ADR 0006 and #40's "terms come from our original `(Order)` email, read from `.msg` files in product folders"; ADR 0009's "import to the database and export Excel" and the exported `Order條件清單`

## 繁體中文

### 背景

作業人員每天在 `G:\Bond Data\PM事項\0.常用工具\ELN每日起單建檔` 填一份 `起單建檔_YYYYMMDD.xlsm`（當月放在根目錄，之前的放在 `YYYYMM` 子資料夾），交易室依它起單。這份檔的欄位就是 #40、#86 匯出的「Order原始資料」（第 2 列表頭、從 B 欄開始，一檔一列）；匯出 Excel 的用途正是貼回這份表。讀信要逐家維護格式、依賴傳統版 Outlook，讀完還要再貼回去；直接讀起單建檔可以省掉兩段。

### 決策（2026-10-21 使用者確認）

- **頁面名稱**：分頁已經跟 Order 沒有關係，「2 Order 條件」改名為「2 起單資料」（畫面文字與文件；程式內部名稱與資料庫的值 `Order` 不改）。
- **來源**：「2 起單資料」改成作業人員選一份起單建檔，只讀這一份；貼的 TDCC 不在這份裡就標「找不到」。只讀、不修改。
- **讀法**：欄位依表頭文字對（不分大小寫、不看位置）。`UF (%)` 那欄放的是價格（舊表頭 `Upfront / NotePrice (%)`），寫進資料庫時 UF = 100 − 價格。選項不分大小寫；認不得的值留空、標紅並寫出讀到的值，不猜值。沒有資料庫欄位的欄（No.、Observation Frequency、OTC、Funding Spread、Effective Date Offset、起單FC）不讀。選到的檔不是每日起單建檔（好幾張工作表、找不到表頭、少欄）時整批不開始。
- **重複列**：同一檔 TDCC 有好幾列，內容一樣就用；不同的欄留空並寫出各列的值，作業人員在畫面上填好才可以匯入（正確是空白時改起單建檔後重讀）。
- **上手**：依 TDCC 前三碼查上手編號對照（`config/issuer_prefixes.toml`），和核對共用一份。
- **承作金額**：起單建檔沒有，畫面拿掉；資料庫 `order_amount` 不再寫入，金額以交易明細為準。ADR 0006「交易明細與 Order 的金額不一致標待確認」因此不做。
- **不再匯出 Excel**：只剩「匯入資料庫」；ADR 0009 的確認畫面（標紅、標黃、帶入資料庫的值、鎖住範圍、人工修改紀錄）照舊。匯入紀錄的 `source_mail` 欄改寫起單建檔檔名與列號。
- **刪除讀信**：讀 `.msg`、各上手格式設定、格式表、匯出問題信件（#42）的程式與測試刪除，Git 保留歷史。

### 考慮過的替代方案

- **依 TDCC 搜整個資料夾**：不用選檔，交易日和檔名日期不同的列（樣本約 3%）也找得到；但每次要開上百份檔，同一檔出現在不同日期時也要另訂規則。使用者選擇一次一份。
- **保留讀信、只取消匯出**：仍要維護 14 家格式與 Outlook；起單建檔本來就是作業人員確認過的條件。
- **承作金額留著讓人手動填**：多一個欄位要填，交易明細已有金額。

### 後果

- 不再需要 Outlook 與商品資料夾就能確認起單條件；「2 起單資料」不用根目錄。
- 起單建檔裡作業人員寫的非標準值（例如期初價格寫「待確認」「明日開盤價」）會標紅擋匯入，要在畫面上改；「明日開盤價」與「開盤價」意思不同，不自動換。
- 資料庫的 `order_amount` 欄保留但不再寫入；`order_import_line.source_mail` 欄名沿用。

## English

### Context

Every day operators fill `起單建檔_YYYYMMDD.xlsm` in `G:\Bond Data\PM事項\0.常用工具\ELN每日起單建檔` (the current month in the root, earlier ones in `YYYYMM` subfolders), and the trading desk places orders from it. Its columns are exactly the `Order原始資料` sheet exported by #40 and #86 (header on row 2 from column B, one product per row); the exported workbook existed to be pasted back into this sheet. Reading emails needed per-issuer formats and classic Outlook, and the result still had to be pasted back; reading the daily sheet directly removes both steps.

### Decision (confirmed by the user 2026-10-21)

- **Page name**: the tab no longer has anything to do with Orders, so "2 Order 條件" is renamed "2 起單資料" (screen text and docs; internal names and the database value `Order` stay).
- **Source**: on "2 起單資料" the operator picks one daily order sheet and only that file is read; pasted TDCCs missing from it are marked 找不到. The sheet is read-only.
- **Reading**: columns match by header text (case-insensitive, any position). The `UF (%)` column holds the price (older header `Upfront / NotePrice (%)`), and the database gets UF = 100 − price. Choices ignore case; unknown values stay blank, highlighted red with the value read, never guessed. Columns without a database field (No., Observation Frequency, OTC, Funding Spread, Effective Date Offset, 起單FC) are not read. A file that is not a daily order sheet (several sheets, no header, missing columns) stops the whole batch.
- **Duplicate rows**: when a TDCC has several rows, identical rows are used; differing fields stay blank with every row's value shown, and the operator must fill them before importing (if blank is right, fix the sheet and read again).
- **Issuer**: looked up from the TDCC's first three digits in `config/issuer_prefixes.toml`, shared with the checker.
- **Order amount**: not in the sheet, so it is removed from the screen; `order_amount` is no longer written and amounts come from the trade rows. ADR 0006's amount mismatch check between trade rows and the Order is therefore dropped.
- **No Excel export**: only "匯入資料庫" remains; ADR 0009's confirmation screen (red, yellow, database fill-in, lock scope, manual-edit log) is unchanged. The import log's `source_mail` column now holds the sheet file name and row numbers.
- **Mail reading removed**: the `.msg` reader, per-issuer formats, template sheet, and problem-mail export (#42) are deleted with their tests; Git keeps the history.

### Alternatives considered

- **Search the whole folder by TDCC**: no file to pick, and rows whose trade date differs from the file date (about 3% of samples) are still found; but every read opens hundreds of files, and a TDCC on several dates needs another rule. The user chose one file at a time.
- **Keep mail reading, drop only the export**: still 14 issuer formats and Outlook to maintain, while the daily sheet already holds the operator-confirmed terms.
- **Keep the order amount for manual entry**: one more field to fill when the trade rows already have the amount.

### Consequences

- Confirming Order terms no longer needs Outlook or product folders; "2 起單資料" no longer uses the root folder.
- Non-standard values operators type in the sheet (for example 期初價格 written as 待確認 or 明日開盤價) are highlighted red and block import until fixed on screen; 明日開盤價 (next day's open) differs from 開盤價 and is not converted automatically.
- The `order_amount` column stays but is no longer written; `order_import_line.source_mail` keeps its name.
