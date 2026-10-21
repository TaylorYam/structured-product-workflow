# 0012: 核對條件的計算、保存與放行／Calculating, Storing, and Releasing Check Terms

- 狀態／Status: Accepted
- 日期／Date: 2026-10-21
- 追蹤／Tracking: #88
- 補充／Completes: [ADR 0006](0006-new-product-data-from-order.md)、[ADR 0008](0008-daily-prices-denomination-and-order-dates.md)、[ADR 0011](0011-strike-date.md) 的價格與交叉比對規則怎麼落到資料庫與畫面／how the price and cross-check rules of ADR 0006, 0008, and 0011 land in the database and the window

## 繁體中文

### 背景

ADR 0006、0008、0011 決定了進場價、執行價、下限價、KO 價怎麼算、分公司配置由交易明細算，以及交易明細和起單資料要交叉比對（ADR 0010 起不比承作金額）。#88 要把它們實作出來：算的結果是 #68 核對時讀的條件，所以要存在資料庫；不一致時要能放行，放行要留紀錄；股價和交易明細常常比起單資料晚到，要能自己補算。

### 決策

規則本身（#88 的 Issue，2026-10-21 使用者確認）：

- 取價日比 Strike date 晚的第一份股價檔；依交易所假日表確認 Strike date 是標的市場的交易日、而且和取價日之間沒有該市場的其他交易日，否則待確認。交易所不在假日表、日期超出涵蓋範圍也是待確認。
- 四個價格用股價檔原值各自 half-up 到小數第 4 位（執行價不是用四捨五入後的進場價算）。VWAP 四個價格留空、不算待確認。代碼找不到、沒有價格、衝突時只有那個標的留空，商品待確認並寫出標的、代碼、原因。還沒有股價檔是「等股價」，交易明細還沒有這檔是「等交易明細」。
- 交叉比對幣別、交易日、UF（交易明細是比例，換成百分點再比），交易列之間不一樣也算；放行記 Windows 使用者名稱、時間與放行了哪些差異，同樣的差異不再擋，差異改變要重新放行。
- 起單資料匯入或每日匯入完成後重算還沒鎖住的商品；已鎖住的不重算。

實作時決定（低風險、可回復）：

- **保存**：migration 0009 新增 `workflow.check_terms`（每檔一筆：狀態、原因、用的股價檔、差異）、`workflow.underlying_price`（每檔每個標的一筆：代碼與四個價格）與 `workflow.cross_check_release`（放行紀錄，一個差異一筆）。分公司配置照舊寫 `workflow.branch_allocation`，寄信草稿不用改。商品條件表 `workflow.product` 不加欄位：起單資料匯入只管條件，算出來的值另外放、整份取代。
- **什麼叫資料有變**：每次把還沒鎖住的商品全部算一遍（量很小），結果和資料庫裡上一次的不同才寫入、才算「有變」。不另存輸入的指紋：結果相同就沒有必要重寫，而且假日表或程式改了也會自然反映。
- **狀態只有一個**：待確認 ＞ 等交易明細 ＞ 等股價 ＞ 完成；同時在等別的資料時寫在原因裡。
- **放行的單位是一個差異**：以（項目、起單資料的值、交易明細的值）比對。只放行作業人員在畫面上看到的差異；資料在放行前變了，新的差異照樣待確認。紀錄不刪，留作歷史。
- **重算和匯入分開兩個 transaction**：匯入成功、重算失敗（例如假日表設定檔壞了）時匯入照樣保留，畫面寫出沒有重算的原因；排除後再匯入一次就會重算。
- **畫面**：「1 每日匯入」結果表多列出這次有變、或還沒完成的商品的核對條件；「2 起單資料」結果表多一欄「核對條件」。兩頁都有「放行差異…」，選到有還沒放行差異的商品才能按。

### 考慮過的替代方案

- **價格寫進 `workflow.product` 的 20 個欄位**：#68 讀起來少一次 join，但起單資料匯入與重算會改同一張表，覆蓋規則要分欄處理。
- **存輸入指紋、只重算指紋變了的商品**：「有變」的定義更貼字面，但要把假日表、程式版本都放進指紋，結果相同時也會白寫一次。
- **放行整檔而不是單一差異**：較簡單，但之後新出現的差異會被舊的放行蓋過，不符合「差異改變要重新放行」。
- **重算放在匯入的同一個 transaction**：一致性較強，但重算失敗會連匯入一起退回，作業人員要等設定修好才能匯入交易明細。

### 後果

- 更新程式後要跑 `spw-db-migrate`（資料表版本 0009）。
- #68 核對時讀 `workflow.check_terms`、`workflow.underlying_price` 與 `workflow.branch_allocation`；鎖住後這些值不再重算。
- 以前清單更新建立、還沒鎖住的商品也會重算，分公司配置改由交易明細算（有交易列時）。
- 港股仍不在交易所假日表，相關商品會待確認（[待定事項](../open-questions.md)）。

## English

### Context

ADR 0006, 0008, and 0011 decided how initial, strike, KI, and KO prices are computed, that branch allocations come from trade rows, and that trade rows are cross-checked against the order terms (without the order amount since ADR 0010). #88 implements them: the results are the terms #68 checks against, so they live in the database; mismatches must be releasable with a record; and prices and trade rows often arrive after the order terms, so the calculation has to catch up on its own.

### Decision

The rules (the #88 Issue, confirmed by the user 2026-10-21):

- Use the first price file dated after the Strike date; the exchange holiday calendar must confirm that the Strike date is a trading day of the underlying's market and that no other trading day of that market lies between it and the price date, otherwise 待確認. Exchanges not in the calendar and dates outside its range are also 待確認.
- Each of the four prices is computed from the raw price file value and rounded half-up to 4 decimals (the strike price is not computed from the rounded initial price). VWAP leaves all four blank without 待確認. A missing code, missing price, or conflict blanks only that underlying and marks the product 待確認 with the underlying, code, and reason. No price file yet is 等股價; no trade rows yet is 等交易明細.
- Cross-check currency, trade date, and UF (trade rows hold a ratio, converted to percentage points); differences between trade rows also count. A release records the Windows user name, time, and the released differences; the same differences no longer block, and changed differences need a new release.
- Recalculate unlocked products after an order-sheet import or a daily import; locked products are never recalculated.

Implementation choices (low risk, reversible):

- **Storage**: migration 0009 adds `workflow.check_terms` (one row per product: status, reason, price file used, differences), `workflow.underlying_price` (one row per product and underlying: code and the four prices), and `workflow.cross_check_release` (one row per released difference). Branch allocations still go to `workflow.branch_allocation`, so mail drafts are unchanged. `workflow.product` gets no new columns: the order-sheet import owns the terms, and calculated values live apart and are replaced as a whole.
- **What "changed" means**: every unlocked product is recalculated each time (the volume is small) and written, and reported as changed, only when the result differs from the stored one. No input fingerprint is kept: an identical result needs no rewrite, and calendar or code changes show up naturally.
- **One status**: 待確認 > 等交易明細 > 等股價 > 完成; other pending data is mentioned in the reason.
- **A release covers one difference**, matched by (item, order value, trade value). Only the differences the operator saw are released; if the data changed meanwhile, the new differences stay 待確認. Records are never deleted.
- **Recalculation runs in its own transaction after the import**: if it fails (for example a broken holiday calendar file) the import is kept and the window says why nothing was recalculated; importing again after the fix recalculates.
- **Window**: the "1 每日匯入" result table also lists the check terms of products that changed or are not done; the "2 起單資料" result table gains a 核對條件 column. Both pages have "放行差異…", enabled only for a product with unreleased differences.

### Alternatives considered

- **Twenty price columns on `workflow.product`**: one join fewer for #68, but the order import and the recalculation would write the same table and need per-column overwrite rules.
- **An input fingerprint, recalculating only products whose fingerprint changed**: closer to the literal "data changed", but the calendar and code version must be part of it, and identical results would still be rewritten.
- **Releasing a whole product instead of single differences**: simpler, but later differences would be hidden by an old release, contrary to "changed differences need a new release".
- **Recalculating inside the import transaction**: stronger consistency, but a recalculation failure would roll back the import, blocking trade-row imports until the configuration is fixed.

### Consequences

- Run `spw-db-migrate` after updating (schema version 0009).
- #68 reads `workflow.check_terms`, `workflow.underlying_price`, and `workflow.branch_allocation`; these values are no longer recalculated once locked.
- Unlocked products created by the old 清單更新 are recalculated too, and their branch allocations are derived from trade rows when there are any.
- Hong Kong stocks are still missing from the exchange holiday calendar, so their products are 待確認 ([open questions](../open-questions.md)).
