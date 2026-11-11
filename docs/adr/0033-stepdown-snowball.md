# 0033: Stepdown 與 Snowball：KO% 每期固定降幅／Stepdown and Snowball: a Fixed KO Step per Period

- 狀態／Status: Accepted
- 日期／Date: 2026-11-11
- 追蹤／Tracking: #537
- 補充／Completes: [ADR 0020](0020-close-prices-and-observation-rules.md)（比價規則）、[ADR 0032](0032-manual-check.md)（人工核對完成）／ADR 0020 (observation rules) and ADR 0032 (manual check)

## 繁體中文

### 背景

使用者的目標是所有 ELN 都能比價，每日 refresh 與匯出的庫存總表才會和公司庫存一致。FCN_Stepdown、Snowball、Snowball_Stepdown 不在支援的商品種類，每日匯入整列略過（只記在 `workflow.other_product`），沒有商品條件也不比價。讀過 17 份說明書（Barclays、Natixis、CA、UBS、BNP、SG）：出場判定和 FCN 相同（KO 每期或每日、記憶或非記憶、KI 只在期末或沒有 KI），差別只有 KO% 從第 1 期開始**每期固定往下降**，降幅 1、2、3、5、10 個百分點都有，沒有不等距的。Snowball 沒有固定配息、出場時領紅利，比價不看配息。

### 決策

2026-11-11 使用者決定：

- Range Accrual、Digital Note 不是 ELN，不做。
- 086001105524、086001105525 是 Snowball_Stepdown（交易明細寫 Snowball 是錯的）。
- 不寫這幾種商品的說明書 parser：自動核對不開放，新案走人工核對完成（ADR 0032），既有的走 FCN 以外商品回補。

做法：

- `fcn_checker.products.PRODUCT_RULES` 新增 `FCN_Stepdown`、`Snowball`、`Snowball_Stepdown`，其他規則照 FCN。每日匯入、起單資料匯入、人工維護、回補因此會收這三種。
- 新增商品條件欄「KO 降幅」（`KO Step Down (%)`，`workflow.product.ko_step_pct`，資料表版本 0038，不能是負數）。`ProductRule.has_ko_step` 決定有沒有這欄、`ko_step_required` 決定是不是必填：Stepdown 兩種必填，起單建檔沒有這欄，所以要在確認視窗或人工維護填，空白時核對條件待確認；Snowball 可以空白（空白等於不降）；其他商品要空白。
- `KO Barrier (%)` 與各標的 KO 價存**第 1 期**。第 n 期 KO% = KO% − 降幅 ×（n − 1），KO 價 = 進場價 × 第 n 期 KO% ÷ 100（同其他價格取到小數第 4 位）。
- 期別：第 n 期是比價日_{n−1} 的隔天到比價日_n（含），第 1 期從交易日開始，最後一個比價日以後算最後一期。每期型在比價日_n 用第 n 期；每日型在第 n 期每天都用第 n 期（Barclays「Autocall Observation Period」、UBS「配息觀察期間」的寫法）。記憶式照舊：標的在某天收盤 ≥ 當天的 KO 價就記住，跨期也記得。
- 比價前多一個檢查：有降幅但沒有 KO%，或最後一期的 KO% 降到 0 以下時待確認。還在庫存的比價說明寫下一天所在的期別與 KO%（例如「第 2 期 KO 97%」），方便對說明書。
- 參考條件表（v27）多一個選填欄「KO降幅(%)」：回補讀這欄（Stepdown 類沒有這欄或空白時那一檔不匯入並寫原因），核對的資料庫來源也帶出這欄。商品總表在「Lookback 期間」後面多一欄「KO降幅(%)」（沒有降幅寫 `-`）。

### 考慮過的替代方案

- **每期各存一個 KO%（例如 12 個欄位或另一張表）**：可以表達不等距的降幅，但 17 份說明書都是固定降幅，多存的欄位要作業人員一期一期填，容易錯；真的遇到不等距時再改。
- **KO Barrier (%) 存最後一期或當期的值**：舊的回補表 CA 早期幾檔存的是第 2 期；但第 1 期才和說明書的寫法、各標的 KO 價一致，也不會隨日期改變。
- **寫新的說明書 parser 讓自動核對開放**：工作量大，而且這幾種商品量少；先用人工核對完成，量變多時再開 Issue。

### 後果

- 三種商品可以匯入、人工核對完成或回補、鎖住、比價，庫存總表包含它們。
- 舊回補表 CA 早期幾檔的 KO% 是第 2 期的值，086001105525 的記憶式也和說明書不符，回補前要作業人員更正（清單在 #537）。
- 曝險表與商品總表的 KO 距離還是用第 1 期的 KO 價，不跟著期別往下降。
- 需要資料表版本 0038。

## English

### Context

The user wants every ELN observed so the daily refresh and the exported inventory match the company inventory. FCN_Stepdown, Snowball, and Snowball_Stepdown were not supported product types: the daily import skipped their rows (recording them only in `workflow.other_product`), so they had no product terms and were not observed. Seventeen term sheets (Barclays, Natixis, CA, UBS, BNP, SG) show the same exit rules as FCN (period-end or daily KO, memory or non-memory, KI only at maturity or none). The only difference is that the KO% **falls by a fixed step each period**, starting from period 1. Steps of 1, 2, 3, 5, and 10 points occur; none are uneven. A Snowball has no fixed coupon and pays a bonus on exit, and observation does not look at coupons.

### Decision

The user decided on 2026-11-11:

- Range Accrual and Digital Note are not ELNs and are out of scope.
- 086001105524 and 086001105525 are Snowball_Stepdown (the trade rows' Snowball is wrong).
- No term-sheet parsers for these types: automatic checking stays closed, new trades use the manual check (ADR 0032), and existing ones use the non-FCN product backfill.

Implementation:

- `fcn_checker.products.PRODUCT_RULES` adds `FCN_Stepdown`, `Snowball`, and `Snowball_Stepdown`, otherwise following FCN. The daily import, the order-sheet import, manual maintenance, and the backfill therefore accept them.
- A new product term, the KO step (`KO Step Down (%)`, `workflow.product.ko_step_pct`, schema version 0038, never negative). `ProductRule.has_ko_step` says whether a product has it and `ko_step_required` whether it is required:
  - Both Stepdown types require it. The order sheet has no such column, so it is filled in the confirm window or on manual maintenance, and the check terms stay 待確認 while it is blank.
  - Snowball may leave it blank (blank means no step).
  - Other products must leave it blank.
- `KO Barrier (%)` and each underlying's KO price hold **period 1**. Period n's KO% is KO% − step × (n − 1), and its KO price is the initial price × period n's KO% ÷ 100 (rounded to 4 decimals like other prices).
- Periods: period n runs from the day after autocall date n−1 through autocall date n inclusive. Period 1 starts at the trade date, and days after the last autocall date fall in the last period. Period-end types use period n on autocall date n. Daily types use period n on every day of period n, as Barclays' "Autocall Observation Period" and UBS's 配息觀察期間 put it. Memory works as before: an underlying is remembered once it closes at or above that day's KO price, across periods.
- One more check before observation: a step without a KO%, or a last-period KO% at or below 0, makes the product 待確認. The on-book observation note shows the period and KO% of the next day (for example 第 2 期 KO 97%), so it can be compared with the term sheet.
- The reference sheet (v27) gains an optional 「KO降幅(%)」 column. The backfill reads it: a Stepdown-type row without the column or with it blank is not imported, and the reason is written. The checker's database source carries the column too. The product master gains a 「KO降幅(%)」 column after 「Lookback 期間」 (`-` without a step).

### Alternatives considered

- **Storing one KO% per period (for example 12 columns or another table)**: this could express uneven steps, but all 17 term sheets use a fixed step. The extra columns would have to be filled period by period, which invites mistakes. Revisit if an uneven step appears.
- **Storing the last or the current period in KO Barrier (%)**: the old backfill sheet holds period 2 for some early CA trades. Period 1 matches how the term sheets and the per-underlying KO prices are written, and it does not change over time.
- **Writing new term-sheet parsers to open automatic checking**: this is a lot of work for few trades. Use the manual check for now and open an Issue if volumes grow.

### Consequences

- The three types can be imported, manually checked or backfilled, locked, and observed, and the inventory export includes them.
- Before the backfill, operators must correct the old sheet (the list is in #537). Some early CA trades there hold the period-2 KO%, and 086001105525's memory flag contradicts its term sheet.
- Distance to KO in the exposure report and the product master still uses the period-1 KO price; it does not step down with the period.
- Schema version 0038 is required.
