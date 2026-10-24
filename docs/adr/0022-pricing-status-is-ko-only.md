# 0022: 比價狀態只講 KO，KI 另列一欄／Observation Status Covers KO Only, KI in Its Own Column

- 狀態／Status: Accepted
- 日期／Date: 2026-10-24
- 追蹤／Tracking: #196
- 取代／Supersedes: [ADR 0020](0020-close-prices-and-observation-rules.md) 的「MKI 比價狀態標待確認『每期觀察 KI 需人工追蹤』」與比價狀態待確認原因裡的 MKI；已比到的日期改成只算 KO 觀察／ADR 0020's "MKI is 待確認 '每期觀察 KI 需人工追蹤'" and MKI among the 待確認 reasons; the observed-through date now counts KO observation only

## 繁體中文

### 背景

「8 進度」的比價狀態、已比到、比價原因把 KO 和 KI 混在一起。每日觀察 KI（AKI）從 Strike date 隔天起每天比，所以在 Non-call 期間就有已比到的日期，同一批其他商品沒有，看起來不一致。對顧客與作業人員來說「比價」一般指 KO；KI 就算觸及，期末最差標的收盤價在執行價之上也沒關係，重要性較低。

每期觀察 KI（MKI）整檔標待確認，但 KO 照常在比，看起來像有問題。資料庫的比價日 1～12 是 KO 觀察日（P 型從 Non-Call 那期起、D 型只有兩格），不是 MKI 要看的每一期，所以照 ADR 0020 仍不比 MKI。

### 決策（2026-10-24 使用者確認）

- 比價狀態、已比到、比價原因只講 KO：已比到在第一個比價日之前、不觀察 KO（Non-Call 等於天期）的在庫商品都空白；出場的照舊是事件日。第一個比價日之前的在庫原因寫「Non-call period（第一個比價日 …）」（#194），不觀察 KO 的寫「還沒到最終比價日（…）」。
- KI 另寫一欄（`workflow.pricing_status.ki`，資料表版本 0022）：沒有 KI、到期觀察、未觸及、已觸及 日期 標的（第一次觸及）、每期觀察，需人工追蹤。出場後、核對條件後來不是完成時沿用上一次。
- 2026-10-24 補充（[#198](https://github.com/TaylorYam/structured-product-workflow/issues/198)，資料表版本 0023）：KI 欄改用英文代碼 No KI、EKI、AKI、MKI (manual)，觸及時 `AKI hit 日期 標的`（EKI 在最終比價日跌破寫 `EKI hit …`）；已出場、沒存過 KI 的寫 KI 種類。
- 每日觀察 KI 照樣每天比，缺它要的價格照樣等股價，原因加「（每日觀察 KI）」；KI 觸及照舊影響到期接股的判定。
- MKI 比價狀態照 KO 算（在庫、等股價、出場…），KI 欄寫「每期觀察，需人工追蹤」；最終比價日沒有 KO 時照舊待確認，要人工判定接股或到期還本。

### 考慮過的替代方案

- **維持混在一起，只改原因文字**：已比到在 Non-call 期間仍會因 AKI 前進，看起來還是不一致。
- **用比價日 1～12 比 MKI**：比價日只有 KO 觀察的期數，少了 Non-Call 期間的各期；D 型只有兩格。要比 MKI 得在核對寫回時存整張日期表（ADR 0020 的替代方案），等有 MKI 商品再做。
- **KI 存結構化的觸及日期與標的**：多兩欄、畫面還要組字；目前只給人看，存顯示的字和比價原因一致。

### 後果

- 進度頁與匯出多一欄 KI；已比到在 Non-call 期間空白，等股價時看原因知道缺的是 KI 要的價格。
- 升級後既有比價狀態的 KI 空白，下一次比價（回補股價後或「重新比價全部」）補上。
- MKI 商品不再整檔待確認，要靠 KI 欄的提醒人工追蹤。

## English

### Context

The 比價狀態, 已比到, and 比價原因 columns on "8 進度" mixed KO and KI. A daily-observed KI (AKI) is checked every day from the day after the strike date, so it showed an observed-through date during the non-call period while other products of the same batch did not. To clients and operators "observation" means KO; a touched KI does not matter if the worst underlying closes above the strike at maturity.

A periodic KI (MKI) made the whole product 待確認 although its KO is observed as usual. The stored autocall dates 1-12 are KO observation dates (P type from the non-call period on, D type only two cells), not every period MKI watches, so MKI is still not observed as in ADR 0020.

### Decision (confirmed by the user on 2026-10-24)

- 比價狀態, 已比到, and 比價原因 cover KO only: the observed-through date is blank before the first autocall date and for products that never observe KO (non-call equals tenor) while on book; exits keep their event date. Before the first autocall date the reason is "Non-call period（第一個比價日 …）" (#194); products that never observe KO show "還沒到最終比價日（…）".
- KI gets its own column (`workflow.pricing_status.ki`, schema version 0022): 沒有 KI, 到期觀察, 未觸及, 已觸及 date underlying (first touch), or 每期觀察，需人工追蹤. Exited products and products whose check terms are no longer 完成 keep the previous value.
- Amended on 2026-10-24 ([#198](https://github.com/TaylorYam/structured-product-workflow/issues/198), schema version 0023): the KI column uses English codes No KI, EKI, AKI, MKI (manual), and `AKI hit date underlying` when touched (`EKI hit …` when EKI breaks at the final valuation date); exited products without a stored KI show the KI type.
- A daily KI is still checked every day; a missing price it needs still means 等股價, with "（每日觀察 KI）" in the reason, and a touch still decides delivery at maturity.
- MKI follows KO for its status (在庫, 等股價, exits…) with "每期觀察，需人工追蹤" in the KI column; when the final valuation date has no KO it stays 待確認 for a person to decide delivery or redemption.

### Alternatives considered

- **Keep them mixed and only reword the reason**: the observed-through date would still move for AKI during the non-call period.
- **Observe MKI with autocall dates 1-12**: they hold only the KO periods, missing the non-call periods, and D type has two cells. Observing MKI needs the whole date table stored at check write-back (ADR 0020's alternative), deferred until an MKI product exists.
- **Store the touch date and underlying as structured columns**: two more columns and the screen would build the text; it is only displayed, so storing the text matches the reason column.

### Consequences

- The progress page and its export gain a KI column; 已比到 is blank during the non-call period, and a 等股價 reason tells when the missing price is one the daily KI needs.
- After the upgrade the KI column is blank until the next observation (after the price backfill or 重新比價全部).
- MKI products are no longer 待確認 as a whole and rely on the KI column reminder for manual follow-up.
