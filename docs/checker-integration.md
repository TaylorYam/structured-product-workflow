# 核對工具串接提案／Checker Integration Proposal

## 繁體中文

狀態：Proposed，尚未實作或正式決定。獨立專案分工已確認；以下為建議接法。

初期採本機 CLI 與檔案交換。流程專案的 checker adapter 負責呼叫已安裝、固定版本的核對工具；不直接 import 私有函式、不複製解析規則，也不要求部署網路服務。

目前檢視基準：核對專案 commit `7db5786df8ca13a64066a4970a743be8a0565f3c`。現有 `fcn-batch` 接收參考條件表、多份 PDF 及輸出目錄；PDF 嚴格使用 `<12位TDCC>_TS.pdf`、`<12位TDCC>_IIS.pdf`。先預覽、再核對、再儲存。輸出含 Excel 結果與 JSON 核對紀錄；結束碼 0／1／2 分別代表 PASS／有不一致或需覆核／ERROR。這些是當時已觀察能力，實作時需重新驗證版本。

資料庫選型已另記錄於 [ADR 0001](adr/0001-postgresql-workflow-storage.md)。流程專案從 PostgreSQL 讀取已確認條件，自動產生 Excel 交換檔，再接收結果回存；checker 不直接連 PostgreSQL。這是目標流程，尚未實作；回填與版本失效規則見 [資料庫與部署](database-and-deployment.md)。

建議流程：

1. 建立獨立 run 目錄，放入已確認的 PDF 與參考條件表快照。
2. 保存案件編號、run ID、輸入指紋、核對程式與設定版本；供應確切 PDF 路徑給 CLI。
3. 讀取逐商品及逐文件結果，將程式執行失敗、需覆核、核對通過分開處理；不能只用整批結束碼判定單商品可寄送。
4. 保存本次結果、輸出路徑及文件對應；檢查完成條件後才推進案件。

目前已實作的 TS／IIS 準備（#5）只透過檔案銜接：輸出符合檔名規則的 PDF，放回時直接讀核對結果檔：「回填後」工作表（第 2 列表頭的 `TDCC Code`、`TS/IIS完成檢核`＝`V`）決定標準檔名放回，「錯誤清單」工作表（第 1 列表頭的 `TDCC Code`、`PDF 檔名`、`錯訊`）決定以「_待確認」放回（觀察基準 `34837cd`）；工作表或欄位改名時放回會整批停止並寫出原因。現有 JSON 是核對紀錄，不保證已是穩定的跨專案 interface。建議先以固定版本 adapter 驗證；若不足，在核對專案另開 Issue 設計正式結果格式或結果匯出指令。本專案初始化不修改核對專案。

未來契約應明確定義 schema_version、run_id、每個商品與 TS／IIS 的狀態、人工放行、可歸檔判定、輸入輸出指紋、錯誤及取消狀態。這些是候選欄位，不是現有已支援參數。未知版本、損毀或缺少結果、來源變更均停止推進；重試新增 run，不覆蓋既有紀錄。

兩個 repository 各自版本化，建立相容版本對照與虛構資料的契約測試後才能升級。若將來需要共用伺服器，再評估 HTTP interface；目前不預先引入。

## English

Status: Proposed, neither implemented nor finalized. Repository separation is confirmed; the integration approach below is a recommendation.

Start with local CLI execution and file exchange. A checker adapter invokes an installed, pinned checker version without importing private functions, copying parsing rules, or requiring a network service.

Inspected baseline: checker commit `7db5786df8ca13a64066a4970a743be8a0565f3c`. Existing `fcn-batch` accepts a reference workbook, multiple PDFs, and an output directory. PDF names must be `<12-digit-TDCC>_TS.pdf` or `<12-digit-TDCC>_IIS.pdf`. It previews, checks, then saves Excel results and JSON audit records. Exit codes 0/1/2 mean PASS, mismatch or review required, and ERROR. Revalidate these observed capabilities against the version used during implementation.

Database selection is recorded separately in [ADR 0001](adr/0001-postgresql-workflow-storage.md). The workflow will read confirmed terms from PostgreSQL, generate exchange Excel automatically, and persist returned results. The checker never directly connects to PostgreSQL. This is unimplemented target behavior; see [database and deployment](database-and-deployment.md) for backfill/version invalidation rules.

Recommended sequence:

1. Create an isolated run directory containing confirmed PDFs and a reference-workbook snapshot.
2. Record case ID, run ID, input hashes, checker version, and configuration version; pass exact PDF paths to the CLI.
3. Read per-product and per-document results. Distinguish execution failures, review, and passes; batch exit codes alone cannot authorize distribution of individual products.
4. Preserve results, output locations, and document mappings; advance only after completion conditions are satisfied.

The implemented TS/IIS preparation (#5) integrates through files only: it outputs PDFs that follow the naming rule and, when returning files, reads the result workbook directly: the 回填後 sheet (`TDCC Code` and `TS/IIS完成檢核` = `V` in the row-2 header) selects standard-name returns and the 錯誤清單 sheet (`TDCC Code`, `PDF 檔名`, `錯訊` in the row-1 header) selects `_待確認` returns (observed at `34837cd`); renamed sheets or columns stop the whole return with a reason. Existing JSON is an audit record, not a guaranteed stable integration interface. Validate a pinned adapter first. If insufficient, open a separate checker issue to define a supported result format or export command. This bootstrap does not modify the checker.

A future contract should define schema_version, run_id, per-product and TS/IIS states, manual releases, archiving eligibility, input/output hashes, errors, and cancellation. These are candidate fields, not existing supported parameters. Unknown versions, missing/corrupt results, and changed sources stop progress. Retries create new runs and preserve existing records.

Version repositories independently; require a compatibility matrix and synthetic contract tests before upgrades. Consider an HTTP interface only if a shared server becomes necessary.
