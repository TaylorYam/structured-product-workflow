# 0011: 期初定價日 Strike date／Strike Date for Initial Pricing

- 狀態／Status: Accepted
- 日期／Date: 2026-10-21
- 追蹤／Tracking: #101
- 部分取代／Partly supersedes: [ADR 0010](0010-order-terms-from-daily-order-sheet.md) 的「明日開盤價標紅擋匯入、不自動換」／ADR 0010's "明日開盤價 is highlighted red and blocks import, never converted"

## 繁體中文

### 背景

起單建檔的「期初價格」有時寫「明日開盤價」：取交易日隔天的開盤價（使用者 2026-10-21 確認；樣本裡都是日股）。#97 的期初價格只有 開盤價／收盤價／VWAP，讀到「明日開盤價」會擋匯入；改成「開盤價」又會變成取交易日當天的開盤價。#88 算進場價時要知道取哪一天的股價。

### 決策（2026-10-21 使用者確認）

- 期初價格維持 開盤價／收盤價／VWAP 三選一；另加 **Strike date**（期初定價日），存在 `workflow.product.strike_date`。
- Strike date 正常等於 Trade date。期初價格寫「明日開盤價」時，期初價格記成開盤價，Strike date = 交易日之後標的市場的下一個營業日（`config/exchange_holidays.toml`，跳過週末與任一標的交易所的休市日）。
- 假日表確定不了（標的交易所不在表上、日期超出範圍）、或好幾列的 Trade date／期初價格不同時，Strike date 留空標紅，由作業人員填；Strike date 不能早於 Trade date。
- migration 0008 加欄位，已有的商品補成交易日。
- #88 用 Strike date（不是 Trade date）找股價檔。核對時怎麼比 TS 上的定價日由 #68 處理。

### 考慮過的替代方案

- **期初價格加第四個選項「明日開盤價」**：一欄就夠，但「哪一天」和「開盤或收盤」混在同一個值，#88、核對都要再解讀一次；假日順延也沒有地方記。
- **維持擋下、人工改**：改成「開盤價」會取錯日期。

### 後果

- 「2 起單資料」多一欄 Strike date（必填），一般情況自動帶 Trade date。
- 作業人員在畫面上改 Trade date 時，原本等於 Trade date 的 Strike date 跟著改；明日開盤價算出的不動，早於 Trade date 時擋匯入。
- 算不出來的 Strike date 不從資料庫補值，一定要作業人員填。
- 更新程式後要跑 `spw-db-migrate`（資料表版本 0008）。

## English

### Context

The order sheet's 期初價格 sometimes says 明日開盤價: the open on the day after the trade date (confirmed by the user 2026-10-21; samples are all Japanese stocks). #97 only allows 開盤價/收盤價/VWAP, so 明日開盤價 blocks the import, while changing it to 開盤價 would mean the trade date's own open. #88 needs to know which day's price to use for the initial price.

### Decision (confirmed by the user 2026-10-21)

- 期初價格 stays 開盤價, 收盤價, or VWAP; a new **Strike date** (initial pricing date) is stored in `workflow.product.strike_date`.
- Strike date normally equals Trade date. When 期初價格 says 明日開盤價, 期初價格 becomes 開盤價 and Strike date is the underlyings' next business day after the trade date (`config/exchange_holidays.toml`, skipping weekends and any underlying exchange's holidays).
- When the calendar cannot tell (an exchange not listed, a date outside its range) or duplicate rows differ in Trade date or 期初價格, Strike date stays blank and red for the operator; Strike date may not be earlier than Trade date.
- Migration 0008 adds the column and fills existing products with their trade date.
- #88 finds the price file by Strike date rather than Trade date. How checking compares the TS pricing date is left to #68.

### Alternatives considered

- **A fourth 期初價格 choice 明日開盤價**: one column, but "which day" and "open or close" share a value that #88 and checking would each have to decode, with nowhere to record holiday shifts.
- **Keep blocking and fix by hand**: changing it to 開盤價 picks the wrong day.

### Consequences

- "2 起單資料" gains a required Strike date field, normally filled with Trade date.
- Editing Trade date on screen moves a Strike date that equalled it; a 明日開盤價 Strike date stays, and one earlier than Trade date blocks import.
- A Strike date that could not be worked out is never filled from the database; the operator must enter it.
- Run `spw-db-migrate` after updating (schema version 0008).
