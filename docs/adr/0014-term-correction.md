# 0014: 鎖住後更正商品條件／Correct Product Terms After Locking

- 狀態／Status: Accepted；入口改到「7 人工維護」分頁，由 [ADR 0017](0017-manual-maintenance-tab.md) 取代，更正的規則照舊／Accepted; the entry point moved to the "7 人工維護" tab, superseded by ADR 0017; the correction rules still stand
- 日期／Date: 2026-10-21
- 追蹤／Tracking: #55
- 補充／Completes: [ADR 0006](0006-new-product-data-from-order.md)「重複匯入」的「更正商品條件」、[ADR 0013](0013-check-from-database.md) 的鎖住／the "correct product terms" flow named by ADR 0006's re-import rule and the locking of ADR 0013

## 繁體中文

### 背景

ADR 0006 決定：TS 核對通過前，重新匯入起單資料就覆蓋；通過後條件鎖住，要改走「更正商品條件」。ADR 0013 實作了鎖住：核對通過並回填後設定 `locked_at`，之後起單資料匯入與核對條件重算都不碰這檔；migration 0006 時已在資料庫的舊商品也視同鎖住。鎖住之後如果發現條件錯了，需要一個明確、留紀錄的修改方式。

### 決策（2026-10-21 使用者確認）

- **入口**：「2 起單資料」的「更正商品條件…」按鈕，輸入 TDCC（結果表選中的那檔已鎖住時先帶入），從資料庫帶出目前的條件，在另一個視窗用同一套編輯欄位修改；不需要起單建檔裡有這檔。只接受已鎖住的商品，還沒鎖住的照舊用「匯入資料庫」。
- **欄位**：起單資料的欄位（Order 條件），Issue date 除外（TS 回填）；ISIN、比價日不在範圍。上手不能改（上手編號對照或發行機構寫法後來改了也照樣可以更正）；單位面額不直接改，只有幣別改了時換成新幣別的預設值。照起單資料匯入的規則逐欄檢查。清空或補上的欄也標黃，可以改回。
- **價格**：算出來的價格不能手改。有核對條件（ADR 0012）的商品，改到影響核對條件的欄（標的、比例、Strike date、期初定價、幣別、交易日、UF 等）時重算這一檔的價格與交叉比對（鎖住也算）；只改 Coupon、到期日這類欄位時不重算，避免股價檔或假日表之後變了把原本的價格算掉。以前清單更新匯入、沒有核對條件的商品只改條件。VWAP 的價格是 TS 回填的：更正前也是 VWAP、同一位置同一標的的保留，執行價(%)、KI(%)、KO(%) 或 Barrier Type 改了的價格由保留的進場價重算；其他位置的價格空白，核對條件標待確認並寫出原因。
- **紀錄**：每次更正在 `workflow.term_correction` 留一筆：改了哪些欄位與價格（更正前、更正後）、更正前的商品欄位、Windows 使用者名稱、時間、原因（選填）。沒有改任何欄位時不寫入。
- **之後**：商品維持鎖住（重新匯入起單資料照樣不覆蓋），已有的核對結果不動。打開之後資料庫的條件被改過、或已經不是鎖住的，不寫入，請重新載入。

### 考慮過的替代方案

- **只能在起單資料結果表「已鎖住」那一列更正**：不用另外輸入，但以前的單通常不在今天的起單建檔裡。
- **更正後解鎖、要求重新核對**：核對結果和條件一定一致，但已寄出的文件也要重跑流程；使用者選擇維持鎖住。
- **價格也可以手改**：彈性大，但價格是由條件與股價算出的，手改容易和條件不一致。

### 後果

- 新增 `workflow.term_correction`（資料表版本 0011），更新程式後請執行一次 `spw-db-migrate`。
- 更正後的核對條件可能變成待確認（例如 UF 改了和交易明細不同）；已鎖住的商品不能放行差異，狀態只是提示。
- 核對結果不會因更正失效；需要重新核對時，作業人員自己再核對一次（已鎖住的商品可以再核對，但不再回填）。

## English

### Context

ADR 0006 decided that re-importing order terms overwrites them until the TS check passes; afterward the terms lock and changes go through "correct product terms". ADR 0013 implemented locking: `locked_at` is set after a passing check and backfill, after which order-sheet imports and check-term recalculation leave the product alone; old products already in the database at migration 0006 count as locked. A wrong term found after locking needs an explicit, recorded way to change it.

### Decision (confirmed by the user 2026-10-21)

- **Entry**: a "更正商品條件…" button on "2 起單資料" asks for a TDCC (pre-filled with the selected product when it is locked), loads the current terms from the database, and edits them in a separate window with the same fields; the product need not be in the order sheet. Only locked products are accepted; unlocked ones still use "匯入資料庫".
- **Fields**: the order-term fields except Issue date (backfilled from the TS); ISIN and observation dates are out of scope. The issuer cannot change (a correction still works when the issuer prefix map or spelling has changed since); the denomination is not edited directly and changes to the new currency's default only when the currency changes. Fields are checked with the order-import rules. Cleared or newly filled fields are also marked yellow and can be restored.
- **Prices**: calculated prices cannot be edited. A product with check terms (ADR 0012) has its prices and cross-check recalculated, even though it is locked, when a field affecting the check terms changes (underlyings, percentages, Strike date, initial pricing, currency, trade date, UF, and so on); changing only fields such as the coupon or maturity date does not recalculate, so later changes to price files or the holiday calendar cannot wipe the existing prices. An old 清單更新 product without check terms only has its terms changed. VWAP prices come from the TS: when the product was already VWAP, prices at the same position with the same underlying are kept, and the prices whose strike %, KI %, KO %, or barrier type changed are recalculated from the kept initial price; other positions are blank and the check terms become 待確認 with the reason.
- **Record**: each correction writes one `workflow.term_correction` row: changed fields and prices (before and after), the previous product columns, the Windows user name, the time, and an optional reason. Nothing is written when nothing changed.
- **Afterward**: the product stays locked (order-sheet imports still do not overwrite it) and existing check results are untouched. If the terms changed in the database after opening, or the product is no longer locked, nothing is written and the operator reloads.

### Alternatives considered

- **Correct only from a 已鎖住 row in the order-sheet results**: no TDCC to type, but older products are usually not in today's order sheet.
- **Unlock after correcting and require a recheck**: results always match the terms, but documents already sent would go through the flow again; the user chose to keep the lock.
- **Allow editing prices directly**: more flexible, but prices derive from terms and quotes, and hand edits drift from the terms.

### Consequences

- New table `workflow.term_correction` (schema version 0011); run `spw-db-migrate` once after updating.
- Corrected check terms may become 待確認 (for example a UF that now differs from the trade rows); locked products cannot release differences, so the status is informational.
- Corrections do not invalidate check results; when a recheck is needed the operator runs it (locked products can be checked again but are not backfilled again).
