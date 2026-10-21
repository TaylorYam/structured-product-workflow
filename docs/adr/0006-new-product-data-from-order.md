# 0006: 新商品資料改由 Order、交易明細與股價建立／Build New Product Data from Orders, Trade Rows, and Prices

- 狀態／Status: Accepted；待定事項由 [ADR 0008](0008-daily-prices-denomination-and-order-dates.md) 補上，過渡期「清單更新照樣匯入、只比對」由 ADR 0008 取代；「確認 Order 條件清單後匯入」改為在視窗裡確認，由 [ADR 0009](0009-confirm-order-terms-in-window.md) 取代；條件改從起單建檔讀、不再讀 `(Order)` 原信，承作金額不再比對，由 [ADR 0010](0010-order-terms-from-daily-order-sheet.md) 取代／Accepted; open items completed by ADR 0008, and the transition rule of importing 清單更新 for comparison superseded by ADR 0008; reviewing the Order term list before import is replaced by in-window confirmation per ADR 0009; terms now come from the daily order sheet instead of `(Order)` emails, and the order amount is no longer compared, per ADR 0010
- 日期／Date: 2026-10-20
- 追蹤／Tracking: #54（範圍改寫）
- 部分取代／Partly supersedes: [ADR 0003](0003-minimal-fcn-database.md) 中「商品條件以清單更新（庫存總表）為來源」／ADR 0003's use of 清單更新 (the inventory master) as the source of product terms

## 繁體中文

### 背景

`workflow.product` 目前只從「清單更新」匯入。庫存總表是建檔之後才有的，ISIN、發行日、比價日與標的價格都已經從 TS 抄過去；拿它核對 TS 等於拿 TS 對 TS，也無法在核對時就有資料。新商品在核對當下能取得的可靠資料只有三個：作業人員輸入的交易明細、交易室實際送給上手的 Order，以及隔天早上取得的股價。

### 決策（2026-10-20 使用者確認）

- **來源**：條件以 Order 為準（作業人員確認 Order 條件清單、補好淺紅格子後，按「匯入 Order 條件」寫入；不讀完直接寫）。承作金額與分公司配置以交易明細為準。期初定價、標的代碼以 Order 為準。
- **價格**：進場價取 Order 期初定價指定的交易日開盤價或收盤價；執行價、下限價、KO 價 = 進場價 × K%／KI%／KO%，四捨五入到小數第 4 位。
- **重複匯入**：TS 核對通過前，重新匯入 Order 條件就覆蓋並留每次紀錄；通過後鎖住，要改走「更正商品條件」。
- **交叉比對**：交易明細金額加總與 Order 的金額、幣別、交易日、UF 不一致時標「待確認」並寫出兩邊的值，人看過可放行；已取消的交易列不算。
- **核對與回填**：核對從資料庫讀條件，核對完直接把 ISIN、發行日、比價日與 VWAP 價格回填進商品資料。TS 核對通過即是 Order 條件的第二次驗證。不一致時由作業人員判斷：上手錯照舊以「_待確認」放回；Order 條件錯則修正後重新匯入再核對；或人工放行。
- **過渡期**：清單更新照樣匯入，只比對、不覆蓋，以新流程為準，穩定後由使用者決定停用；Excel 參考條件表保留到資料庫流程完全處理好，用 Excel 核對時不寫資料庫；寄信草稿暫時維持「有標準檔名 PDF 即可建」，每日流程順了再改成「核對通過並回填完成」；進度分頁等合成一個 PANEL 時再做。

### 考慮過的替代方案

- **繼續以清單更新為核對來源**：不用改資料流，但核對時資料常常還不存在，而且是拿 TS 抄出來的值對 TS。
- **Order 讀完直接寫入**：少一步，但程式讀錯或人工回填的格子無法先確認。
- **核對工具只寫結果表、由本專案搬進商品資料**：權限較小；但兩個專案合併（[ADR 0005](0005-merge-checker-into-workflow.md)）後同一套程式依序執行，直接回填較單純。

### 後果

- 商品資料在核對前就存在，ISIN、發行日、比價日等欄位要允許暫時空白，直到回填。
- 需要新的資料：Order 條件（含匯入紀錄）、每日股價（新 schema）、核對結果。
- 尚未決定：股價的取得與匯入方式（使用者會提供涵蓋全部可承作標的的範本）、單位面額的來源、Order 沒寫最終比價日或到期日時如何處理。

## English

### Context

`workflow.product` is imported only from 清單更新. The inventory master exists only after booking, and its ISIN, issue date, observation dates, and underlying prices were already copied from the TS; checking against it compares the TS with itself, and the data is often absent at checking time. The reliable data available when checking a new product is the operator-entered trade rows, the Order the trading desk actually sent to the issuer, and the next morning's prices.

### Decision (confirmed by the user 2026-10-20)

- **Sources**: terms come from the Order (operators review the Order term list, fill light-red cells, then press "Import Order terms"; parsing does not write directly). Amounts and branch allocations come from the trade rows. Initial pricing and underlying codes come from the Order.
- **Prices**: the initial price is the trade-date open or close named by the Order's initial pricing; strike, KI, and KO prices = initial price × K%/KI%/KO%, rounded half-up to 4 decimals.
- **Re-imports**: before the TS check passes, re-importing Order terms overwrites and records each import; after it passes the terms lock and changes go through a "correct product terms" flow.
- **Cross-checks**: when the trade-row amount total, currency, trade date, or UF differs from the Order, the product is marked 待確認 with both values; a person may release it. Cancelled trade rows do not count.
- **Checking and backfill**: checking reads terms from the database and writes ISIN, issue date, observation dates, and VWAP prices directly into product data. A passing TS check is a second verification of the Order terms. On a mismatch the operator decides: issuer error returns `_待確認` files as today; an Order-term error is corrected, re-imported, and rechecked; or the operator releases it manually.
- **Transition**: 清單更新 keeps being imported for comparison only, without overwriting, with the new flow authoritative, until the user decides to stop it. The Excel reference workbook stays until the database flow is complete; Excel-based checks do not write to the database. Mail drafts keep requiring only standard-name PDFs until the daily flow runs smoothly, then require a passed check with backfill. A progress tab waits for the single PANEL.

### Alternatives considered

- **Keep 清單更新 as the checking source**: no data-flow change, but data is often missing at checking time and the TS is checked against values copied from it.
- **Write Order terms directly after parsing**: one step fewer, but misreads and manually filled cells cannot be reviewed first.
- **Checker writes only results; this project moves them into product data**: narrower privileges, but after the merge ([ADR 0005](0005-merge-checker-into-workflow.md)) one program runs the steps in order, so direct backfill is simpler.

### Consequences

- Product data exists before checking, so ISIN, issue date, observation dates, and similar fields must allow blanks until backfilled.
- New data is needed: Order terms (with import records), daily prices (new schema), and check results.
- Open: how prices are obtained and imported (the user will provide a template covering all tradable underlyings), the source of the denomination, and what to do when an Order omits the final valuation or maturity date.
