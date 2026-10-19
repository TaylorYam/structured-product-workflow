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

安裝（Python ≥ 3.11，含 Tkinter；雙擊安裝另見 #10）：

```bash
python -m pip install -c constraints.txt -e ".[dev]"
```

開啟視窗：

```bash
spw-panel
```

1. 在輸入框貼上要核對的 TDCC（換行、空白、逗號、頓號都可以分隔；重複的只算一次）。
2. 選根目錄（商品資料夾可以在底下任何一層）與輸出位置；兩者都會記在本機 `.local/`，下次直接帶入。
3. 按「開始準備」。每次在輸出位置開一個 `待核對_<日期時間>` 資料夾，已存在就不做。
4. 看第一頁內容判斷：開頭附近出現「中文產品說明書」或「中文投資人須知」；說明書第一頁要有資料夾的 TDCC，投資人須知有 12 碼代號時要相符。檔名只當參考。
5. 說明書或投資人須知任一份找不到、或有兩份以上候選時，整個商品跳過並寫出原因與候選檔名。這一版只處理上手 PDF，只有 Word 的商品（例如 MS、BNP）先跳過（#8 會處理轉檔）。
6. 批次資料夾裡的 `準備清單.xlsx` 記錄每份文件的來源檔、輸出檔名、SHA-256 指紋、狀態與原因。完成後按「打開批次資料夾」，再到核對工具 PANEL 選參考條件表與這批 PDF。

測試：`pytest -q`（資料全部虛構）。真實樣本試跑只在本機執行：把環境變數 `SPW_REAL_SAMPLES_DIR` 設成放商品資料夾的目錄，CI 自動略過；真實資料不進 repo。

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

Install (Python ≥ 3.11 with Tkinter; double-click setup is tracked in #10):

```bash
python -m pip install -c constraints.txt -e ".[dev]"
```

Open the window:

```bash
spw-panel
```

1. Paste the TDCCs to check (newlines, spaces, commas, or ideographic commas; duplicates count once).
2. Choose the root folder (product folders may be at any depth) and the output location; both are remembered in local `.local/`.
3. Press Start. Each run creates `待核對_<timestamp>` in the output location and refuses an existing folder.
4. Identification uses page-one content: a 中文產品說明書 or 中文投資人須知 title near the top; the term sheet's first page must contain the folder TDCC, and an investor sheet's 12-digit code must match when present. File names are hints only.
5. If either document is missing or has multiple candidates, the whole product is skipped with the reason and candidate names. This version handles issuer PDFs only; Word-only products (for example MS, BNP) are skipped until #8 adds conversion.
6. `準備清單.xlsx` in the batch folder records each document's source, output name, SHA-256, status, and reason. Then open the batch folder and select the reference workbook and PDFs in the checker PANEL.

Tests: `pytest -q` (synthetic data only). Real-sample runs are local only: set `SPW_REAL_SAMPLES_DIR` to the folder containing product folders; CI skips it, and real data never enters the repository.
