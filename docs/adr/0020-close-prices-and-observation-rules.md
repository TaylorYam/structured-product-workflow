# 0020: 收盤價資料與比價規則／Close Prices and Observation Rules

- 狀態／Status: Accepted
- 日期／Date: 2026-10-22
- 追蹤／Tracking: 實作 Issue 由本 ADR 的 PR 之後拆出／implementation issues are split off after this ADR's PR
- 補充／Completes: [ADR 0019](0019-progress-step-records-and-lifecycle-events.md) 的存續事件由誰產生；[ADR 0008](0008-daily-prices-denomination-and-order-dates.md)、[ADR 0015](0015-price-csv-backfill.md) 的股價（取價日）只用於核對條件／who produces the lifecycle events of ADR 0019; the price-date prices of ADR 0008 and ADR 0015 serve check terms only

## 繁體中文

### 背景

存續狀態要靠比價更新，需要每檔商品每個標的在每一個交易日的收盤價。`workflow.daily_price` 以取價日為鍵、內容是前一個交易日的價格（ADR 0008），CSV 回補存成下一天（ADR 0015）。核對條件用「Strike date 之後第一份股價檔」沒有問題，但比價要的是「某一天的收盤價」：美國放假、日本開市的那個早上，同一份 BBG_Data 裡美股是上週五、日股是前一天，一個取價日表示不了兩個市場。

使用者提供了歷史收盤價 HP.xlsx（只放在私人樣本倉庫）：507 檔標的，2022-07-28 到 2026-10-21 每個平日一欄、每列一檔，代碼帶 Equity／Curncy 字尾；和 Bloomberg 的 `MarketData_YYYYMMDD.csv` 對過，欄頭日期底下就是那一天的收盤價；假日寫 `#N/A N/A`；另有「#N/A Requesting Data...」抓到一半的格子、`#N/A Invalid Security`、`-` 與亂碼。MarketData CSV 每天都有，以收盤日為鍵。交易所假日表只有美國、日本，HP 有 16 檔港股。資料庫的比價日：D 型只存「比價日_{Non-Call}」與最後一期（正好是每日觀察區間的頭尾），P 型存 Non-Call 起每一期。

驗證：舊庫存總表 9/1 到 10/12 的 202 檔「已於某日 KO」，用下面的規則與 HP 重算，199 檔日期完全相同；2 檔差一天是 HP 的壞格子被跳過造成，1 檔（SG）程式比總表早 6 天，待人工查證。總表 841 檔裡 KI 型態是到期觀察 416 檔、沒有 KI 412 檔、每日觀察 13 檔、每期觀察 0 檔。

### 決策（2026-10-22 使用者確認）

**收盤價表**

- 新增 `workflow.close_price`（收盤日、代碼、收盤價、匯入紀錄），以實際收盤日為鍵；`daily_price` 不改，照舊只算核對條件。
- 代碼正規化：去掉彭博代碼最後的 Equity／Curncy 這類類別字，存成和 `workflow.product.underlying_n` 一樣的「代號 交易所碼」，其餘一字不差。
- 來源：(1) HP.xlsx 一次匯入，「維護」選單「匯入歷史收盤價…」，橫向日期、每列一檔，整份檢查後寫入，問題寫錯誤清單；(2) 每天的 MarketData CSV：「1 每日匯入」的回補股價動作同時寫 `daily_price`（ADR 0015 的下一天）與 `close_price`（CSV 的日期原樣），每天都要匯；BBG_Data 不寫 `close_price`，它的日期不明確。
- 有效性：代碼在日期 D 的價格只有 D 是該代碼市場的交易日（週一到週五且不在假日表）才算；市場休市那天 CSV 帶的前一天價格忽略；HP 的 `#N/A N/A` 要落在休市日，錯誤值一律視為沒有價格；交易日沒有價格就是缺價格。同一（收盤日、代碼）再匯入整筆取代並留匯入紀錄。
- 交易所假日表補港股（HK），維護方式同美、日。

**比價**

- 範圍：核對條件「完成」的商品（進場價、執行價、下限價、KO 價已算出或由 TS 回填），已鎖住與未鎖住都比，未鎖住的結果標「未鎖住」；舊清單商品補齊價格與比價日後一樣比；已有存續事件（含顧客贖回）的商品停止。
- 時機：每次匯入收盤價（CSV、HP）後重算全部還在庫的商品，和核對條件一樣「算一遍、有變才寫」；「維護」選單另有「重新比價全部」。
- KO：用收盤價，條件是收盤價 ≥ KO 價。非記憶式：同一個觀察日所有標的都 ≥ 各自的 KO 價；記憶式：每檔標的在任一觀察日 ≥ 就記住，全部記過即 KO，不必同一天。D 型：觀察日是「比價日_{Non-Call}」那天起到最終比價日止，每一個所有標的市場都開市的日子（一籃子預定交易日）；P 型：只看資料庫的各期比價日。保證期之前不觀察，已由存的日期表達。
- KI：EKI 只在最終比價日，收盤價 < 下限價；AKI 自 Strike date 的次一個交易日起到最終比價日止，任一該標的市場的交易日收盤價 < 下限價即觸及；MKI（每期觀察）第一版不比，比價狀態標待確認「每期觀察 KI 需人工追蹤」，KO 照比。
- 到期（最終比價日）：沒有 KI 的商品，或有 KI 且曾觸及，且最差表現標的的收盤價 < 執行價 → 接股，標的是最差表現那檔（最終收盤價 ÷ 進場價最低）；否則到期還本。有 KI 但未觸及 → 到期還本。
- 事件日是觸價或判定的那個觀察日；不推算上手贖回日。干擾日（市場中斷）不處理。
- 缺價格就停住：某標的在它市場的交易日沒有價格，這檔停在那一天，比價狀態「等股價」寫出標的與日期，之後的日子不看；價格補進來下一次重算自然接上。
- 比價狀態：每檔一筆 `workflow.pricing_status`（狀態、原因、已比到的日期、算出時間）：在庫（已比到某日）／等股價／待確認（代碼對不到、市場不在假日表、日期超出涵蓋範圍、MKI、和舊清單事件不同）／已出場（有存續事件）。事件寫進 `workflow.product_event`（ADR 0019）。

**舊商品**

- 「維護」選單「匯入舊商品的價格與比價日…」：讀參考條件表格式的 Excel（例如回填後的庫存總表），只補已鎖住、還沒有 `underlying_price` 或 `autocall_date` 的商品的四個價格與比價日 1～12，其他欄不碰；表上的「已於 Y/M/D KO」「到期還本」寫成來源「舊清單」的事件。之後比價照樣重算，結果和舊清單事件不同時標待確認，等人看過。

### 考慮過的替代方案

- **用 daily_price 推收盤日**：多市場共用一個取價日，對不出各市場的收盤日。
- **BBG_Data 當每日來源**：它抓的是「最新」價格，日期不明確；CSV 本來就以收盤日為鍵，而且每天都有。
- **缺價格的日子跳過**：驗證時兩檔因此錯一天。
- **核對寫回時存整張日期表以支援 MKI**：要動核對寫回；目前 841 檔沒有 MKI，等有樣本再做。
- **只比已鎖住的商品**：AKI 從 Strike date 隔天就開始觀察，TS 常晚幾天才到，會漏掉前幾天。
- **直接相信舊清單的事件、不重算**：失去一次復核；這次驗證就找到 3 檔要看。

### 後果

- 每天多一個固定動作：匯 MarketData CSV；忘了匯，所有商品停在「等股價」，進度頁看得到。
- HP 的壞格子（Requesting Data、亂碼）要重抓再匯，否則那些日子是缺價格。
- 假日表每年要補下一年，含港股。
- 新增 `close_price`、`pricing_status` 與匯入紀錄；更新後要跑 migration。
- 存續狀態從此由程式決定；上手通知不同時走異議（ADR 0019）。
- 比價引擎的測試用虛構價格與虛構商品；舊總表的 202 檔驗證只在本機做。
- 上手贖回日不存，之後需要再加欄。

## English

### Context

Updating the lifecycle status by observation needs the close of every underlying of every product on every trading day. `workflow.daily_price` is keyed by the price date and holds the previous trading day's prices (ADR 0008); the CSV backfill stores the next day (ADR 0015). That works for check terms, which use "the first price file after the Strike date", but observation needs "the close on a given day": on a morning when the US was closed and Japan was open, one BBG_Data file holds last Friday's US closes and yesterday's Japanese closes, and a single price date cannot express both markets.

The user supplied a historical close workbook, HP.xlsx (kept only in the private samples repository): 507 instruments, one column per weekday from 2022-07-28 to 2026-10-21, one row per instrument, codes carrying Equity/Curncy suffixes; checked against Bloomberg's `MarketData_YYYYMMDD.csv`, the value under a header date is that day's close; holidays read `#N/A N/A`; there are also half-fetched "#N/A Requesting Data..." cells, `#N/A Invalid Security`, `-`, and garbled cells. The MarketData CSV is available every day and is keyed by the closing day. The exchange holiday calendar covers only the US and Japan, while HP has 16 Hong Kong stocks. Observation dates in the database: D-type products store only 比價日_{Non-Call} and the last period (exactly the ends of the daily observation window); P-type products store every period from Non-Call on.

Validation: of the 202 products the old inventory master (9/1 to 10/12) marks "KO on date", recomputing with the rules below and HP gives the identical date for 199; 2 differ by one day because a bad HP cell was skipped, and 1 (SG) comes out 6 days earlier than the master and awaits a manual check. Of the master's 841 products, 416 have at-maturity KI, 412 have no KI, 13 have daily KI, and none has periodic KI.

### Decision (confirmed by the user 2026-10-22)

**Close-price table**

- A new `workflow.close_price` (closing day, code, close, import record) keyed by the actual closing day; `daily_price` is unchanged and still serves only check terms.
- Code normalization: drop the trailing asset-class word (Equity, Curncy) from the Bloomberg code and store "ticker exchange" exactly as `workflow.product.underlying_n` does; everything else must match character for character.
- Sources: (1) HP.xlsx once, through "匯入歷史收盤價…" on the 維護 menu, dates across and one instrument per row, validated as a whole before writing, with problems written to an error list; (2) the daily MarketData CSV: the price-backfill action on "1 每日匯入" writes both `daily_price` (ADR 0015's next day) and `close_price` (the CSV's date as is), and it must be imported every day; BBG_Data never writes `close_price`, because its date is ambiguous.
- Validity: a code's price on day D counts only when D is a trading day of that code's market (Monday to Friday and not in the holiday calendar); values the CSV carries forward on a market holiday are ignored; HP's `#N/A N/A` must fall on closed days, and any error value means no price; a trading day without a price is a missing price. Re-importing the same (closing day, code) replaces the row and records the import.
- Hong Kong (HK) is added to the exchange holiday calendar, maintained like the US and Japan.

**Observation**

- Scope: products whose check terms are 完成 (initial, strike, KI, and KO prices calculated or backfilled from the TS), locked or not, with unlocked results flagged 未鎖住; old-list products once their prices and observation dates are backfilled; products with a lifecycle event (including 顧客贖回) are no longer observed.
- Timing: after every close-price import (CSV, HP) all in-stock products are recalculated, as check terms are, "calculate everything, write only what changed"; the 維護 menu also gets "重新比價全部".
- KO: closes only, with close ≥ KO price. Non-memory: every underlying ≥ its KO price on the same observation day; memory: an underlying that is ≥ on any observation day is remembered, and KO occurs once all are remembered, not necessarily on the same day. D-type: observation days are every day from 比價日_{Non-Call} to the final valuation date on which every underlying's market is open (the basket's scheduled trading days); P-type: only the stored observation dates. Nothing is observed before the guaranteed period, which the stored dates already express.
- KI: EKI only on the final valuation date, close < KI price; AKI from the trading day after the Strike date to the final valuation date, touched when the close on any trading day of that underlying's market is < KI price; MKI (periodic) is not observed in the first version, the observation status is 待確認 "每期觀察 KI 需人工追蹤", and KO is still observed.
- Maturity (final valuation date): a product without KI, or with KI that was touched, whose worst performer closes < strike price → 接股 with the worst performer as the underlying (lowest final close ÷ initial price); otherwise 到期還本. KI present but never touched → 到期還本.
- The event date is the observation day of the trigger or determination; the issuer's redemption date is not inferred. Disrupted days are not handled.
- Stop at a missing price: when an underlying has no price on a trading day of its market, the product stops on that day with observation status 等股價 naming the underlying and the date, and later days are not examined; once the price arrives the next run carries on.
- Observation status: one row per product in `workflow.pricing_status` (status, reason, compared-through date, calculated at): 在庫 (compared through a date) / 等股價 / 待確認 (unmatched code, market not in the calendar, date outside its coverage, MKI, disagreement with an old-list event) / 已出場 (a lifecycle event exists). Events go to `workflow.product_event` (ADR 0019).

**Old products**

- "匯入舊商品的價格與比價日…" on the 維護 menu reads a reference-workbook-format Excel (for example the backfilled inventory master) and fills only the four prices and 比價日 1 to 12 of locked products that still lack `underlying_price` or `autocall_date`, touching nothing else; the sheet's "已於 Y/M/D KO" and "到期還本" become events with source 舊清單. Observation then recalculates them as usual, and a result that differs from an old-list event is marked 待確認 for a person to review.

### Alternatives considered

- **Deriving the closing day from daily_price**: one price date shared by several markets cannot be mapped to each market's closing day.
- **BBG_Data as the daily source**: it fetches the "latest" prices with an ambiguous date; the CSV is already keyed by the closing day and available every day.
- **Skipping days with a missing price**: the validation showed two products off by one day for exactly this reason.
- **Storing the whole date table at check write-back to support MKI**: changes the check write-back; none of the 841 products has MKI, so it waits for a sample.
- **Observing only locked products**: AKI starts the day after the Strike date and the TS often arrives days later, so early days would be missed.
- **Trusting old-list events without recalculating**: loses a review; this validation alone found 3 products to look at.

### Consequences

- One more fixed daily action: importing the MarketData CSV; a missed import leaves every product at 等股價, visible on the progress tab.
- HP's bad cells (Requesting Data, garbled) must be re-fetched and re-imported, or those days count as missing prices.
- The holiday calendar needs next year's dates every year, Hong Kong included.
- New `close_price`, `pricing_status`, and import records; run the migration after updating.
- The lifecycle status is now decided by the program; a differing issuer notice goes through an objection (ADR 0019).
- The observation engine is tested with synthetic prices and products; the 202-product validation against the old master runs only locally.
- The issuer's redemption date is not stored and needs a column later if required.
