# Structured Product Workflow／結構型商品作業流程

## 繁體中文

狀態：第一個功能「TS／IIS 批次準備」開發中（[#5](https://github.com/TaylorYam/structured-product-workflow/issues/5)）；其他流程仍在設計階段。

本專案管理正式 Order、建檔、上手文件歸檔、核對串接及 FC 寄送。詢價端沿用既有系統。
核對規則由獨立的 [fcn-term-sheet-checker](https://github.com/TaylorYam/fcn-term-sheet-checker) 負責。

閱讀順序：[業務背景](CONTEXT.md) → [架構](docs/architecture.md) → [待定事項](docs/open-questions.md)。
資料保存與部署先讀 [PostgreSQL 決策](docs/adr/0001-postgresql-workflow-storage.md) 與 [開發部署設計](docs/database-and-deployment.md)。選型已確定，尚未實作。
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
7. 批次資料夾裡的 `準備清單.xlsx` 記錄每份文件的來源檔、是否由 Word 轉檔、輸出檔名、SHA-256 指紋、狀態與原因。完成後按「打開批次資料夾」，再到核對工具 PANEL 選參考條件表與這批 PDF。

核對完、存下核對結果檔後，切到「放回資料夾」分頁：

1. 選批次資料夾（剛準備完會自動帶入）與核對 PANEL 存下的核對結果檔。
2. 按「放回資料夾」。核對結果檔「回填後」上 `TS/IIS完成檢核` 為 `V` 的商品（說明書與投資人須知都通過或人工放行）用標準檔名放回；「錯誤清單」上沒通過的那份，檔名最後加「_待確認」放回（例：`<TDCC>_TS_待確認.pdf`），同商品通過的那份仍用標準檔名，原因欄附上錯訊。核對結果檔裡沒出現的商品（還沒核對）不放回。
3. 放回前重算批次裡 PDF 的指紋，和準備清單不符就不放。商品資料夾已有同名檔：內容相同算「已在」（例如 UBS 本來就給標準檔名），內容不同不覆蓋、整個商品不放回，由作業人員決定留哪份。
4. 只複製，批次資料夾保留；結果寫在批次資料夾的 `放回清單_<日期時間>.xlsx`；這批準備了但還沒核對的商品也會列出，標「不放回」。修好未通過的商品、重新核對後可以再放一次，已放回的會顯示「已在」；改用標準檔名放回時，舊的「_待確認」檔不會自動刪除，清單會提醒你確認後手動刪除。

測試：`pytest -q`（資料全部虛構；真的 Word 轉檔測試只在本機有 Word 時執行，CI 自動略過）。真實樣本試跑只在本機執行：把環境變數 `SPW_REAL_SAMPLES_DIR` 設成放商品資料夾的目錄，CI 自動略過；真實資料不進 repo。

## English

Status: the first feature, TS/IIS batch preparation, is under development ([#5](https://github.com/TaylorYam/structured-product-workflow/issues/5)); other workflows remain in design.

This project coordinates formal Orders, booking, issuer document archiving, checker integration, and FC distribution. Inquiry remains in the existing system.
Checking rules belong to the independent [fcn-term-sheet-checker](https://github.com/TaylorYam/fcn-term-sheet-checker).

Read [business context](CONTEXT.md), [architecture](docs/architecture.md), then [open questions](docs/open-questions.md).
For persistence/deployment, read the [PostgreSQL decision](docs/adr/0001-postgresql-workflow-storage.md) and [development/deployment design](docs/database-and-deployment.md). Selection is settled; implementation has not started.
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
7. `準備清單.xlsx` in the batch folder records each document's source, whether it was converted from Word, output name, SHA-256, status, and reason. Then open the batch folder and select the reference workbook and PDFs in the checker PANEL.

After checking and saving the checker result workbook, switch to the 放回資料夾 (return to folders) tab:

1. Choose the batch folder (filled in automatically after preparation) and the result workbook saved by the checker PANEL.
2. Press Return. Products whose `TS/IIS完成檢核` is `V` on the result workbook's 回填後 sheet (both documents passed or manually released) are returned under standard names. A document listed on the error-list sheet is returned with `_待確認` appended (for example `<TDCC>_TS_待確認.pdf`), while the product's passing document keeps its standard name and the reason column carries the error messages. Products absent from the result workbook (not yet checked) are not returned.
3. Batch PDFs are re-hashed and must match the preparation list. When a product folder already has a same-named file, identical content counts as already there (for example UBS issuing standard names); different content is never overwritten and the whole product is not returned, leaving the decision to the operator.
4. Files are copied only and the batch folder is kept; results go to `放回清單_<timestamp>.xlsx` in the batch folder, which also lists prepared products not yet checked as not returned. After fixing and rechecking failed products, returning again marks already returned files as already there; when a standard name is returned, an old `_待確認` file is never deleted automatically and the list reminds you to remove it after review.

Tests: `pytest -q` (synthetic data only; real Word conversion tests run locally only when Word is available and are skipped in CI). Real-sample runs are local only: set `SPW_REAL_SAMPLES_DIR` to the folder containing product folders; CI skips it, and real data never enters the repository.
