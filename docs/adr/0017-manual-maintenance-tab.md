# 0017: 人工維護分頁／Manual Maintenance Tab

- 狀態／Status: Accepted
- 日期／Date: 2026-10-22
- 追蹤／Tracking: #115
- 部分取代／Partly supersedes: [ADR 0014](0014-term-correction.md) 的入口（「2 起單資料」的「更正商品條件…」）；更正的規則照舊／the entry point of ADR 0014 (the "更正商品條件…" button on "2 起單資料"); the correction rules still stand
- 補充／Completes: [ADR 0016](0016-partial-product-terms.md) 留給人工維護的「先存一部分、之後再補」／the "save part now, finish later" left to manual maintenance by ADR 0016

## 繁體中文

### 背景

ADR 0016 讓資料庫的商品條件可以部分空白，缺欄的商品停在待確認、不能核對。但作業人員還沒有地方找出哪些商品缺資料、也沒有地方補：起單資料匯入照舊擋下缺必填欄的那一檔，鎖住後的更正（ADR 0014）只接受已鎖住的商品、而且不能留空白。交易明細已經有、起單建檔還沒有的新商品，也只能等起單建檔。

### 決策

- **入口**：視窗新增「7 人工維護」分頁，排在步驟欄最後（步驟標題寫 N／7）。「2 起單資料」的「更正商品條件…」拿掉，鎖住後的更正也改在這裡做（取代 ADR 0014 的入口）。
- **檢查**：按「檢查」列出兩種商品，依 TDCC 排：還沒鎖住、缺起單資料必填欄的商品（照 ADR 0016 的 `missing_fields`，和核對條件、核對、寄信同一個判斷）；交易明細有、資料庫還沒有商品條件的 TDCC（必填欄全部算缺）。已鎖住的不列（鎖住前核對條件一定完成，不會缺欄）。結果表一格一格畫，缺的格子淺紅底，可以左右捲動，TDCC 欄固定在左邊；`ttk.Treeview` 只能整列上色，所以用 Canvas 自己畫。
- **打開**：點檢查結果的一檔，或輸入任何一檔 TDCC 按「打開」（包括已鎖住、沒缺資料的）。資料庫有商品條件的帶出目前的值；只有交易明細的從空白開始，商品種類 FCN（交易明細只收 FCN），上手依上手編號對照。兩邊都沒有的不能打開：新商品一定先出現在交易明細或起單建檔，避免打錯 TDCC 建出不存在的商品。
- **編輯**：和「2 起單資料」同一套編輯欄位與格式檢查。
  - 已鎖住：照 ADR 0014 更正的規則（不能留空白、Issue date 不能改、商品維持鎖住、核對結果不動、影響核對條件的欄改了才重算價格）。
  - 還沒鎖住、只有交易明細：格式錯、欄位之間對不上（例如 Barrier Type 是 NONE 時 KI(%) 要空白、Strike date 不能早於 Trade date）照匯入的規則擋存檔；必填欄可以先空著（淺紅底，寫「缺，可以先存、之後再補」），商品種類除外（資料庫不可空白）。KI(%) 不在必填欄，照匯入的規則有 KI 時必填：還不確定就把 Barrier Type 也先空著，這檔就照樣留在檢查清單上。還沒回填的 Issue date 照起單資料一樣可以改。
- **上手與單位面額**：資料庫已有的商品上手不改（同更正）；新建的依 TDCC 前三碼查上手編號對照，查不到時擋存檔。單位面額不直接改：新建時依幣別預設值（幣別空白就空白），已有的只有幣別改了才換成新幣別的預設值（同更正）。
- **存檔**：還沒鎖住的商品，在同一個 transaction 裡鎖住那一列、確認打開之後資料庫沒變、寫入商品條件、重算這一檔的核對條件（ADR 0012）、留紀錄；任何一步失敗都不留下一半。存檔後畫面換成資料庫存的值（百分比存成小數 6 位），同一檔可以接著改、再存。新建的商品來源是「人工維護」（migration 0013 放寬來源的限制：人工維護沒有匯入批次），狀態在庫。之後重新匯入起單資料時，和其他還沒鎖住的商品一樣整檔覆蓋，紀錄寫「人工維護建立的商品改由起單資料匯入接手」。
- **紀錄**：每次存檔都在 `workflow.term_correction` 留一筆，和更正共用一張表：改了哪些欄（存檔前、存檔後；新建時包括上手與單位面額）、存檔前的商品欄位（新建時空白，所以 `previous` 改成可以空白）、Windows 使用者名稱、時間、原因（選填）。新增 `locked` 欄分辨：true 是已鎖住的更正（migration 前的紀錄都是），false 是還沒鎖住的人工維護。沒有改任何欄時不寫入。
- **同時有人改**：打開之後資料庫的這檔改過、被鎖住（核對通過）、被刪掉，或只有交易明細的那檔已經有人建了商品條件（包括兩個人同時存檔）、交易明細沒了，都不寫入，請重新載入。

### 考慮過的替代方案

- **補充 ADR 0014、繼續用「2 起單資料」的對話框**：改動小，但更正和補資料是同一件事（改資料庫的商品條件），分兩個地方要記兩套入口；而且起單資料頁是每天匯入用的，補資料常常是事後做。
- **人工維護另開一張紀錄表**：還沒鎖住和已鎖住分開放，但「誰在什麼時候改了這檔什麼」要查兩張表；一張表加 `locked` 欄比較好查。
- **允許新增交易明細沒有的 TDCC**：可以先建條件，但打錯 TDCC 會留下不存在的商品，而且之後的分公司配置、核對都靠交易明細。
- **重算核對條件失敗時照樣存檔**（像匯入那樣保留資料、在完成訊息寫原因）：不會被重算擋住，但會留下和條件對不上的核對條件；人工維護一次只存一檔，失敗時直接不寫、寫出原因，排除後再存。
- **有 KI 時 KI(%) 也可以先空著、另外列進檢查清單**：比較有彈性，但「缺資料」就有兩種定義（ADR 0016 只看 `REQUIRED`），寄信草稿、核對也要另外處理空白的 KI(%)；把 Barrier Type 一起空著，同一套判斷就找得到。

### 後果

- 新增 migration 0013（資料表版本 0013），更新程式後請執行一次 `spw-db-migrate`。以前的更正紀錄 `locked` 是 true，內容不變。
- 讀 `workflow.term_correction` 的程式要處理 `previous` 空白，並用 `locked` 分辨更正和人工維護。
- 「2 起單資料」不再有更正按鈕；README 與步驟欄改成七步。
- 來源「人工維護」的商品沒有匯入批次（`first_run_id`、`order_import_id` 都空白）。
- 位置（#203）：人工維護的規則在 `sp_workflow.product_terms`：`draft_from(TDCC, …)` 打開一檔、`missing_products` 給「檢查」、`write` 存檔；兩邊都沒有這檔時由 `window_actions.open_product` 擋下。

## English

### Context

ADR 0016 lets database product terms be partially blank; products with blank fields stay 待確認 and cannot be checked. But operators had nowhere to find which products lack data or to fill it in: the order-sheet import still blocks a product with blank required fields, and the correction after locking (ADR 0014) accepts only locked products and allows no blanks. A new product already in the trade rows but not yet in the order sheet could only wait for the order sheet.

### Decision

- **Entry**: a new "7 人工維護" (manual maintenance) tab, last in the step bar (step headers read N／7). The "更正商品條件…" button on "2 起單資料" is removed, and corrections after locking move here (superseding ADR 0014's entry point).
- **Check**: "檢查" lists two kinds of products, sorted by TDCC: unlocked products missing order-sheet required fields (ADR 0016's `missing_fields`, the same test check terms, checking, and mail drafts use); and TDCCs in the trade rows with no product terms in the database yet (all required fields count as missing). Locked products are not listed (locking requires complete check terms, so they cannot have blanks). The result table is drawn cell by cell, with missing cells in light red, horizontal scrolling, and the TDCC column fixed on the left; `ttk.Treeview` can only color whole rows, so it is drawn on a Canvas.
- **Opening**: click a product in the check results, or type any TDCC and click "打開" (including locked and complete ones). A product with terms in the database shows its current values; a trade-only TDCC starts blank, with product type FCN (the trade rows hold only FCN) and the issuer from the issuer prefix map. A TDCC in neither cannot be opened: new products always appear first in the trade rows or the order sheet, and this keeps a mistyped TDCC from creating a product that does not exist.
- **Editing**: the same editor fields and format checks as "2 起單資料".
  - Locked: ADR 0014's correction rules (no blanks, Issue date fixed, the product stays locked, check results are untouched, prices are recalculated only when a field affecting check terms changes).
  - Unlocked or trade-only: bad formats and inconsistent fields (for example a KI % with barrier type NONE, or a Strike date before the Trade date) block the save under the import rules; required fields may stay blank for now (light red, noted "缺，可以先存、之後再補"), except the product type (`NOT NULL` in the database). KI % is not a required field, so the import rule still applies: it is required with a KI barrier type; when unsure, leave Barrier Type blank too, and the product stays on the check list. An Issue date not yet backfilled can be edited, as in the order sheet.
- **Issuer and denomination**: an existing product keeps its issuer (as in corrections); a new one takes the issuer from the TDCC's first three digits in the issuer prefix map, and an unknown prefix blocks the save. The denomination is not edited directly: a new product gets the currency default (blank when the currency is blank), and an existing one changes to the new currency's default only when the currency changes (as in corrections).
- **Saving**: for unlocked products one transaction locks the row, confirms the database is unchanged since opening, writes the terms, recalculates this product's check terms (ADR 0012), and writes the record; any failure leaves nothing half-written. After saving, the editor shows the values as stored (percentages kept to 6 decimals), so the same product can be edited and saved again. New products get source 人工維護 (migration 0013 relaxes the source constraint, since manual maintenance has no import batch) and status 在庫. A later order-sheet import overwrites them like any other unlocked product, recording "人工維護建立的商品改由起單資料匯入接手".
- **Record**: every save writes one row in `workflow.term_correction`, the table shared with corrections: the changed fields (before and after; for a new product including issuer and denomination), the previous product columns (blank for a new product, so `previous` becomes nullable), the Windows user name, the time, and an optional reason. A new `locked` column tells them apart: true for corrections of locked products (every row from before the migration), false for maintenance of unlocked ones. Nothing is written when nothing changed.
- **Concurrent changes**: nothing is written, and the operator reloads, when after opening the product changed in the database, became locked (check passed), or was deleted, or when a trade-only TDCC got product terms from someone else (including two people saving at once) or lost its trade rows.

### Alternatives considered

- **Extend ADR 0014 and keep the dialog on "2 起單資料"**: a smaller change, but correcting and filling in are the same task (changing product terms in the database), and two entry points would have to be learned; the order-sheet page serves the daily import, while filling in data often happens later.
- **A separate record table for manual maintenance**: keeps unlocked and locked changes apart, but "who changed what on this product, and when" would need two tables; one table with a `locked` column is easier to query.
- **Allow creating TDCCs absent from the trade rows**: terms could be entered earlier, but a mistyped TDCC would leave a product that does not exist, and branch allocation and checking depend on the trade rows anyway.
- **Save even when recalculating check terms fails** (as imports keep data and report the reason): saving would never be blocked by recalculation, but check terms could disagree with the terms; manual maintenance saves one product at a time, so on failure nothing is written, the reason is shown, and the operator saves again after the fix.
- **Allow a blank KI % with a KI barrier type and list it separately**: more flexible, but "missing data" would have two definitions (ADR 0016 counts only `REQUIRED`), and mail drafts and checking would need their own handling of a blank KI %; leaving Barrier Type blank too keeps the product findable with the one test.

### Consequences

- New migration 0013 (schema version 0013); run `spw-db-migrate` once after updating. Earlier correction rows get `locked` true and are otherwise unchanged.
- Code reading `workflow.term_correction` must handle a blank `previous` and use `locked` to tell corrections from manual maintenance.
- "2 起單資料" no longer has a correction button; the README and the step bar now have seven steps.
- Products with source 人工維護 have no import batch (`first_run_id` and `order_import_id` are blank).
- Location (#203): the maintenance rules live in `sp_workflow.product_terms`: `draft_from(TDCC, …)` opens a product, `missing_products` serves 檢查, and `write` saves; `window_actions.open_product` refuses a TDCC found in neither table.
