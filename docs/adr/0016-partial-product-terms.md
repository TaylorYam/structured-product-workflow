# 0016: 商品條件可以部分空白／Partially Blank Product Terms

- 狀態／Status: Accepted
- 日期／Date: 2026-10-22
- 追蹤／Tracking: #114
- 補充／Completes: [ADR 0012](0012-check-terms-calculation.md) 的核對條件狀態、[ADR 0013](0013-check-from-database.md) 的「核對條件還不是完成就不核對」／the check-term status of ADR 0012 and the "not checked unless the check terms are done" rule of ADR 0013

## 繁體中文

### 背景

`workflow.product` 的必填欄（標的、Strike、KO、Coupon、Tenor、交易日、最終比價日、到期日等）原本都是 `NOT NULL`，商品條件只能整檔齊全才寫得進去。起單資料缺欄時，使用者希望先在人工維護（#115）補一部分、之後再補。資料庫要能存部分空白的商品，而且缺資料的商品不能被核對放行。

### 決策

- **哪些欄可以空白**：起單資料匯入的必填欄（`order_terms.REQUIRED`）對應的欄（商品種類除外），加上由 KO Type、Barrier Type 換算的代碼欄（migration 0012）。TDCC、商品種類、上手、狀態照舊不可空白。Strike date、期初定價本來就可以空白。
- **缺資料怎麼判斷**：`order_terms.missing_fields` 以 `REQUIRED` 為準，回傳缺的欄名（畫面上的名稱）；KO Type、Barrier Type 的換算代碼空白也算那一欄缺（沒有存齊，參考條件表會少欄）。讀資料庫時用 `with_required` 把要看的欄加進查詢；核對條件、核對與寄信共用，#115 的「檢查」也照這個。`REQUIRED` 與欄位對照從 `order_import` 搬到 `order_terms`，因為 `check_terms` 不能 import `order_import`（會循環）。（#126 之後：`REQUIRED`、`missing_fields` 與欄位對照在 `product_record`，讀資料庫改成讀全部 `PRODUCT_COLUMNS`，`with_required` 已拿掉。）
- **核對條件**：商品條件缺欄時狀態是「待確認」，原因是「缺 Strike (%)、Maturity date」這種寫法（依 `REQUIRED` 的順序，交易明細還沒有時接著寫）。不算價格、不交叉比對，所以沒有可以放行的差異；分公司配置只看交易明細，照樣算。補齊後下一次重算照常。
- **核對**：資料庫來源產生參考條件表前直接檢查商品條件，缺欄就不產生列（原因「商品條件缺 …；補齊後再核對」），不看核對條件：存檔之後可能還沒重算。寫回前同樣檢查，缺欄不回填、不鎖住。有核對條件、已鎖住的商品不會缺欄（鎖住前核對條件一定是完成，更正照匯入規則擋空白）；以前清單更新匯入、已鎖住的商品可能沒有期初定價，照舊先判斷成「請用 Excel」。
- **寄信草稿**：缺信裡要用的必填欄時標「待處理」並寫出缺的欄，不再因為空白中斷變成「建立失敗」。信裡沒有 Strike date；期初價格空白時信裡空白，照舊可以寄（以前清單更新匯入的商品可能沒有期初定價）。
- **起單資料匯入規則不變**：缺必填欄的那一檔照舊擋下；資料庫空白的欄只是不帶入、不比對，重新匯入照常覆蓋。

### 考慮過的替代方案

- **缺欄時照樣算得出來的部分**（例如有 Strike date 與期初定價就先算進場價、有幣別就先比幣別）：作業人員可以早一點看到價格與差異，但每個計算都要處理空白，而且狀態本來就是待確認；補齊後重算就有。
- **只看核對條件的狀態，不在核對前另外檢查商品條件**：核對條件在每次存檔後重算，平常會一致，但重算失敗或還沒重算時，舊的「完成」會讓缺欄的商品產生列。
- **寄信草稿也擋期初價格空白**：和核對的規則一致，但以前清單更新匯入的商品會突然不能寄。

### 後果

- 更新程式後要跑 `spw-db-migrate`（資料表版本 0012）。已有的資料不變。
- 必填欄齊全的商品行為不變。以前清單更新建立、還沒鎖住、期初定價空白的商品，原因從「期初定價「空白」…」變成「缺 期初價格」，狀態一樣是待確認。
- 讀 `workflow.product` 的新程式要能處理必填欄空白，或先用 `missing_fields` 擋下。
- 位置（#203）：`REQUIRED` 與 `missing_fields` 現在在 `sp_workflow.product_record`（#126）；人工維護標紅不擋的空白必填欄由 `product_terms.problems` 給。

## English

### Context

The required columns of `workflow.product` (underlyings, strike, KO, coupon, tenor, trade, final valuation, and maturity dates, and so on) were all `NOT NULL`, so product terms could be written only when complete. When order terms are incomplete, the user wants to fill part of them in manual maintenance (#115) and finish later. The database must store partially blank products, and products with missing data must never be checked and released.

### Decision

- **Which columns may be blank**: the columns behind the order-import required fields (`order_terms.REQUIRED`, except the product type) plus the codes derived from KO Type and Barrier Type (migration 0012). TDCC, product type, issuer, and status stay `NOT NULL`. Strike date and initial pricing were already nullable.
- **What counts as missing**: `order_terms.missing_fields` checks `REQUIRED` and returns the blank field names (as shown on screen); a blank code derived from KO Type or Barrier Type also counts as that field missing (not fully stored, and the reference workbook would lack a column). Database reads add the needed columns with `with_required`; check terms, checking, and mail drafts share it, and #115's 檢查 follows it too. `REQUIRED` and the column map moved from `order_import` to `order_terms` because `check_terms` cannot import `order_import` (circular import). (Since #126: `REQUIRED`, `missing_fields`, and the column map live in `product_record`; database reads select all of `PRODUCT_COLUMNS`, and `with_required` is gone.)
- **Check terms**: a product with blank required fields is 待確認 with a reason such as "缺 Strike (%)、Maturity date" (in `REQUIRED` order, followed by the missing-trade-rows note when there are no trade rows). Prices and the cross-check are skipped, so there are no differences to release; branch allocation depends only on trade rows and is still derived. Once filled, the next recalculation proceeds as usual.
- **Checking**: the database source checks the product terms directly before generating the reference workbook and produces no row when fields are blank (reason "商品條件缺 …；補齊後再核對"), regardless of the check-term status, which may not have been recalculated since a save. Write-back checks again and neither backfills nor locks such a product. Locked products with check terms cannot have blank fields (locking requires 完成 check terms, and corrections block blanks with the import rules); old locked 清單更新 products may lack an initial pricing and are still sent to Excel first.
- **Mail drafts**: a product missing a required field used in the mail is 待處理 with the fields named, instead of failing with 建立失敗 on the blank value. The mail has no Strike date; a blank initial pricing stays blank in the mail and can still be sent (old 清單更新 products may lack it).
- **Order-sheet import rules are unchanged**: a product with blank required fields is still blocked; blank database fields are simply neither filled in nor compared, and re-imports overwrite as before.

### Alternatives considered

- **Calculating whatever is possible despite blanks** (for example initial prices when the Strike date and pricing are present, or the currency check when the currency is present): operators would see prices and differences earlier, but every calculation would have to handle blanks, and the status is 待確認 anyway; recalculation after filling provides them.
- **Relying only on the check-term status before checking**: check terms are recalculated after each save and normally agree, but when a recalculation failed or has not run, a stale 完成 would generate a row for a product with blanks.
- **Blocking mail drafts on a blank initial pricing too**: consistent with checking, but old 清單更新 products would suddenly become unsendable.

### Consequences

- Run `spw-db-migrate` after updating (schema version 0012). Existing data is unchanged.
- Products with all required fields behave as before. Unlocked 清單更新 products with a blank initial pricing change their reason from "期初定價「空白」…" to "缺 期初價格"; their status stays 待確認.
- New code reading `workflow.product` must handle blank required fields or block them first with `missing_fields`.
- Location (#203): `REQUIRED` and `missing_fields` now live in `sp_workflow.product_record` (#126); `product_terms.problems` marks the blank required fields that maintenance does not block.
