# 業務背景／Business Context

## 繁體中文

來源：使用者截至 2026-10-18 的架構討論。本文件保存使用者已確認的業務需求；實作方案的建議與未決問題見其他文件。

- 詢價：已有半自動化系統，不納入本專案。
- 起單：以正式 Order Outlook 郵件把條件下給交易室。
- 建檔：依正式 Order 條件建檔；各家格式不同，希望歸一化並自動歸檔。
- 原始歸檔：上手通常隔天寄 TS／IIS 及其他文件。目前人工存入公槽 FCN 下以 TDCC 命名的資料夾，附件數量多。
- 核對：從商品資料夾辨識 TS／IIS，原檔名可能是亂碼；Word 需轉 PDF，標準命名後批次核對並歸檔。
- 寄送：把已核對 TS／IIS 寄給有承作該商品的 FC（輔銷人員），由 FC 再寄給顧客。郵件取庫存總表指定欄位，套用固定表格；庫存總表不是核對用參考條件表。
- 產品：目前優先 Vanilla FCN，使用者估計占約 85%；未來希望擴展 EXPRESS FCN、BEN 等。高度客製化產品可選人工核對。
- 專案分工：現有 fcn-term-sheet-checker 專心處理核對；另建本專案承載整體流程。
- 資料管理方向：採用 PostgreSQL；先本機開發，未來希望由公司伺服器支援兩到三人接手協作，避免依賴個人電腦。筆電與作業主機的開發／正式資料分開；亦希望分析四年 FCN 並匯出 Excel。選型與限制見 [ADR 0001](docs/adr/0001-postgresql-workflow-storage.md)。
- 當前授權：建立專案與保存設計文件。業務程式、真實信箱操作、寄信及公司資料處理尚未開始。

名詞：Order 指正式交易指示；TS 指產品說明書；IIS 指投資人須知；TDCC 是商品配對代號；FC 指輔銷人員。
「原始歸檔」保存收到的文件；「確認版本歸檔」標記經核對、可供寄送的確切文件版本。
「商品資料夾」指公槽 FCN 下名稱剛好是 TDCC 的資料夾，存放該商品的原始歸檔；「批次資料夾」是每次準備核對時另開的資料夾，放這批商品標準命名的 TS／IIS PDF；「準備清單」記錄批次中每份 PDF 的來源檔與指紋；「放回清單」記錄核對後哪些 TS／IIS 已放回商品資料夾：通過的用標準檔名，沒通過的檔名加「_待確認」。「錯訊檔」是放回待確認檔時一起寫進商品資料夾的 `<TDCC>_錯誤訊息_<日期時間>.xlsx`，列出沒通過的原因。

## English

Source: user architecture discussions through 2026-10-18. This document records confirmed business needs; implementation recommendations and unresolved matters are documented separately.

- Inquiry: an existing semi-automated system handles it; excluded here.
- Order placement: send formal Order terms to the trading desk through Outlook.
- Booking: normalize differing issuer Order formats into common records and archive their sources.
- Incoming archive: issuers usually send TS/IIS and other files the next day. Staff currently archive numerous attachments under TDCC-named folders in the shared FCN directory.
- Checking: identify TS/IIS despite opaque filenames, convert Word to PDF, standardize names, batch-check, and archive.
- Distribution: send checked TS/IIS to FCs handling the product; FCs forward to customers. Build a fixed email table from selected inventory-master columns. The inventory master is separate from the checking reference workbook.
- Products: prioritize Vanilla FCN, estimated by the user at approximately 85%; later expand to EXPRESS FCN, BEN, and others. Highly customized products may use manual checking.
- Ownership: fcn-term-sheet-checker remains focused on checking; this new repository owns the overall workflow.
- Data direction: select PostgreSQL, develop locally first, then target a company server for two or three collaborators independent of a personal computer. Separate laptop development and operational data; support future four-year FCN analysis and Excel export. See [ADR 0001](docs/adr/0001-postgresql-workflow-storage.md).
- Current authorization: create the project and preserve design documentation. Business implementation, actual mailbox operations, sending, and company-data processing have not started.

Terms: Order means formal trading instructions; TS means term sheet; IIS means investor information sheet; TDCC identifies a product; FC means sales support personnel.
Incoming archiving preserves received sources; approved-version archiving identifies the exact reviewed files eligible for distribution.
A product folder is the shared-FCN folder named exactly by a TDCC and holding that product's incoming archive. A batch folder is created for each checking preparation and holds that batch's standard-named TS/IIS PDFs. The preparation list records each batch PDF's source file and hash; the return list records which checked TS/IIS files were copied back into product folders, passing files under standard names and failed ones with `_待確認` appended. The error workbook is the `<TDCC>_錯誤訊息_<timestamp>.xlsx` written into the product folder alongside pending files, listing why they failed.
