# 0013: 核對從資料庫讀條件並寫回／Check Against Database Terms and Write Back

- 狀態／Status: Accepted
- 日期／Date: 2026-10-21
- 追蹤／Tracking: #68
- 補充／Completes: [ADR 0006](0006-new-product-data-from-order.md)「核對與回填」、[ADR 0008](0008-daily-prices-denomination-and-order-dates.md)「單位面額」、[ADR 0012](0012-check-terms-calculation.md) 算出的核對條件／the checking and backfill rules of ADR 0006, the denomination rule of ADR 0008, and the check terms calculated per ADR 0012

## 繁體中文

### 背景

ADR 0006 決定核對改從資料庫讀條件，核對完把 TS 回填的 ISIN、發行日、比價日與 VWAP 價格寫進商品資料、鎖住。核對規則（`src/fcn_checker/`）目前只讀 Excel 參考條件表，回填、核對結果檔、回報包都以那張表的列與儲存格為準；Excel 來源要照常可以用。#68 留了一項待定：寫回資料庫失敗時怎麼辦。

### 決策

使用者 2026-10-21 確認：

- **寫回失敗**：核對結果檔照常輸出，畫面標出寫回失敗與原因，之後可以重新寫回；放回資料夾不受影響。
- **什麼時候算通過、鎖住**：說明書與投資人須知都通過或人工放行（同「TS/IIS 打勾」）才回填並鎖住；人工放行的也回填並鎖住。
- **核對條件還不是「完成」**（等股價、等交易明細、待確認）：那一份不核對、需人工覆核、不能放行，同批其他照常。
- **單位面額**（ADR 0008）：人工放行的說明書，單位面額和資料庫不同時改成 TS 的值，原值留紀錄。

實作時決定（低風險、可回復）：

- **資料庫來源 = 從資料庫產生一份參考條件表**：依 PDF 檔名的 TDCC 讀資料庫，寫一份和匯入格式同欄位的 Excel（放在安裝資料夾 `runtime/資料庫參考條件表/`，`fcn-batch` 放在輸出資料夾），再交給原本的預覽、核對、放行、存檔與回報包。規則、回填決策與錯訊完全共用，所以「資料庫來源與同一份資料的 Excel 核對結果相同」由構造保證；回報包的「參考條件表_未通過」就是這份表。核對程式只多一個接口（`DatabaseSource`：產生表、寫回），連資料庫的程式在 `sp_workflow/check_source.py`。
- **表上沒有列的原因**：資料庫沒有這檔、核對條件還沒完成、以前清單更新鎖住的舊商品（沒有核對條件，請用 Excel），寫進那一份的配對結果；沒有對到列的文件本來就不能放行。
- **寫回的內容**：說明書這次寫進「回填後」的格子（回填與 VWAP 覆寫）→ `workflow.product` 的 ISIN、發行日，`workflow.autocall_date` 的比價日（空值寫法存空白），`workflow.underlying_price` 的 VWAP 價格；單位面額見上；然後鎖住（`locked_at`）。每個改變的原值記在 `workflow.backfill_log`。每份文件最新的核對結果存 `workflow.check_result`（存檔時寫入，再存一次就覆蓋；人工放行後要存檔才會更新；資料庫沒有這檔的文件不存）。整次寫回在一個 transaction 裡。
- **核對後條件又改了**：回填前用資料庫現在的值重新產生那一列，和核對時那份表比（回填欄位除外），並確認核對條件仍是完成；不同就不回填、不鎖住，請作業人員重新核對。這樣鎖住的一定是核對過的條件（預覽之後重新匯入起單資料或重算的情況）。
- **已鎖住的商品**：照資料庫的值產生（TS、IIS 打勾、回填過的欄位有值），可以再核對，但不再回填、不改商品資料；核對結果照樣保存。
- **畫面**：核對分頁多一列「條件來源」，預設 Excel（行為不變）；選資料庫時不用選參考條件表。（[#108](https://github.com/TaylorYam/structured-product-workflow/issues/108) 改為預設資料庫，選資料庫時參考條件表那一列收起。）寫回失敗時出現「重新寫回資料庫」。`fcn-batch --database <PDF…>` 做同樣的事，寫回失敗只提示。

### 考慮過的替代方案

- **在記憶體裡從資料庫組參考條件表的列，不寫檔**：少一個檔，但回填、核對結果檔、來源快照與回報包都以檔案為準，要各自改；共用一份檔最不容易兩邊不一致。
- **把「核對條件未完成」照樣產生列、讓規則判不一致**：作業人員會看到一堆價格不一致，而真正的原因是資料還沒到。
- **寫回失敗整批算錯誤**：使用者選擇保留核對結果檔、可以重新寫回。

### 後果

- 更新程式後要跑 `spw-db-migrate`（資料表版本 0010）。
- 資料庫來源每次都留一份產生的參考條件表，可以拿來用 Excel 重跑或給維護者。
- 以前清單更新匯入、已鎖住的商品沒有核對條件，資料庫來源會請作業人員改用 Excel。
- 核對讀資料庫的測試放在 `tests/checker/`（要用合成說明書）並標 `db`；CI 在有 PostgreSQL 的工作依序跑 `pytest -q -m db tests/checker`。
- Strike date 怎麼和 TS 的定價日比對（ADR 0011 留給 #68）這次沒有改：規則照舊比交易日；需要時另開 Issue。

## English

### Context

ADR 0006 decided that checking reads its terms from the database and, after checking, writes the TS-backfilled ISIN, issue date, observation dates, and VWAP prices into product data and locks it. The checker (`src/fcn_checker/`) only reads an Excel reference workbook today, and backfill, the result workbook, and the report bundle all key on that workbook's rows and cells; the Excel source must keep working. #68 left one open question: what happens when the database write-back fails.

### Decision

Confirmed by the user 2026-10-21:

- **Failed write-back**: the result workbook is still written, the window shows the failure and its reason, and the write-back can be retried later; returning files to folders is unaffected.
- **Pass and lock**: backfill and lock only when both the term sheet and the investor sheet pass or are manually released (the same rule as the TS/IIS checkmarks); manually released products are also backfilled and locked.
- **Check terms not done** (等股價, 等交易明細, 待確認): that document is not checked, needs review, and cannot be released; the rest of the batch proceeds.
- **Denomination** (ADR 0008): for a manually released term sheet whose denomination differs from the database, the TS value is written and the old value recorded.

Implementation choices (low risk, reversible):

- **Database source = a reference workbook generated from the database**: the TDCCs in the PDF file names are read from the database into an Excel file with the import-format columns (saved in the install folder's `runtime/資料庫參考條件表/`, or the output folder for `fcn-batch`), which then goes through the existing preview, check, release, save, and report-bundle flow. Rules, backfill decisions, and messages are fully shared, so "database results equal the Excel results for the same data" holds by construction, and the bundle's `參考條件表_未通過` is that workbook. The checker gains one interface (`DatabaseSource`: export and write back); the database code lives in `sp_workflow/check_source.py`.
- **Why a TDCC has no row**: not in the database, check terms not done, or an old 清單更新 product locked without check terms (use Excel); the reason goes into that document's pairing result, and documents without a row could never be released anyway.
- **What is written back**: the cells this term sheet writes into 回填後 (fill and VWAP overwrite) → ISIN and issue date in `workflow.product`, observation dates in `workflow.autocall_date` (the empty-value marker stored as blank), VWAP prices in `workflow.underlying_price`; the denomination as above; then the lock (`locked_at`). Every changed value's old value goes into `workflow.backfill_log`. Each document's latest result is kept in `workflow.check_result` (written on save and overwritten by the next one; a manual release is stored once saved; documents whose TDCC is not in the database are not stored). The whole write-back is one transaction.
- **Terms changed after checking**: before backfilling, the row is regenerated from current database values and compared with the checked workbook (backfill fields excluded), and the check terms must still be 完成; otherwise the product is neither backfilled nor locked and the operator rechecks. This guarantees that what gets locked is what was checked (for order-sheet re-imports or recalculations after the preview).
- **Locked products** are exported from their database values (TS/IIS checked, backfilled fields filled) and can be checked again, but are not backfilled again and their product data is not changed; results are still saved.
- **Window**: the check tab gets a 條件來源 row defaulting to Excel (behaviour unchanged); with the database no reference workbook is chosen. ([#108](https://github.com/TaylorYam/structured-product-workflow/issues/108) changed the default to the database and hides the reference workbook row while it is chosen.) A failed write-back shows 重新寫回資料庫. `fcn-batch --database <PDFs…>` does the same and only warns when the write-back fails.

### Alternatives considered

- **Building reference rows in memory without a file**: one file fewer, but backfill, the result workbook, the source snapshot, and the report bundle all work from the file and would each need changes; one shared file keeps both sources consistent.
- **Exporting rows for products whose check terms are not done and letting the rules flag them**: operators would see many price mismatches whose real cause is missing data.
- **Treating a failed write-back as a batch error**: the user chose to keep the result workbook and allow a retry.

### Consequences

- Run `spw-db-migrate` after updating (schema version 0010).
- Each database-sourced check leaves its generated reference workbook, usable for an Excel rerun or by the maintainer.
- Old locked 清單更新 products have no check terms, so the database source asks the operator to use Excel.
- The database-source tests live in `tests/checker/` (they need the synthetic term sheets) and are marked `db`; CI runs `pytest -q -m db tests/checker` serially in the job that has PostgreSQL.
- How the Strike date is compared with the TS pricing date (left to #68 by ADR 0011) is unchanged here: rules still compare the trade date; open an Issue if needed.
