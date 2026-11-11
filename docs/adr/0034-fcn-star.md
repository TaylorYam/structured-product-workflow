# 0034: FCN_Star 與 FCN_OneStar：到期上限價由人判斷／FCN_Star and FCN_OneStar: a Person Decides the Maturity Cap

- 狀態／Status: Accepted
- 日期／Date: 2026-11-11
- 追蹤／Tracking: #538
- 補充／Completes: [ADR 0020](0020-close-prices-and-observation-rules.md)（比價規則）、[ADR 0032](0032-manual-check.md)（人工核對完成）／ADR 0020 (observation rules) and ADR 0032 (manual check)

## 繁體中文

### 背景

目標是所有 ELN 都能比價（ADR 0033）。FCN_Star、FCN_OneStar 不在支援的商品種類，每日匯入整列略過，不比價。目前各 1 檔，都是 CA。CA「Safety FCN」的說明書（086001104869）：

- KO 每日觀察、記憶式，自第 1 個配息期結束日（含）到期末訂價日（**不含**）。
- 沒有 KI。
- 到期：任一標的期末價 ≥ 上限價（例：期初的 105%，這檔和 KO% 相同）就還本；沒有上限事件時照 FCN，最差標的低於執行價接股，否則還本。

FCN_OneStar 沒有說明書。

### 決策

照 #538 的最小做法：

- `fcn_checker.products.PRODUCT_RULES` 新增 `FCN_Star`、`FCN_OneStar`：最終比價日不算 KO（同 Super Bull），其他照 FCN。每日匯入、起單資料匯入、人工維護、回補因此會收這兩種。FCN_OneStar 先用同一套規則。
- 不新增上限價欄。到期判定問商品規則 `delivery_needs_review`：
  - 最差標的 ≥ 執行價 → 到期還本（有沒有上限事件結果都一樣）。
  - 最差標的 < 執行價（要接股的情況）→ 比價待確認，原因寫「最差標的 … 低於執行價：要確認最終比價日 … 有沒有標的 ≥ 上限價（有就到期還本，沒有就接股）」，不自動接股；作業人員判斷後登記存續事件。
- 不開放自動核對（`[auto_check_products]` 不列），新案走人工核對完成，既有的走 FCN 以外商品回補。

### 考慮過的替代方案

- **新增上限價欄，系統自己判斷**：結果全自動，但要多一個資料表欄位、起單建檔與回補表都沒有這欄，作業人員要另外填；只有 2 檔、只在跌破執行價時才需要，人工判斷一次比較省。檔數變多時再開 Issue。
- **上限價當成和 KO 價相同**：這一檔剛好相同，但說明書把它們分開寫，不能保證每一檔都一樣。

### 後果

- 兩種商品可以匯入、人工核對完成或回補、鎖住、比價，庫存總表包含它們。
- 到期跌破執行價的那一次要人工登記存續事件；其他情況（KO、到期還本）自動。
- 不需要新的資料表版本。

## English

### Context

The goal is to observe every ELN (ADR 0033). FCN_Star and FCN_OneStar were not supported product types, so the daily import skipped their rows and they were not observed. There is one of each today, both from CA. CA's "Safety FCN" term sheet (086001104869) says:

- KO is observed daily with memory, from the end of the first coupon period (inclusive) to the final pricing date (**exclusive**).
- There is no KI.
- At maturity the note is repaid if any underlying's final price is at or above the cap price (for example 105% of initial, here equal to the KO%). Without a cap event it settles like FCN: delivery when the worst performer is below the strike, repayment otherwise.

FCN_OneStar has no term sheet.

### Decision

Following the minimal approach in #538:

- `fcn_checker.products.PRODUCT_RULES` adds `FCN_Star` and `FCN_OneStar`. The final valuation date does not count for KO (as for Super Bull); everything else follows FCN. The daily import, the order-sheet import, manual maintenance, and the backfill therefore accept them. FCN_OneStar uses the same rules for now.
- No cap-price column is added. Maturity asks the product rule's `delivery_needs_review`:
  - Worst performer at or above the strike → 到期還本 (the result is the same with or without a cap event).
  - Worst performer below the strike (where delivery would follow) → observation goes to 待確認. The reason asks whether any underlying was at or above the cap on the final valuation date (repaid if so, delivered if not). Nothing is delivered automatically; the operator decides and records the lifecycle event.
- Automatic checking stays closed (not listed in `[auto_check_products]`). New trades use the manual check, and existing ones the non-FCN product backfill.

### Alternatives considered

- **Adding a cap-price column and deciding automatically**: fully automatic, but it needs a new column that neither the order sheet nor the backfill sheet has, so operators would fill it separately. With two trades, needed only when the worst performer is below the strike, one manual decision is cheaper. Open an Issue if volumes grow.
- **Treating the cap as equal to the KO price**: true for this trade, but the term sheet states them separately, so it cannot be assumed for every trade.

### Consequences

- Both types can be imported, manually checked or backfilled, locked, and observed, and the inventory export includes them.
- A maturity below the strike needs a manual lifecycle event; KO and repayment at maturity stay automatic.
- No new schema version is needed.
