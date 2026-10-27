# 0026: 每日更新也寫收盤價／The Daily Update Also Writes Closes

- 狀態／Status: Accepted
- 日期／Date: 2026-10-27
- 追蹤／Tracking: #280
- 取代／Supersedes: [ADR 0020](0020-close-prices-and-observation-rules.md) 的「BBG_Data 不寫 `close_price`」與「每天都要匯 MarketData CSV」／ADR 0020's "BBG_Data never writes `close_price`" and "the MarketData CSV must be imported every day"

## 繁體中文

### 背景

ADR 0020 讓收盤價只來自 MarketData CSV 與歷史收盤價，理由是 BBG_Data 一個取價日表示不了多個市場的收盤日。結果每天要多匯一個 CSV，沒匯就停在「等股價」：2026-10-27 的每日匯入已經有 10/26 的價格，收盤價表卻只到 10/23。使用者希望每日匯入就補齊收盤價，不必看股價從哪裡來。

用開發資料庫比對兩份每日更新：股票的「最新收盤價」和前一個交易日的收盤價幾乎都相同，「1日前收盤價」是再前一天的。對不上的主要是外匯、利率、指數這類沒有交易所碼的代碼，值有四捨五入，也不一定是收盤價。

### 決策（2026-10-27 使用者確認）

- **每日匯入同時寫收盤價**：有股價、讀得到交易所假日表時，和 `daily_price` 在同一個 transaction 寫 `close_price`，來源記「每日更新」（資料表版本 0030）。只匯交易明細、讀不到假日表時不寫；整份不匯入時什麼都不寫。寫了收盤價就比價，比到的那一天帶到出場清單，和回補股價一樣。
- **用「最新收盤價」**（`last_close`），不用「1日前收盤價」。
- **收盤日依各代碼的市場推算**：代碼市場在取價日之前最近的交易日（跳過週末與假日表的休市日），不是取價日減一天。週一取價是週五的收盤；美國週一休市、週二取價時，美股算週五、日股算週一。
- **推不出收盤日的不存**：交易所不在假日表的代碼（外匯、利率、指數等）不存，和 CSV 不同；中間的日子超出假日表涵蓋範圍的也不存。兩種都寫在完成訊息與匯入紀錄。
- **MarketData CSV 與歷史收盤價優先**：每日更新只補還沒有的收盤價、取代自己之前寫的（值不同時舊值記在 `close_price_change`）；CSV 或歷史收盤價已有的那一筆不動，算在「沒有取代」。CSV、歷史收盤價照舊整筆取代任何來源。
- **已在 `daily_price`、還沒進收盤價表的日子不另做回填**：更新後重新匯入那一天的每日更新即可（交易明細已存在的不重複寫，股價整天取代成一樣的值）。

### 考慮過的替代方案

- **只用一個收盤日（取價日前一個平日），休市的代碼略過**：存進去的結果相同，但「每個代碼各自的前一個交易日」比較好說明，也不必另外判斷哪些市場那天休市。
- **後匯入的蓋過，不分來源**：最簡單，但 BBG_Data 晚抓（亞洲已收盤）或拿舊檔回補時，會把正確的 CSV 收盤價改錯，進而影響 KO／KI。
- **維護選單做一次性回填**：要多寫程式與測試；舊的每日股價有些是當時抓的最新價，日期不一定可靠。
- **外匯、指數也存（照 CSV 的規則）**：日期推不出來，值又有四捨五入，存了會和 CSV 的值互相取代。

### 後果

- 每天不必再匯 MarketData CSV，比價直接用每日匯入的收盤價；放假或請假回補時仍用 CSV（ADR 0015），而且 CSV 會蓋過每日更新寫的值。
- BBG_Data 抓的是取價當下的最新價格：晚抓或拿舊檔匯入時，沒有 CSV 的那幾天可能記到錯的收盤日。回補時照舊勾「只匯交易明細」。
- 交易所不在假日表的標的不會有每日更新的收盤價，比價本來就標待確認。
- 資料表版本 0030 只放寬 `close_price_import.source` 的檢查，不動既有資料。

## English

### Context

ADR 0020 took closes only from the MarketData CSV and the historical closes, because a single BBG_Data price date cannot express several markets' closing days. That made the CSV one more daily import, and forgetting it leaves observation at 等股價: on 2026-10-27 the daily import already held the 10/26 prices while the close table stopped at 10/23. The user wants the daily import to complete the closes, whatever the price source.

Comparing two daily updates in the development database: for stocks, 最新收盤價 nearly always equals the previous trading day's close, and 1日前收盤價 is the day before that. The mismatches are mostly codes without an exchange code (FX, rates, indices), whose values are rounded and not necessarily closes.

### Decision (confirmed by the user 2026-10-27)

- **The daily import also writes closes**: when it has prices and the exchange holiday calendar can be read, it writes `close_price` in the same transaction as `daily_price`, with source 每日更新 (schema version 0030). A trades-only import or an unreadable calendar writes none, and a rejected file writes nothing at all. Writing closes triggers observation, and the observed-through day goes to the exit list, as with the price backfill.
- **Use 最新收盤價** (`last_close`), not 1日前收盤價.
- **The closing day follows each code's market**: the latest trading day of the code's market before the price date (skipping weekends and closed days in the calendar), not the price date minus one day. A Monday pull is Friday's close; when the US was closed on Monday, a Tuesday pull is Friday's close for US stocks and Monday's for Japanese stocks.
- **No closing day, no close**: codes whose exchange is not in the calendar (FX, rates, indices, and so on) are not stored, unlike the CSV; nor are codes whose intervening days are outside the calendar's coverage. Both are reported in the completion message and the import record.
- **The MarketData CSV and the historical closes take precedence**: the daily update only fills missing closes and replaces its own earlier ones (with old values in `close_price_change` when they differ); a close already stored by a CSV or the historical import stays, counted as not replaced. CSV and historical imports still replace a row from any source.
- **No separate backfill for days already in `daily_price` but not in the close table**: after updating, re-import that day's daily update (existing trades are not written again and the day's prices are replaced with the same values).

### Alternatives considered

- **One closing day (the weekday before the price date), skipping closed markets**: stores the same closes, but "each code's previous trading day" is easier to explain and needs no separate check of which markets were closed that day.
- **Last import wins regardless of source**: simplest, but a late BBG_Data pull (after Asian markets close) or a stale file used for backfill would overwrite correct CSV closes and then affect KO/KI.
- **A one-off backfill on the 維護 menu**: more code and tests, and older daily prices were sometimes the latest prices at pull time, so their dates are not reliable.
- **Storing FX and indices too (the CSV rule)**: their closing day cannot be inferred and their values are rounded, so they would keep replacing the CSV values.

### Consequences

- The MarketData CSV is no longer a daily step; observation uses the daily import's closes directly. Backfills after holidays or leave still use the CSV (ADR 0015), which overrides values the daily update wrote.
- BBG_Data holds the latest prices at pull time: a late pull or a stale file may assign closes to the wrong day when no CSV covers it. Backfills still tick 只匯交易明細.
- Underlyings whose exchange is not in the calendar get no closes from the daily update; observation already marks them 待確認.
- Schema version 0030 only widens the `close_price_import.source` check and leaves existing data untouched.
