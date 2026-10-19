# 0002: Word 轉 PDF 使用 Microsoft Word／Convert Word to PDF with Microsoft Word

- 狀態／Status: Accepted（#8 實作／implemented in #8）
- 日期／Date: 2026-10-19
- 追蹤／Tracking: #5、#8

## 繁體中文

### 背景

部分上手只提供 Word 版的中文產品說明書與投資人須知（目前是 MS 的 `.docx` 與 BNP 的舊版 `.DOC`），核對工具只接受 PDF。轉出的 PDF 要交給 fcn-term-sheet-checker 依各上手範本解析；換行、表格、字型與頁數只要和範本導入時的樣本不同，就可能讀錯或轉人工。核對專案現有的 MS、BNP 樣本 PDF，產生程式都是 Microsoft Word LTSC。作業電腦是公司 Windows，已安裝 Microsoft Office。

### 決策

[TS／IIS 準備工具（#5）](https://github.com/TaylorYam/structured-product-workflow/issues/5) 需要轉檔時，以 pywin32 在背景控制 Microsoft Word 開檔並另存 PDF。只有同一種文件沒有上手 PDF 時才轉；有上手 PDF 一律用上手 PDF。轉檔先複製到暫存區，原始 Word 不動；不允許 Word 跳出對話框卡住，逾時或失敗即視為該份轉檔失敗。轉檔包成可替換的轉檔器，測試以假的轉檔器替換。

### 考慮過的替代方案

- LibreOffice：免費、可在 Linux 伺服器跑；但中文字型替換、表格換行與分頁可能和 Word 不同，MS、BNP 的核對範本要重新驗證，且差異可能只在少數商品才出現。
- 由作業人員手動另存 PDF：不需新相依，但每天重複操作且容易漏轉，違背自動準備的目的。
- 一律用 Word 轉檔、不用上手 PDF：版面來源一致，但會和上手正式 PDF 及核對範本的樣本來源不同。

### 後果

- 轉檔只能在安裝 Microsoft Word 的 Windows 上執行；CI（Linux）只跑假轉檔器的測試，真的 Word 轉檔只在本機有 Word 時測。
- 背景控制 Word 可能被巨集、保護檢視、損毀檔或授權視窗卡住，需要逾時與失敗處理。
- 與 [ADR 0001](0001-postgresql-workflow-storage.md) 提到的伺服器方向有張力：桌面 Word 自動化不能假設可原樣搬到背景伺服器。日後要在伺服器上轉檔，需新增 ADR 取代本決策，並重新驗證核對範本。

## English

### Context

Some issuers provide the Chinese term sheet and investor information sheet only as Word files (currently MS `.docx` and legacy BNP `.DOC`), while the checker accepts only PDFs. The converted PDFs are parsed by fcn-term-sheet-checker against issuer templates; differences in line breaks, tables, fonts, or page count from the onboarding samples may cause misreads or manual review. The checker's existing MS and BNP sample PDFs were all produced by Microsoft Word LTSC. Operational machines are company Windows computers with Microsoft Office installed.

### Decision

When the [TS/IIS preparation tool (#5)](https://github.com/TaylorYam/structured-product-workflow/issues/5) needs conversion, it drives Microsoft Word in the background through pywin32 and saves the document as PDF. Conversion happens only when no issuer PDF exists for that document type; issuer PDFs always take precedence. Sources are copied to a temporary area first and original Word files stay untouched. Word dialogs must not block; a timeout or failure marks that document's conversion as failed. Conversion sits behind a replaceable converter, and tests use a fake converter.

### Alternatives considered

- LibreOffice: free and runs on Linux servers, but Chinese font substitution, table wrapping, and pagination may differ from Word. MS and BNP templates would need revalidation, and differences may surface only on a few products.
- Manual Save-as-PDF by operators: no new dependency, but repetitive daily work that is easily missed, defeating automated preparation.
- Always convert Word and ignore issuer PDFs: one layout source, but it diverges from issuers' official PDFs and the checker templates' sample sources.

### Consequences

- Conversion runs only on Windows with Microsoft Word installed. CI (Linux) runs fake-converter tests only; real Word conversion is tested locally when Word is available.
- Background Word automation can be blocked by macros, Protected View, corrupt files, or licensing prompts, so timeout and failure handling are required.
- This is in tension with the server direction noted in [ADR 0001](0001-postgresql-workflow-storage.md): desktop Word automation cannot be assumed to move unchanged to a background server. Server-side conversion would require a superseding ADR and checker-template revalidation.
