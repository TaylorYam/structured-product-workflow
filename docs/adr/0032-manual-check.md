# 0032: 人工核對完成：跳過自動核對、鎖住／Manual Check: Skipping the Automatic Check and Locking

- 狀態／Status: Accepted
- 日期／Date: 2026-11-09
- 追蹤／Tracking: #527
- 補充／Completes: [ADR 0013](0013-check-from-database.md)（核對寫回後鎖住）、[ADR 0017](0017-manual-maintenance-tab.md)（人工維護）／ADR 0013 (locking after the check write-back) and ADR 0017 (manual maintenance)

## 繁體中文

### 背景

比價只看核對條件是「完成」、而且有比價日（`workflow.autocall_date`）；比價日、ISIN、發行日與 VWAP 的價格平常由「4 核對」讀說明書後寫回資料庫，寫回的同時鎖住。系統不支援的上手或商品種類（例如 DACN 還沒開放自動核對的上手）在核對是「未支援上手／不支援樣態」，不能人工放行，所以永遠沒有比價日，比價一直是「待確認：沒有比價日」，也不會鎖住。

### 決策

2026-11-09 使用者決定：

- 作業人員自己核對完說明書後，在「7 人工維護」直接輸入核對要回填的值，不另外匯入 Excel。
- 任何還沒鎖住的商品都可以用，不限系統不支援的上手。

做法（`sp_workflow.manual_check`）：

- 人工維護打開還沒鎖住的商品時有「人工核對完成…」：輸入 ISIN、比價日（一行一個，依序）；期初定價是 VWAP 時另外輸入各標的的進場價。原因沿用頁面上的原因欄。畫面有還沒存檔的修改時要先存檔。
- 寫入前的檢查全部列出，任一項不過就什麼都不寫：還沒鎖住；核對條件是完成；已有 Issue date（鎖住後不能改）；ISIN 是 2 碼英文＋9 碼英數＋1 碼數字；比價日 1～12 期、由小到大不重複、最後一期等於 Final valuation date（同比價前的檢查）；VWAP 每個標的都有大於 0 的進場價。
- 通過後在一個 transaction 裡照核對寫回的方式寫入（`product_terms.backfill`）：ISIN、比價日 1～12（沒用到的期別寫空白）、VWAP 的四個價格（執行價、KI、KO 由進場價乘比例，比例空白時空白，同更正 VWAP 的算法）。值有改變時 `workflow.backfill_log` 留原值，結果檔欄寫「人工核對（人工維護）」；另記一筆項目「人工核對」、新值是原因（沒寫時「跳過自動核對」）的紀錄，然後鎖住。接著比價全部（同其他寫入後的比價），畫面重新打開這檔，換成更正的規則。
- 不寫核對結果（`workflow.check_result`）：沒有讀說明書，沒有核對結果可存。進度頁把回填紀錄裡那一筆「人工核對」當成一份核對結果（「人工核對：跳過自動核對」，核對日期是寫入時間），所以沒跑過核對的商品鎖住後照常是待寄信；進度的判斷順序不變。
- 打開之後資料庫的條件被改過（例如重新匯入起單資料）時不寫，請重新載入（同人工維護存檔）。輸入的進場價同核對條件取到小數第 4 位。
- 是不是 lookback 看說明書，系統判斷不了：BEN 的輸入視窗提醒先在編輯區填 Lookback end date 存檔。

### 考慮過的替代方案

- **填參考條件表 Excel 後匯入**：可以一次很多檔、和回補 FCN 以外商品同一種格式，但要另外開檔案、對欄位；使用者選在人工維護直接輸入。
- **只限核對結果是未支援上手／不支援樣態的商品**：比較不會被誤用，但要先跑過一次核對才能用；使用者選不限。
- **寫一筆核對結果、類別記「人工核對」**：進度頁的核對欄會有字，但 `check_result` 的類別、說明書檔名等欄是給核對結果用的，要改資料表；先用回填紀錄留痕，需要時再開 Issue。

### 後果

- 系統不支援的上手也能鎖住、比價；人工核對的商品之後照已鎖住的規則更正。
- 是否真的核對過由作業人員負責：寫入前再問一次，回填紀錄留下誰、何時、原因。
- 不需要新的資料表版本。

## English

### Context

Observation needs check terms that are 完成 (done) and observation dates (`workflow.autocall_date`). The observation dates, ISIN, issue date, and VWAP prices are normally written back by "4 核對" after reading the term sheet, and the write-back locks the product. Issuers or product types the system does not support (for example DACN from issuers not yet opened for automatic checking) come out of the check as 未支援上手／不支援樣態 (unsupported issuer or variant) and cannot be released by hand, so they never get observation dates, stay 待確認: 沒有比價日 (to confirm: no observation dates) in observation, and are never locked.

### Decision

The user decided on 2026-11-09:

- After checking the term sheet themselves, operators type the values the check would write back directly on "7 人工維護" (manual maintenance), rather than importing an Excel file.
- Any unlocked product may use it, not only unsupported issuers.

Implementation (`sp_workflow.manual_check`):

- When an unlocked product is open on manual maintenance, "人工核對完成…" (manual check done) asks for the ISIN and the observation dates (one per line, in order); when the initial pricing is VWAP it also asks for each underlying's initial price. The reason comes from the page's reason field. Unsaved edits must be saved first.
- All checks are listed before writing, and nothing is written if any fails: the product is unlocked; its check terms are 完成; it has an Issue date (which cannot change after locking); the ISIN is 2 letters, 9 letters or digits, and 1 digit; there are 1 to 12 observation dates, strictly increasing, the last equal to the Final valuation date (the same check observation makes); every VWAP underlying has an initial price above 0.
- When they pass, one transaction writes the values the way the check write-back does (`product_terms.backfill`): the ISIN, observation dates 1 to 12 (unused slots blank), and the four VWAP prices (strike, KI, and KO derived from the initial price times the percentage, blank when the percentage is blank, as in VWAP corrections). Changed values keep their old value in `workflow.backfill_log` with 人工核對（人工維護） as the result file; one more row with item 人工核對 records the reason as its new value (跳過自動核對 when blank); then the product is locked. Observation then runs for all products (as after other writes), and the page reopens the product under the correction rules.
- No check result (`workflow.check_result`) is written: no term sheet was read, so there is no result to store. The progress page treats the backfill log's 人工核對 row as one check result ("人工核對: 跳過自動核對", dated when it was written), so a product never run through the check is 待寄信 once locked; the order of the progress rules is unchanged.
- Nothing is written when the database terms changed after opening (for example an order-sheet re-import); reload first, as with a manual-maintenance save. The entered initial prices are rounded to 4 decimals like calculated ones.
- Whether a product has a lookback comes from the term sheet and cannot be detected: for BEN the window reminds the operator to fill Lookback end date in the editor and save first.

### Alternatives considered

- **Filling in the reference workbook and importing it**: handles many products at once in the same format as the non-FCN product backfill, but needs a separate file and column matching; the user chose typing on manual maintenance.
- **Limiting it to products whose check result is unsupported issuer or variant**: harder to misuse, but needs a check run first; the user chose no limit.
- **Writing a check result with category 人工核對**: the progress page's check column would show it, but `check_result`'s category and file-name columns are meant for check results and would need a schema change; the backfill log keeps the trail for now, with a new Issue if needed.

### Consequences

- Products from unsupported issuers can be locked and observed; manually checked products are corrected afterwards under the locked-product rules.
- Whether the term sheet was really checked is the operator's responsibility: the window asks once more before writing, and the backfill log records who, when, and why.
- No new schema version is needed.
