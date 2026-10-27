# 0025: 標的價格的基準日／Price Basis Date of Underlying Prices

- 狀態／Status: Accepted
- 日期／Date: 2026-10-27
- 追蹤／Tracking: #275
- 補充／Completes: [ADR 0024](0024-stock-splits-in-observation.md)

## 繁體中文

### 背景

ADR 0024 假設商品的進場價、執行價、下限價、KO 價都是 Strike date 當時的股數基準，比價時只把之後的拆股換算到收盤價上。實際資料裡，舊商品（以前清單更新、舊商品補資料匯入的）有相當比例的價格已經是上手依拆股調整過的，連 Strike date 之後很久才發生的拆股都含在裡面；同一個代碼兩種都有。這種商品登記拆股後收盤價又乘一次比例，等於換算兩次，2026-10-27 在本機試登記時就算出一筆錯的 KO。

### 決策（2026-10-27 使用者確認，選 (a)）

- **每個標的存一個價格基準日**：`workflow.underlying_price.price_basis_date`（資料表版本 0029）。空白是 Strike date；有值表示四個價格已經含到那天生效的拆股。
- **由程式推算，不人工填**：寫價格的地方（核對條件重算、舊商品補資料、核對寫回）都不知道價格是哪一天的基準（Excel、TS 上沒有），所以比價全部時先依當時的拆股登記重算（`stock_splits.refresh_basis`），有變才寫。推法：拿進場價跟 Strike date 當天（或之前 10 天內最近一天）的收盤價比，候選是「不含」與「含到 Strike date 之後第 1、2…次拆股」，取比例最接近 1 的；最接近的也超出 0.6～1 ÷ 0.6 時留空白。確認不是拆股（1:1）不算。
- **換算往兩個方向**：收盤日在基準日之後，乘上 (基準日, 收盤日] 生效的拆股；在之前，除以 (收盤日, 基準日] 生效的拆股。
- **對不起來就待確認**：Strike date 之後有登記拆股的標的，進場價跟 Strike date 的收盤價換到價格基準後比例超出 0.6～1 ÷ 0.6 時，比價狀態待確認，原因寫標的、進場價、收盤日與換算後的值，不比下去。
- **重新比的已出場商品不限生效日以後出場的**：價格已經調整過的標的，生效日以前的收盤價也要換算，所以新增、刪除拆股登記後，用到這個代碼、Strike date 在生效日之前、事件全部來自比價的已出場商品都重新比（ADR 0024 原本只比最晚事件不早於生效日的）。
- 拆股登記視窗存檔後告訴作業人員：這個代碼有幾個標的的價格已經含這次拆股。

### 考慮過的替代方案

- **(b) 每次比價時推，不存**：結果一樣，但查不到每個標的當時被當成哪個基準。
- **(c) 匯入時把價格改回原始基準**：改到已鎖住、核對過的值，違反 ADR 0024 不改商品價格的決定。
- **寫價格時由作業人員填基準日**：Excel 與 TS 上沒有這個資訊，作業人員也得自己推，而且舊資料數量大。

### 後果

- 資料表版本 0029 只加一欄，不動既有資料；升級後第一次比價補上。
- 推算靠 Strike date 的收盤價：Strike date 之後有拆股、收盤價表卻缺那幾天的標的推不出來，比價狀態待確認；進場價本來就跟收盤價差很多的（例如不是用收盤價定價）在有拆股時可能被標待確認。
- 比例接近的兩個候選（例如 2 拆 3）可能推錯；由「對不起來就待確認」與作業人員確認兜底。

## English

### Context

ADR 0024 assumes a product's initial, strike, KI, and KO prices are on the share basis of the Strike date, so observation converts only later splits onto the closes. In real data a sizeable share of old products (imported by the former list update or the old-product backfill) carry prices the issuer already adjusted for splits, including splits long after the Strike date; one code can have both kinds. Registering a split then multiplies those closes again, converting twice; a trial registration on 2026-10-27 produced a wrong KO this way.

### Decision (confirmed by the user 2026-10-27, option (a))

- **Each underlying stores a price basis date**: `workflow.underlying_price.price_basis_date` (schema version 0029). Blank means the Strike date; a value means the four prices already include splits effective up to that day.
- **Inferred by the program, not entered by hand**: the writers of prices (check-terms recalculation, old-product backfill, check write-back) do not know the basis (the Excel and the TS do not say), so observing everything first recomputes it from the current split registry (`stock_splits.refresh_basis`), writing only changes. The rule: compare the initial price with the close on the Strike date (or the latest within the 10 days before), with candidates "none" and "up to the 1st, 2nd, … split after the Strike date", taking the ratio closest to 1; when even the closest is outside 0.6 to 1 ÷ 0.6 the date stays blank. "Not a split" (1:1) entries do not count.
- **Conversion works both ways**: a close after the basis date is multiplied by the splits effective in (basis, close day]; a close before it is divided by those effective in (close day, basis].
- **A mismatch needs confirmation**: for an underlying with a registered split after the Strike date, when the initial price and the Strike-date close converted to the price basis differ by a ratio outside 0.6 to 1 ÷ 0.6, the observation status is 待確認 with the underlying, initial price, close day, and converted value, and observation stops.
- **Re-observed exits are no longer limited to exits on or after the effective date**: for adjusted prices, closes before the effective date are converted too, so after adding or deleting an entry every exited product using the code, with a Strike date before the effective date and events all from observation, is observed again (ADR 0024 only took those whose latest event was not before the effective date).
- After saving, the split window tells the operator how many underlyings of the code already include that split.

### Alternatives considered

- **(b) Infer at every observation without storing**: the same results, but no record of which basis each underlying was treated as.
- **(c) Convert prices back to the original basis on import**: changes locked, checked values, against ADR 0024's decision not to change product prices.
- **Operators enter the basis date when prices are written**: neither the Excel nor the TS states it, operators would have to infer it themselves, and the old data is large.

### Consequences

- Schema version 0029 only adds a column without touching existing data; the first observation after the upgrade fills it.
- Inference depends on the Strike-date close: an underlying with a split after the Strike date whose closes for those days are missing cannot be inferred and is marked 待確認; products whose initial price differs a lot from the close anyway (for example, not fixed on the close) may be marked 待確認 when a split exists.
- Two close candidates (for example, a 2-for-3 split) can be confused; the 待確認 check and operators are the backstop.
