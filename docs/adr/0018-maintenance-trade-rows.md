# 0018: 人工維護修改交易明細／Editing Trade Rows in Manual Maintenance

- 狀態／Status: Accepted
- 日期／Date: 2026-10-22
- 追蹤／Tracking: #116
- 補充／Extends: [ADR 0017](0017-manual-maintenance-tab.md) 的人工維護分頁；已鎖住的照 [ADR 0014](0014-term-correction.md)／the manual maintenance tab of ADR 0017; locked products follow ADR 0014

## 繁體中文

### 背景

每日匯入以資料庫為準：交易列和資料庫不同時顯示「內容不同（未覆蓋）」，資料庫保留第一次的內容。交易明細第一次匯入時寫錯（例如金額、分公司打錯），之後改了 Excel 也不會進資料庫，分公司配置、交叉比對就一直用錯的值；以前只能直接改資料庫。

### 決策

- **畫面**：「7 人工維護」打開一檔時一起讀這個 TDCC 的交易明細列，放在編輯區的商品條件底下，依 row_seq 逐列（「第 n 列」）列出，欄位同每日匯入的交易明細欄。只有交易明細、還沒有商品條件的 TDCC 也列；資料庫還沒有交易明細時寫出來。顏色同商品條件：擋存檔的紅色，和資料庫不同的黃色、寫出資料庫的值、可以一鍵改回。
- **只改值**：不新增、不刪除列。備註改成 Cancel 或「取消」等於刪除（每日匯入不存取消的列），所以擋存檔。
- **格式與必填**：畫面打的字照每日匯入的規則解讀（`daily_import.trade_value`，和匯入共用同一套轉換），存進去的值和之後匯入同一個 Excel 的值一樣；必填欄同每日匯入（交易日期、幣別、金額、FC/PB、分公司），空白或寫錯擋存檔，畫面標出原因。只看改了的格子：沒改的照資料庫的值，不擋這次存檔（例如以前匯入、備註寫了取消的舊列，改別的欄或只改商品條件照樣可以存）。
- **一起存檔**：同一個「存檔」按鈕把商品條件與交易明細在同一個 transaction 裡寫入，只重算一次；任何一步失敗都不留下一半。存檔時鎖住這檔的交易明細列，打開之後多了、少了列或值改過就不寫入，請重新載入。
- **重算**：
  - 還沒鎖住：照 ADR 0012 重算這一檔的核對條件（分公司配置、交叉比對），和上一次不同才寫入。只有交易明細、還沒有商品條件的不重算（沒有商品條件就沒有核對條件），也不會因為改交易明細就建出商品條件。
  - 已鎖住：照 ADR 0014，商品維持鎖住、核對結果不動；只有核對條件會用到的欄（交易日期、幣別、金額、分公司、UF）改了才重算，改 FC、備註等不重算。沒算過核對條件的（migration 0006 時就在的商品）照舊不重算。
- **FC 名字**：改了的 FC 名字照每日匯入比對信箱的規則（大小寫不同算同一人、先查別名、PB 不比），對不到時在那一格寫紅字提醒、完成訊息列出名字，但不擋存檔：信箱可能還沒補上這個人，寄信草稿本來就會標出找不到收件人。
- **紀錄**：新增 `workflow.trade_row_correction`（migration 0014），每次改了交易明細就留一筆：改了哪些格（row_seq、欄、存檔前、存檔後）、存檔前這檔的所有交易明細列、Windows 使用者名稱、時間、原因（同商品條件的原因欄）、`locked` 記存檔時是否已鎖住。不連到 `workflow.product`（只有交易明細的 TDCC 沒有商品條件）。商品條件也改了時照舊在 `workflow.term_correction` 另留一筆；已鎖住的商品只改交易明細、重算後價格改變時，改變的價格記在這筆的 `price_changes`。沒有改任何格子時不寫入。
- **之後的每日匯入**：照舊以資料庫為準、不覆蓋；改到和 Excel 一樣時匯入結果是「已存在」。

### 考慮過的替代方案

- **交易明細另一個存檔按鈕**：兩邊各自存比較單純，但同時改兩邊時會分兩個 transaction、重算兩次，第二次失敗就只存了一半。
- **紀錄放在 `workflow.term_correction`**：一張表比較好查，但它連到 `workflow.product`，只有交易明細的 TDCC 存不進去；而且交易明細是一列一列改，和商品條件的欄位不同。
- **讓每日匯入用 Excel 覆蓋資料庫**：不用另外改，但資料庫以第一次匯入為準是既有的規則，覆蓋會讓手動修正和 Excel 打架。
- **FC 名字對不到信箱就擋存檔**：可以確保寄得出信，但信箱常常比交易明細晚補，擋住會讓其他欄也改不了。
- **允許改成取消（刪除列）**：可以處理整筆取消，但刪除列牽涉 row_seq 與之後匯入的對應，超出這次範圍。

### 後果

- 新增 migration 0014（資料表版本 0014）；更新程式後重新執行安裝，安裝器會自動套用（#119）。
- 每日匯入的交易明細欄位與轉換規則公開為 `TRADE_FIELDS`、`TRADE_REQUIRED`、`trade_value`、`is_cancel_note`，人工維護共用，改匯入規則時兩邊一起變。
- `check_terms.recalculate_corrected` 多一個 `trades_changed`：已鎖住的商品只改交易明細時也能重算。
- 查「這檔被誰改過什麼」要看 `workflow.term_correction` 與 `workflow.trade_row_correction` 兩張表。

## English

### Context

The daily import treats the database as authoritative: trade rows that differ from the database show "內容不同（未覆蓋）" (different, not overwritten), and the database keeps the first version. A mistake in the first import (a wrong amount or branch, say) never reaches the database even after the workbook is fixed, so branch allocation and the cross-check keep using the wrong values; the only fix was editing the database directly.

### Decision

- **Screen**: opening a product on "7 人工維護" also reads that TDCC's trade rows and lists them under the product terms in the editor, row by row by row_seq ("第 n 列"), with the daily import's trade columns. Trade-only TDCCs without product terms are listed too; a TDCC with no trade rows in the database says so. Colors match the product terms: red blocks the save; yellow marks a difference from the database, shows the database value, and has a button to restore it.
- **Values only**: rows are never added or deleted. Changing 備註 to Cancel or 取消 amounts to deleting the row (the daily import never stores cancelled rows), so it blocks the save.
- **Formats and required fields**: typed text is read with the daily import's rules (`daily_import.trade_value`, the same conversion the import uses), so a stored value equals what a later import of the same workbook would produce. Required fields match the daily import (交易日期, 幣別, 金額, FC/PB, 分公司); blanks and bad formats block the save, with the reason shown on screen. Only edited cells are checked: unchanged cells keep their database values and never block a save (for example an old row whose 備註 mentions a cancellation does not stop edits to other cells or to the product terms).
- **One save**: the one "存檔" button writes the product terms and trade rows in a single transaction with a single recalculation; any failure leaves nothing half-written. Saving locks the TDCC's trade rows, and if rows were added, removed, or changed since opening, nothing is written and the operator reloads.
- **Recalculation**:
  - Unlocked: this product's check terms (branch allocation, cross-check) are recalculated under ADR 0012 and written only when they differ. A trade-only TDCC without product terms is not recalculated (no product terms, no check terms), and editing trade rows never creates product terms.
  - Locked: under ADR 0014 the product stays locked and check results are untouched; check terms are recalculated only when a column they use changes (交易日期, 幣別, 金額, 分公司, UF), not for FC, 備註, and the like. Products without check terms (present at migration 0006) are still never recalculated.
- **FC names**: a changed FC name is matched against the mailbox with the daily import's rules (case-insensitive, aliases first, PB skipped). An unmatched name gets a red note on its cell and is listed in the completion message, but does not block the save: the mailbox often lags behind, and mail drafts already flag a missing recipient.
- **Record**: a new `workflow.trade_row_correction` table (migration 0014) gets one row whenever trade rows change: the changed cells (row_seq, column, before, after), all of the TDCC's trade rows before the save, the Windows user name, the time, the reason (the same reason box as the product terms), and `locked` recording whether the product was locked. It has no foreign key to `workflow.product`, since trade-only TDCCs have no product terms. When product terms change too, `workflow.term_correction` still gets its own row; when only trade rows change on a locked product and recalculation changes prices, the changed prices go in this row's `price_changes`. Nothing is written when no cell changed.
- **Later daily imports**: still treat the database as authoritative and do not overwrite; once the database matches the workbook, the import shows "已存在" (already exists).

### Alternatives considered

- **A separate save button for trade rows**: simpler on each side, but changing both would take two transactions and two recalculations, and a failed second save would leave only half saved.
- **Record in `workflow.term_correction`**: one table is easier to query, but its foreign key to `workflow.product` cannot hold trade-only TDCCs, and trade rows change row by row with columns unlike the product terms.
- **Let the daily import overwrite the database from the workbook**: no separate editing needed, but the database keeping the first import is an established rule, and overwriting would make manual fixes fight the workbook.
- **Block the save when an FC name is not in the mailbox**: guarantees mail can be sent, but the mailbox often lags behind the trade rows, and blocking would keep the other columns from being fixed.
- **Allow cancelling (deleting) rows**: would cover whole cancellations, but deleting rows involves row_seq and matching later imports, which is out of scope here.

### Consequences

- New migration 0014 (schema version 0014); rerun the installer after updating and it applies the migration automatically (#119).
- The daily import's trade columns and conversion rules are public as `TRADE_FIELDS`, `TRADE_REQUIRED`, `trade_value`, and `is_cancel_note`, shared with manual maintenance, so a change to the import rules applies to both.
- `check_terms.recalculate_corrected` gains `trades_changed`, so a locked product can be recalculated when only its trade rows change.
- "Who changed what on this product" now spans `workflow.term_correction` and `workflow.trade_row_correction`.
