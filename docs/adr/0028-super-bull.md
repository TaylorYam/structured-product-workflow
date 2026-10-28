# 0028: Super Bull 沿用 FCN 的流程與欄位／Super Bull Reuses the FCN Workflow and Fields

- 狀態／Status: Accepted
- 日期／Date: 2026-10-28
- 追蹤／Tracking: #309、#310

## 繁體中文

### 背景

Super Bull 是 FCN 以外量較大的商品之一，發行上手以 BNP、CA 為主，HSBC 少量。每日匯入以前把它當成其他商品略過。

三家的 TS／IIS 樣本機制相同：沒有配息；每期觀察、非記憶式，所有標的在同一個比價日收盤價 ≥ KO 價就提前出場，付面額 ×（100% + 出場紅利），紅利是固定比例、一次付，不是年利率；沒有 KI；到期最差標的 < 執行價交股，≥ 執行價付面額 × 最終價 ÷ 執行價（參與漲幅）。比價日由 TS 表列、次數不規則（例：6 個月期只在第 3 個月比一次），KO 價可能高於執行價（CA）。說明書的出場表不含期末評價日，到期那天不付紅利。

起單建檔、交易明細的 Product 都寫 `Super Bull`，起單建檔沿用 FCN 那組欄：`Coupon p.a. (%)` 填出場紅利、`Guaranteed Periods (m)` 是第一個比價日的月數、`KO Type` 是 `Period End`、`Barrier Type` 是 `NONE`。舊總表照 FCN 的放法存比價日：觀察日放「比價日_{距發行日月數}」，「比價日_{天期}」放期末評價日。

### 決策（2026-10-28 使用者確認）

- **商品種類存 `Super Bull`**，加進 `fcn_checker.products.PRODUCT_RULES`；Non-Call 和 FCN 一樣至少 1。
- **出場紅利存 `coupon_pct`**，起單資料頁、寄信草稿、商品總表仍叫 `Coupon p.a. (%)`，和來源一致；比價不用這個數字。
- **比價日照舊總表放**，最後一格是最終比價日（比價本來就要求最後一個比價日等於最終比價日）。**比價依商品種類加一條規則**：`ProductRule.final_date_callable` 為 False 的商品（Super Bull），最終比價日即使也在比價日裡，也不算 KO，只做到期判定。比價日只有最終比價日的不會提前出場。
- **到期 ≥ 執行價沿用「到期還本」事件**：系統不算金額，事件只表示沒有交股、領現金。
- **寄信草稿、商品總表、風險等級**和 FCN 相同，主旨與 Product 欄帶出 `Super Bull`。
- **自動核對**：`[auto_check_products]` 登記 `"Super Bull" = []`，先不開放任何上手，照不支援樣態轉人工；之後依 BNP → CA → HSBC 開放（#311、#312、#314），樣態限制（只支援 `Period End`＋`NONE`、2026-06 以後的範本版本）在開放時一起做。
- **既有商品**由「匯入既有的 FCN 以外商品」（#303）回補，Non-Call 與比價日照舊表、不轉換。

### 考慮過的替代方案

- **比價日不放期末評價日**：比價程式不用加規則，但違反「最後一個比價日等於最終比價日」的既有檢查，舊總表回補要清掉一格，商品總表和作業人員手上的表也對不起來。
- **出場紅利新開一欄或改名「出場紅利 (%)」**：起單建檔、交易明細、舊總表都填在 Coupon 欄，改名反而對不起來；比價不用這個數字。
- **新增存續事件「到期現金結算」**：系統不算金額，新事件會牽動狀態篩選、商品總表與分析分頁。

### 後果

- 每日匯入、起單資料匯入、人工維護收 Super Bull；`Guaranteed Periods (m)` 0 擋匯入。
- 比價讀資料庫的商品種類套 `final_date_callable`；FCN、FCN_Express 照舊（最終比價日也可以 KO）。
- Super Bull 的 TS 在各上手開放前不能自動核對，比價日由回補或人工維護提供。

## English

### Context

Super Bull is one of the larger products besides FCN, issued mainly by BNP and CA, with a few from HSBC. The daily import used to skip it as another product.

TS/IIS samples from the three issuers share one mechanism: no coupon; periodic, non-memory observation, exiting early when every underlying closes at or above its KO price on the same observation date and paying notional × (100% + exit bonus), a fixed one-off percentage rather than an annual rate; no KI; at maturity, delivery if the worst performer ends below strike, otherwise notional × final ÷ strike (participation). Observation dates come from the TS table at irregular intervals (for example, a 6-month note observed only in month 3), and the KO price may sit above the strike (CA). The term sheet's exit table excludes the final valuation date, and no bonus is paid at maturity.

The order sheet and the trade sheet write Product as `Super Bull`, and the order sheet reuses the FCN columns: `Coupon p.a. (%)` holds the exit bonus, `Guaranteed Periods (m)` is the month of the first observation date, `KO Type` is `Period End`, and `Barrier Type` is `NONE`. The old master sheet stores observation dates the FCN way: each observation date in 比價日_{months from issue}, and the final valuation date in 比價日_{tenor}.

### Decision (confirmed by the user 2026-10-28)

- **Store the product type as `Super Bull`** in `fcn_checker.products.PRODUCT_RULES`; Non-Call is at least 1, as for FCN.
- **Store the exit bonus in `coupon_pct`**, still labelled `Coupon p.a. (%)` in the order tab, mail drafts, and the product master to match the sources; observation does not use it.
- **Store observation dates as the old master sheet does**, with the final valuation date in the last slot (observation already requires the last observation date to equal the final valuation date). **Observation adds one rule per product type**: for a product whose `ProductRule.final_date_callable` is False (Super Bull), the final valuation date never counts as KO even when it is stored as an observation date; it only decides maturity. A product whose only observation date is the final one never exits early.
- **Keep the 到期還本 event when the worst performer ends at or above strike**: amounts are not computed, so the event only says the note settled in cash rather than delivering shares.
- **Mail drafts, the product master, and the risk level** work as for FCN, with `Super Bull` in the subject and the Product column.
- **Automatic checking**: `[auto_check_products]` registers `"Super Bull" = []`, so no issuer is opened yet and checks go to manual review as an unsupported variant; BNP, CA, and HSBC open in that order (#311, #312, #314), together with the variant limits (only `Period End` + `NONE`, and template versions from June 2026 on).
- **Existing products** are backfilled with "匯入既有的 FCN 以外商品" (#303), keeping the old sheet's Non-Call and observation dates unchanged.

### Alternatives considered

- **Leave the final valuation date out of the observation dates**: observation would need no new rule, but it breaks the existing check that the last observation date equals the final valuation date, the backfill would have to clear a slot, and the product master would no longer match the operators' sheets.
- **A new column or an "exit bonus (%)" label**: the order sheet, the trade sheet, and the old master all use the Coupon column, so a new name would not match; observation does not use the value.
- **A new lifecycle event for cash settlement at maturity**: amounts are not computed, and a new event would ripple into status filters, the product master, and the analysis sheets.

### Consequences

- The daily import, the order import, and manual maintenance accept Super Bull; `Guaranteed Periods (m)` of 0 blocks the import.
- Observation reads the product type from the database and applies `final_date_callable`; FCN and FCN_Express are unchanged (the final valuation date can still be a KO day).
- Until an issuer is opened, Super Bull term sheets are not checked automatically, and observation dates come from the backfill or manual maintenance.
