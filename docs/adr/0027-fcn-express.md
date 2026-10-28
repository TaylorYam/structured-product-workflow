# 0027: FCN_Express 和 FCN 走同一套流程／FCN_Express Follows the FCN Workflow

- 狀態／Status: Accepted
- 日期／Date: 2026-10-28
- 追蹤／Tracking: #301、#302、#303

## 繁體中文

### 背景

FCN 以外量最大、成長最快的是 FCN_Express：2026-04～10 共 113 檔（Barclays 103、CA 6、HSBC 4），8、9 月各 30、40 檔。每日匯入以前把它當成其他商品略過，交易明細列、商品條件、比價、寄信都沒有。

14 份 TS／IIS 樣本（Barclays 10、CA 2、HSBC 2）顯示三家機制相同：每月固定配息；從發行日後下一個一籃子交易日起每日觀察 KO（幾乎都是記憶式），沒有 Non-Call 期間；出場在事件日後 2～3 個營業日贖回，付 100% 本金加當期按天數攤的配息（第一期內出場付整期）；到期表現最差的標的低於執行價就交股，KI 可有可無。條件欄位和 FCN 完全相同；起單建檔是同一份、同一組欄，`Product` 寫 `FCN_Express`、`Guaranteed Periods (m)` 寫 0。舊總表把 Non-Call 寫成 1、比價日_1 放第 1 期期始日。

### 決策（2026-10-28 使用者確認）

- **商品種類存 `FCN_Express`**，和交易明細、起單建檔的 Product 一字不差（比對不分大小寫）。商品種類只看 Product 欄，不從條件（例如 G = 0）推；以後其他商品也可能 G = 0。
- **支援的商品種類與規則放在 `fcn_checker.products.PRODUCT_RULES`**（原在 `product_record`，#304 搬到核對端共用）：FCN 的 Guaranteed Periods (m) 至少 1；FCN_Express 沒有 Non-Call，一定是 0。新增商品時加一筆、訂它自己的規則。每日匯入、起單資料匯入、人工維護都照這份；核對也照它檢查說明書推得的 Non-Call 符不符合參考條件表的商品種類，不符合待確認（#304）。
- **只有交易明細的 TDCC**：人工維護打開時，商品種類是交易明細的 Product，不固定 FCN。
- **比價不分商品種類**：D 型從資料庫最早的比價日起每天觀察；沒有 Non-Call 的商品，比價日_1 存第一個觀察日（TS 第 1 期期始日）。只記事件日，不推算贖回日、不算出場配息金額，和 FCN 一樣。
- **寄信草稿、商品總表**和 FCN 相同，主旨與 Product 欄帶出 `FCN_Express`，Guaranteed Periods 寫 0。
- **自動核對依「上手＋商品種類」開放**：上手編號對照設定檔新增 `[auto_check_products]` 白名單（商品種類 → 上手，`"*"` 是全部），參考條件表新增選填的 Product 欄。沒開放的組合照不支援樣態處理：不跑規則、不能人工放行，請人工核對。Product 空白或表上沒有這欄時照 FCN 核對。第一版只開放 FCN；FCN_Express 依 Barclays → CA、HSBC 的順序開放（#304～#306）。

### 考慮過的替代方案

- **Non-Call 存 1，沿用舊總表**：匯入時要把起單建檔的 0 改成 1，寄信也會寫 1，核對時要解釋「1 其實代表 0」。
- **從條件推商品種類（G = 0 就是 Express）**：以後其他商品也可能 G = 0，會被誤認。
- **商品種類另存「FCN＋樣態 Express」兩欄**：和交易明細、起單建檔、作業人員的分析表寫法不同；之後的 FCN_Stepdown、BEN 也難以同樣處理。
- **核對分流只放在資料庫來源**：Excel 參考條件表來源也會遇到 Express，放在辨識階段兩邊都擋得到。

### 後果

- 每日匯入不再把 FCN_Express 記到 `workflow.other_product`；以前記過的由「匯入既有的 FCN 以外商品」（#303）回補：從舊總表格式的 Excel 建立鎖住的商品條件（來源「歷史回補」，Non-Call 轉成 0、期初定價日存交易日）、價格、比價日與分公司配置，並從 `other_product` 移除；交易明細列（含 FC）由「只匯交易明細」重匯交易清單補上，不從舊表寫。
- 在 Barclays 開放之前，Express 的 TS 不能自動核對，比價日也不會由核對回填；要等 #303 回補或 #304。#304 已開放 Barclays：G = 0 的 Non-Call 為 0、比價日_1 回填第 1 期期始日、最後一格回填最終評價日。
- 參考條件表格式 v20、上手編號對照 v11；舊的 Excel 參考條件表沒有 Product 欄照常核對。
- 核對後商品種類改了，也算條件改了（不回填、不鎖住）；核對時的表沒有 Product 欄不算改了。

## English

### Context

FCN_Express is the largest and fastest-growing product besides FCN: 113 trades from April to October 2026 (Barclays 103, CA 6, HSBC 4), with 30 and 40 in August and September. The daily import skipped it as another product, so it had no trade rows, product terms, observation, or mail drafts.

Fourteen TS/IIS samples (Barclays 10, CA 2, HSBC 2) show the same mechanism at all three issuers: a fixed monthly coupon; daily KO observation (nearly always memory) from the first basket trading day after the issue date, with no non-call period; on exit, redemption 2-3 business days after the event at 100% plus the current period's coupon accrued by days (a full coupon within the first period); at maturity, delivery if the worst performer ends below strike, with or without KI. The terms use exactly the FCN fields; the daily order sheet is the same file with the same columns, with `Product` = `FCN_Express` and `Guaranteed Periods (m)` = 0. The old master sheet wrote Non-Call 1 and put the first period's start date in 比價日_1.

### Decision (confirmed by the user 2026-10-28)

- **Store the product type as `FCN_Express`**, exactly as the trade sheet and the order sheet write Product (matched case-insensitively). The product type comes only from Product, never from the terms (such as G = 0); other products may also have G = 0.
- **Supported product types and their rules live in `fcn_checker.products.PRODUCT_RULES`** (moved from `product_record` in #304 so the checker shares it): FCN requires Guaranteed Periods (m) of at least 1; FCN_Express has no non-call, so it must be 0. A new product adds an entry with its own rule. The daily import, the order import, and manual maintenance all use it; the checker also uses it to check that the Non-Call derived from the term sheet fits the reference sheet's product type, and flags a mismatch for confirmation (#304).
- **A trade-only TDCC** opened in manual maintenance takes its product type from the trade rows' Product instead of always FCN.
- **Observation ignores the product type**: type D observes daily from the earliest stored observation date; for a product without non-call, 比價日_1 holds the first observation day (the TS first period's start). Only the event date is recorded; redemption dates and exit coupons are not computed, as for FCN.
- **Mail drafts and the product master export** work as for FCN, with `FCN_Express` in the subject and the Product column and 0 for Guaranteed Periods.
- **Automatic checking is opened per issuer and product type**: the issuer prefix config gains an `[auto_check_products]` allowlist (product type → issuers, `"*"` for all), and the reference sheet gains an optional Product column. A combination not opened is handled like an unsupported variant: no rules run and no manual release; check it by hand. A blank or absent Product is checked as FCN. Only FCN is opened at first; FCN_Express opens for Barclays, then CA and HSBC (#304-#306).

### Alternatives considered

- **Store Non-Call 1 as the old master sheet did**: the order import would turn the order sheet's 0 into 1, mail drafts would show 1, and checks would have to explain that 1 means 0.
- **Infer the product type from the terms (G = 0 means Express)**: other products may also have G = 0 and would be misidentified.
- **Store "FCN plus variant Express" in two columns**: differs from the trade sheet, the order sheet, and the operators' analysis sheets, and does not extend to FCN_Stepdown or BEN the same way.
- **Gate automatic checks only in the database source**: Excel reference sheets can also contain Express; gating at identification covers both.

### Consequences

- The daily import no longer records FCN_Express in `workflow.other_product`; the ones recorded earlier are backfilled by "匯入既有的 FCN 以外商品" (#303), which creates locked product terms from the old master-sheet format (source 歷史回補, Non-Call turned into 0, the trade date stored as the strike date) with prices, autocall dates, and branch allocations, and removes them from `other_product`; trade rows (with the FC) come from re-importing the trade list with "只匯交易明細", not from the old sheet.
- Until Barclays is opened, Express term sheets are not checked automatically and observation dates are not backfilled by the checker; they come from #303 or #304. #304 opened Barclays: with G = 0 the Non-Call is 0, 比價日_1 is backfilled with the first period's start date, and the last slot with the final valuation date.
- Reference sheet format v20 and issuer prefix map v11; old Excel reference sheets without a Product column are checked as before.
- A product type changed after checking counts as changed terms (no backfill or lock); a checked sheet without a Product column does not count as changed.
