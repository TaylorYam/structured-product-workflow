# Structured Product Workflow／結構型商品作業流程

## 繁體中文

狀態：「TS／IIS 準備與放回」已完成，以真實樣本複本試跑過，等作業主機公槽實測（[#5](https://github.com/TaylorYam/structured-product-workflow/issues/5)）。「每日匯入」最小 FCN 資料庫見 [#32](https://github.com/TaylorYam/structured-product-workflow/issues/32)；「寄信草稿」需求已確認、待實作（[#29](https://github.com/TaylorYam/structured-product-workflow/issues/29)，改讀資料庫）。其他流程仍在設計階段。

本專案管理正式 Order、建檔、上手文件歸檔、核對串接及 FC 寄送。詢價端沿用既有系統。
核對規則由獨立的 [fcn-term-sheet-checker](https://github.com/TaylorYam/fcn-term-sheet-checker) 負責。

閱讀順序：[業務背景](CONTEXT.md) → [架構](docs/architecture.md) → [待定事項](docs/open-questions.md)。
資料保存與部署先讀 [PostgreSQL 決策](docs/adr/0001-postgresql-workflow-storage.md) 與 [開發部署設計](docs/database-and-deployment.md)。第一階段的資料範圍見 [ADR 0003](docs/adr/0003-minimal-fcn-database.md)。
串接工作另讀 [核對工具串接提案](docs/checker-integration.md)。

從 TaylorYam/ai-project-template 建立，初始化日期 2026-10-18。保留範本的 Issue、PR、CI 與 Agent 工作流程。
只提交文件與虛構範例；公司信件、交易、客戶、庫存、公槽檔案及憑證保留在公司環境。

### TS／IIS 批次準備

從公槽 FCN 底下以 TDCC 命名的商品資料夾，挑出中文產品說明書與中文投資人須知，改名成核對工具要的 `<TDCC>_TS.pdf`、`<TDCC>_IIS.pdf`，放進新開的批次資料夾。商品資料夾裡的檔案只讀不改。

**作業人員安裝（Windows）**：先安裝官方 Python 3.11 以上（包含 Tcl/Tk），把專案放到自己有寫入權限的資料夾，雙擊 `setup_panel.cmd`。安裝會建立專案自己的 `.venv`，依 `constraints.txt` 安裝套件，需要能連到公司允許的 Python 套件來源。看到「安裝完成」後，雙擊桌面或專案資料夾裡的「TS IIS 準備與放回」捷徑開啟視窗；捷徑建立失敗時改雙擊 `launch_panel.cmd`。日常操作不用打指令；上次選的資料夾記在安裝資料夾的 `.local/`。搬移資料夾或更新程式後請重新執行安裝。Word 轉檔需要這台電腦裝有 Microsoft Word。

**開發者安裝**：

```bash
python -m pip install -c constraints.txt -e ".[dev]"
```

PostgreSQL 驅動已包含在套件裡。

開發時用指令開啟視窗：

```bash
spw-panel
```

1. 在輸入框貼上要核對的 TDCC（換行、空白、逗號、頓號都可以分隔；重複的只算一次）。
2. 選根目錄（商品資料夾可以在底下任何一層）與輸出位置；兩者都會記在本機 `.local/`，下次直接帶入。
3. 按「開始準備」。每次在輸出位置開一個 `待核對_<日期時間>` 資料夾，已存在就不做。
4. 看第一頁內容判斷：開頭附近出現「中文產品說明書」或「中文投資人須知」；說明書第一頁要有資料夾的 TDCC，投資人須知有 12 碼代號時要相符。檔名只當參考。
5. 有上手 PDF 就用 PDF；某種文件沒有 PDF 時（例如 MS、BNP 只給 Word），才把資料夾裡的 Word（`.docx`、`.doc`）複製到暫存區，用 Microsoft Word 轉成 PDF 再判斷（[ADR 0002](docs/adr/0002-word-to-pdf-with-microsoft-word.md)）。原始 Word 不動；轉檔失敗或逾時（Word 跳出對話框）時那個商品跳過。
6. 說明書或投資人須知任一份找不到、或有兩份以上候選時，整個商品跳過並寫出原因與候選檔名。
   **重新核對上手修正版**：資料夾裡上次放回的「_待確認」檔不當候選，只用來認出哪一版沒通過（比對全文）；內容相同的幾份只算一份。上手寄來新版時會挑新版，連上次已放回的標準名舊版也一併換掉（修正版沿用舊檔名、覆蓋了舊原檔也一樣：內容不同時上手檔名的版本優先，只以標準檔名存在的內容當成上次放回的舊版）；上手還沒寄新版時，沿用上次沒通過的那一版照常準備（原因寫「沿用上次沒通過的版本」；資料夾裡若另有封面商品代號不符或轉檔失敗的新檔，也會一起寫出），所以改過參考條件表或核對規則後，可以用同一版重複核對；同一版又沒通過時，錯訊和資料夾裡最新的錯訊檔不同才會再寫一份。準備清單的原因欄會寫出排除了哪些舊版。MS、BNP 這類只給 Word 的上手，只有沒通過的那份會換成新版；如果兩份都改了，請先把舊的標準名檔移到子資料夾。上次沒通過的是 PDF 時，修正版也要有 PDF 才會被挑到（例如 JPM 同時給 PDF 和 Word，修正版只有 Word 時請上手補 PDF）。
7. 批次資料夾裡的 `準備清單.xlsx` 記錄每份文件的來源檔、是否由 Word 轉檔、輸出檔名、SHA-256 指紋、狀態與原因。完成後按「打開批次資料夾」，再到核對工具 PANEL 選參考條件表與這批 PDF。

核對完、存下核對結果檔後，切到「放回資料夾」分頁：

1. 選批次資料夾（剛準備完會自動帶入）與核對 PANEL 存下的核對結果檔。
2. 按「放回資料夾」。核對結果檔「回填後」上 `TS/IIS完成檢核` 為 `V` 的商品（說明書與投資人須知都通過或人工放行）用標準檔名放回；「錯誤清單」上沒通過的那份，檔名最後加「_待確認」放回（例：`<TDCC>_TS_待確認.pdf`），同商品通過的那份仍用標準檔名，原因欄附上錯訊；同時在商品資料夾寫一份 `<TDCC>_錯誤訊息_<日期時間>.xlsx`（一條錯訊一列，附 PDF 頁碼，頁碼對應放回的「_待確認」檔），打開資料夾就看得到問題在哪裡。重複放回、待確認檔已經在時不會再寫。核對結果檔裡沒出現的商品（還沒核對）不放回。
3. 放回前重算批次裡 PDF 的指紋，和準備清單不符就不放。商品資料夾已有同名檔：內容相同（位元組相同，或全文相同，例如同一份 Word 重新轉出的 PDF）算「已在」（例如 UBS 本來就給標準檔名），內容不同不覆蓋、整個商品不放回，由作業人員決定留哪份。
4. 只複製，批次資料夾保留；結果寫在批次資料夾的 `放回清單_<日期時間>.xlsx`；這批準備了但還沒核對的商品也會列出，標「不放回」。修好未通過的商品、重新核對後可以再放一次，已放回的會顯示「已在」；改用標準檔名放回時，舊的「_待確認」檔和舊錯訊檔不會自動刪除，清單會提醒你確認後手動刪除。

### 每日匯入（最小 FCN 資料庫）

把每天的 FCN 交易與商品條件匯入 PostgreSQL；信箱在資料庫直接維護（[ADR 0004](docs/adr/0004-mailbox-maintained-in-database.md)、[#35](https://github.com/TaylorYam/structured-product-workflow/issues/35)）（[#32](https://github.com/TaylorYam/structured-product-workflow/issues/32)、[ADR 0003](docs/adr/0003-minimal-fcn-database.md)）。寄信草稿之後只讀這個資料庫。

**第一次設定（每台電腦一次）**：
1. 安裝 PostgreSQL，建立資料庫與日常使用的帳號（可用 `scripts/provision_postgres_dev.py`）。
2. 在安裝資料夾（開發時是 repo 根目錄）建立 `.env`，填 `PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER`；`PGPASSWORD` 可以不填，匯入時會詢問。`.env` 不進 Git。
3. 用有建立權限的帳號建立資料表，並把讀寫權限給 `PGUSER`：

```bash
spw-db-migrate --admin-user postgres
```

之後程式更新、有新的資料表版本時，再執行一次同樣的指令。換電腦（例如從筆電到公司電腦）時不複製資料庫，照上面三步重建，再把每日更新重新匯入。

**每天**：
1. 打開 `每日更新.xlsx`：把前一天的交易整列貼到 `交易明細`（欄位同交易清單月份工作表），這些商品在庫存總表的列整列貼到 `清單更新`（欄位同 `FCN清單.xlsx`）。第 1 列欄名不要動。
2. 視窗切到「每日匯入」，選 `每日更新.xlsx`，按「匯入」。
3. 看結果：
   - 「新增商品」「新增交易」：寫入成功。「已存在」：之前匯過，這次沒有寫入。
   - 「內容不同（未覆蓋）」：和資料庫裡的不一樣，資料庫保留第一次的內容。
   - 「擋下」：這一列缺值或寫法不認得（例如庫存狀態、KO／KI 代碼），修正 Excel 後重新匯入。
   - 「分公司對帳差異」：交易明細各分公司金額加總和庫存總表的承作金額不同（PB 也算），只提醒。
   - 「庫存尚未更新」：交易已匯入、清單更新還沒有這檔；之後貼進來再匯入一次即可。
   - 「名字對不到信箱」：交易明細的 FC 名字在 `mailbox.person` 找不到（大小寫不同視為同一人）；拼法不同請在 `mailbox.alias` 加一筆別名。
   - 「信箱尚未建立」：`mailbox.person` 還是空的，名字無法比對。

只收 FCN，其他商品略過並計數；同一份重複匯入不會重複寫入；任何錯誤都不會留下一半的資料。已存在商品的庫存狀態這一版不更新。每日匯入不會修改信箱。

**信箱維護**：直接在資料庫的 `mailbox.person`（人員）、`mailbox.alias`（別名）新增或修改，例如用 pgAdmin 以有寫入權限的帳號執行 SQL。比對用的小寫鍵由資料庫自動產生；名字不分大小寫不能重複，信箱格式不對、別名指到不存在的人都會被拒絕。改名時別名會跟著改。

```sql
-- 新增人員（fixed_cc = true 是寄信的固定副本）
INSERT INTO mailbox.person (name, email, unit, fixed_cc) VALUES ('Amy', 'amy@example.com', 'FC', false);
-- 交易清單上的拼法不同時，加別名指到使用者名稱
INSERT INTO mailbox.alias (alias, person_name) VALUES ('Amie', 'Amy');
-- 修改、刪除
UPDATE mailbox.person SET email = 'amy.new@example.com' WHERE name = 'Amy';
DELETE FROM mailbox.person WHERE name = 'Amy';  -- 她的別名會一起刪除
```

測試：`pytest -q`（資料全部虛構；真的 Word 轉檔測試只在本機有 Word 時執行，CI 自動略過）。資料庫測試需要一個名稱以 `_test` 結尾、可以清空的 PostgreSQL 資料庫，把連線字串設在環境變數 `SPW_TEST_DSN`（例如 `host=localhost dbname=spw_test user=...`）；沒設定時略過，CI 用 PostgreSQL 服務執行。真實樣本試跑只在本機執行：把環境變數 `SPW_REAL_SAMPLES_DIR` 設成放商品資料夾的目錄，CI 自動略過；真實資料不進 repo。

## English

Status: TS/IIS preparation and return is complete and trial-run on copies of real samples; operational shared-drive testing is pending ([#5](https://github.com/TaylorYam/structured-product-workflow/issues/5)). The minimal FCN database with daily import is [#32](https://github.com/TaylorYam/structured-product-workflow/issues/32); mail drafts have confirmed requirements and await implementation ([#29](https://github.com/TaylorYam/structured-product-workflow/issues/29), now reading the database). Other workflows remain in design.

This project coordinates formal Orders, booking, issuer document archiving, checker integration, and FC distribution. Inquiry remains in the existing system.
Checking rules belong to the independent [fcn-term-sheet-checker](https://github.com/TaylorYam/fcn-term-sheet-checker).

Read [business context](CONTEXT.md), [architecture](docs/architecture.md), then [open questions](docs/open-questions.md).
For persistence/deployment, read the [PostgreSQL decision](docs/adr/0001-postgresql-workflow-storage.md) and [development/deployment design](docs/database-and-deployment.md). First-phase data scope is in [ADR 0003](docs/adr/0003-minimal-fcn-database.md).
For integration, also read the [checker integration proposal](docs/checker-integration.md).

Created from TaylorYam/ai-project-template on 2026-10-18, retaining its issue, PR, CI, and agent workflows.
Commit documentation and synthetic examples only. Company messages, trades, customers, inventory, network files, and credentials remain in the company environment.

### TS/IIS batch preparation

Picks the Chinese term sheet and investor information sheet from TDCC-named product folders under the shared FCN directory, renames them to the checker's `<TDCC>_TS.pdf` and `<TDCC>_IIS.pdf`, and places them in a new batch folder. Product folders are read only.

**Operator setup (Windows)**: install official Python 3.11 or later (with Tcl/Tk), place the project in a folder you can write to, and double-click `setup_panel.cmd`. Setup creates the project's own `.venv` and installs pinned packages from `constraints.txt`, which requires access to the company-approved Python package source. After "安裝完成", double-click the "TS IIS 準備與放回" shortcut on the desktop or in the project folder; if shortcut creation failed, double-click `launch_panel.cmd`. Daily use needs no commands; the last chosen folders are remembered in the install folder's `.local/`. Rerun setup after moving the folder or updating the code. Word conversion requires Microsoft Word on this computer.

**Developer setup**:

```bash
python -m pip install -c constraints.txt -e ".[dev]"
```

The PostgreSQL driver is included in the package.

Open the window from a terminal during development:

```bash
spw-panel
```

1. Paste the TDCCs to check (newlines, spaces, commas, or ideographic commas; duplicates count once).
2. Choose the root folder (product folders may be at any depth) and the output location; both are remembered in local `.local/`.
3. Press Start. Each run creates `待核對_<timestamp>` in the output location and refuses an existing folder.
4. Identification uses page-one content: a 中文產品說明書 or 中文投資人須知 title near the top; the term sheet's first page must contain the folder TDCC, and an investor sheet's 12-digit code must match when present. File names are hints only.
5. Issuer PDFs take precedence. Only when a document type has no PDF (for example MS and BNP provide Word only) are the folder's Word files (`.docx`, `.doc`) copied to a temporary area and converted with Microsoft Word before identification ([ADR 0002](docs/adr/0002-word-to-pdf-with-microsoft-word.md)). Original Word files stay untouched; a failed or timed-out conversion (for example a Word dialog) skips that product.
6. If either document is missing or has multiple candidates, the whole product is skipped with the reason and candidate names.
   **Rechecking revised issuer documents**: `_待確認` files returned last time are never candidates and only identify which version failed (full-text comparison); identical copies count once. A newly sent version is picked, replacing even the previously returned standard-name version (also when the revision reuses the old file name and overwrites the old original: with differing contents the issuer-named version wins and content found only under the standard name is treated as the previous return); until the issuer sends one, the previously failed version is reused (the reason says so, and also lists new files that could not be used because of a cover-code mismatch or failed conversion), so the same version can be rechecked after fixing the reference workbook or checker rules; a new error workbook is written only when the errors differ from the latest one in the folder. The preparation list's reason column names the excluded versions. For Word-only issuers such as MS and BNP, only the failed document is replaced; if both changed, move the old standard-name file into a subfolder first. When the failed version was a PDF, the revision must also include a PDF to be picked (for example, ask JPM, which sends both PDF and Word, for a PDF when the revision is Word only).
7. `準備清單.xlsx` in the batch folder records each document's source, whether it was converted from Word, output name, SHA-256, status, and reason. Then open the batch folder and select the reference workbook and PDFs in the checker PANEL.

After checking and saving the checker result workbook, switch to the 放回資料夾 (return to folders) tab:

1. Choose the batch folder (filled in automatically after preparation) and the result workbook saved by the checker PANEL.
2. Press Return. Products whose `TS/IIS完成檢核` is `V` on the result workbook's 回填後 sheet (both documents passed or manually released) are returned under standard names. A document listed on the error-list sheet is returned with `_待確認` appended (for example `<TDCC>_TS_待確認.pdf`), while the product's passing document keeps its standard name and the reason column carries the error messages; a `<TDCC>_錯誤訊息_<timestamp>.xlsx` workbook (one error per row with the PDF page numbers, which refer to the returned `_待確認` file) is also written to the product folder so operators can see the problems there. It is not written again when the pending files are already in place. Products absent from the result workbook (not yet checked) are not returned.
3. Batch PDFs are re-hashed and must match the preparation list. When a product folder already has a same-named file, identical content (same bytes, or the same full text such as a PDF re-converted from the same Word file) counts as already there (for example UBS issuing standard names); different content is never overwritten and the whole product is not returned, leaving the decision to the operator.
4. Files are copied only and the batch folder is kept; results go to `放回清單_<timestamp>.xlsx` in the batch folder, which also lists prepared products not yet checked as not returned. After fixing and rechecking failed products, returning again marks already returned files as already there; when a standard name is returned, old `_待確認` files and error workbooks are never deleted automatically and the list reminds you to remove them after review.

### Daily import (minimal FCN database)

Imports each day's FCN trades and product terms into PostgreSQL; the mailbox is maintained directly in the database ([ADR 0004](docs/adr/0004-mailbox-maintained-in-database.md), [#35](https://github.com/TaylorYam/structured-product-workflow/issues/35)) ([#32](https://github.com/TaylorYam/structured-product-workflow/issues/32), [ADR 0003](docs/adr/0003-minimal-fcn-database.md)). Mail drafts will read only this database.

**First-time setup (once per computer)**:
1. Install PostgreSQL and create the database and the daily account (`scripts/provision_postgres_dev.py` can help).
2. Create `.env` in the install folder (the repository root during development) with `PGHOST`, `PGPORT`, `PGDATABASE`, and `PGUSER`; `PGPASSWORD` is optional and is asked for at import time. `.env` never enters Git.
3. Create the tables with an account that can create schemas, granting read/write to `PGUSER`:

```bash
spw-db-migrate --admin-user postgres
```

Run the same command again after updates that add schema versions. When moving to another computer (for example from the laptop to the company computer), do not copy the database: repeat the three steps and re-import the daily updates.

**Daily**:
1. Open `每日更新.xlsx`: paste yesterday's trade rows whole into `交易明細` (same columns as the trade-list monthly sheets) and those products' inventory rows whole into `清單更新` (same columns as `FCN清單.xlsx`). Do not change the header row.
2. In the window, open "每日匯入", choose `每日更新.xlsx`, and click "匯入".
3. Review the results:
   - "新增商品"/"新增交易": written. "已存在": imported before; nothing written this time.
   - "內容不同（未覆蓋）": differs from the database, which keeps the first version.
   - "擋下": a row is missing values or uses an unrecognized form (for example inventory status or KO/KI codes); fix the workbook and re-import.
   - "分公司對帳差異": per-branch trade totals differ from the inventory allocation (PB included); warning only.
   - "庫存尚未更新": trades are imported but `清單更新` lacks the product; paste it later and import again.
   - "名字對不到信箱": an FC name is missing from `mailbox.person` (case is ignored); for spelling variants add an alias to `mailbox.alias`.
   - "信箱尚未建立": `mailbox.person` is empty, so names cannot be matched.

Only FCN is imported; other products are skipped and counted. Re-importing the same file writes nothing new, and any error leaves no partial data. Inventory status of existing products is not refreshed in this version. The daily import never changes the mailbox.

**Mailbox maintenance**: add or edit rows directly in `mailbox.person` (people) and `mailbox.alias` (aliases), for example with SQL in pgAdmin using an account with write access. The lowercase matching keys are generated by the database; names must be unique ignoring case, and invalid email formats or aliases pointing to unknown people are rejected. Renaming a person updates their aliases.

```sql
-- Add a person (fixed_cc = true marks a fixed mail CC)
INSERT INTO mailbox.person (name, email, unit, fixed_cc) VALUES ('Amy', 'amy@example.com', 'FC', false);
-- Add an alias when the trade list spells the name differently
INSERT INTO mailbox.alias (alias, person_name) VALUES ('Amie', 'Amy');
-- Update or delete
UPDATE mailbox.person SET email = 'amy.new@example.com' WHERE name = 'Amy';
DELETE FROM mailbox.person WHERE name = 'Amy';  -- their aliases are deleted too
```

Tests: `pytest -q` (synthetic data only; real Word conversion tests run locally only when Word is available and are skipped in CI). Database tests need a disposable PostgreSQL database whose name ends in `_test`; put its connection string in `SPW_TEST_DSN` (for example `host=localhost dbname=spw_test user=...`). Without it they are skipped; CI runs them against a PostgreSQL service. Real-sample runs are local only: set `SPW_REAL_SAMPLES_DIR` to the folder containing product folders; CI skips it, and real data never enters the repository.
