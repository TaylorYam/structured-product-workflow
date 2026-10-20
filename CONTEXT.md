# 業務背景／Business Context

## 繁體中文

來源：使用者截至 2026-10-18 的架構討論。本文件保存使用者已確認的業務需求；實作方案的建議與未決問題見其他文件。

- 詢價：已有半自動化系統，不納入本專案。
- 起單：以正式 Order Outlook 郵件把條件下給交易室。
- 建檔：依正式 Order 條件建檔；各家格式不同，希望歸一化並自動歸檔。
- 原始歸檔：上手通常隔天寄 TS／IIS 及其他文件。目前人工存入公槽 FCN 下以 TDCC 命名的資料夾，附件數量多。
- 核對：從商品資料夾辨識 TS／IIS，原檔名可能是亂碼；Word 需轉 PDF，標準命名後批次核對並歸檔。
- 寄送：把已核對 TS／IIS 寄給有承作該商品的 FC（輔銷人員），由 FC 再寄給顧客。郵件取庫存總表指定欄位，套用固定表格；庫存總表不是核對用參考條件表。2026-10-19 確認第一版：只建傳統版 Outlook 草稿、由人寄出；手動指定 TDCC；只做 FCN；收件人為交易清單上該 TDCC 的 FC（略過 PB），副本由信箱表標記；一檔一封。細節見 [#29](https://github.com/TaylorYam/structured-product-workflow/issues/29)。
- 產品：目前優先 Vanilla FCN，使用者估計占約 85%；未來希望擴展 EXPRESS FCN、BEN 等。高度客製化產品可選人工核對。
- 專案分工：現有 fcn-term-sheet-checker 專心處理核對；另建本專案承載整體流程。2026-10-20 決定把核對專案連同歷史併入本專案（`src/fcn_checker/`），先合併、再做新資料流程；舊 repo 封存，`fcn-oos-samples` 維持獨立私人（[ADR 0005](docs/adr/0005-merge-checker-into-workflow.md)）。
- 新商品資料來源（2026-10-20 確認，[ADR 0006](docs/adr/0006-new-product-data-from-order.md)）：不再用庫存總表，改由三個來源建立：交易明細（承作金額、分公司配置）、作業人員確認後匯入的 Order 條件（條件、期初定價、標的代碼以它為準）、隔天早上的股價（進場價 = 交易日開盤或收盤價；執行價／下限價／KO 價 = 進場價 × K%／KI%／KO%，四捨五入到小數第 4 位）。核對從資料庫讀條件，核對完直接回填 ISIN、發行日、比價日與 VWAP 價格；TS 核對通過即 Order 條件的第二次驗證。Order 條件在核對通過前重匯就覆蓋並留紀錄，通過後鎖住。交易明細與 Order 的金額、幣別、交易日、UF 不一致標待確認。過渡期清單更新只比對不覆蓋、Excel 參考條件表保留、寄信草稿維持有 PDF 即可建、進度分頁等合成一個 PANEL 再做。股價取得方式、單位面額來源、Order 沒寫最終比價日／到期日的處理待定。
- 資料管理方向：採用 PostgreSQL；先本機開發，未來希望由公司伺服器支援兩到三人接手協作，避免依賴個人電腦。筆電與作業主機的開發／正式資料分開；亦希望分析四年 FCN 並匯出 Excel。選型與限制見 [ADR 0001](docs/adr/0001-postgresql-workflow-storage.md)。
- Order 條件讀取：2026-10-20 確認第一版以我方寄出的 `(Order)` 原信為準（上手回覆信不讀），從商品資料夾的 `.msg` 讀；每家上手一套固定讀法、不用 AI，讀不到留空並標示；信裡全部欄位都讀出並統一欄名，輸出 `Order條件清單.xlsx`，不寫資料庫。同日確認清單第一張排成作業人員既有的「Outlook orders 轉成 Excel」格式，起單FC 留空；同日改為待確認的商品也照樣放，有問題的格子標淺紅底。GS、HSBC 的 Order 沒寫的日期，分別到 GS「Indicative Docs」信與 Order 信下方引用的 HSBC 回覆補（只補日期）。細節見 [#40](https://github.com/TaylorYam/structured-product-workflow/issues/40)。
- Order 讀法原則（2026-10-20 確認）：不過度客製；格式明顯偏離常態的信（例如一封信下多檔又沒寫清楚哪段是哪檔）接受人工回填，程式標待確認、問題格子淺紅底即可。同日確認例外：交易室多檔下單常只寫「1.」「2.」分段，依 We buy 行的 TDCC 順序對應，但段數與每段條件表要剛好對得上；發行日不從偏移天數推算。程式結果已用 10 月清單逐欄核對（只放私人樣本倉庫）。同一張條件表放好幾檔（沒有 TDCC 欄）的不核對，交給人工回填。
- 當前授權：TS／IIS 準備與放回、最小 FCN 資料庫與每日匯入（#32）、寄信草稿（#29，只建草稿、不寄出）、Order 條件讀取（#40，只輸出清單）已實作。2026-10-20 授權規劃合併 repo 與新商品資料流程（ADR 0005、0006），依 Issue 逐步實作；自動寄信等其他流程仍需另行授權。

名詞：Order 指正式交易指示；TS 指產品說明書；IIS 指投資人須知；TDCC 是商品配對代號；FC 指輔銷人員。

核對名詞（完整定義見 [核對名詞表](docs/checker/glossary.md)）：「上手編號」是 TDCC 前三碼，決定用哪家上手的範本；「參考條件表」是核對條件的 Excel 來源，只看第一張工作表、依欄名找欄；「必要欄」少一欄整批不核對；「回填」是把說明書讀出的 ISIN、發行日、比價日（VWAP 商品另含價格）填進核對結果；「人工放行」是作業人員看過錯訊、認定可以通過；「錯訊」是一條中文的不一致說明，寫出兩邊各是多少；「核對結果檔」有「回填後」與「錯誤清單」兩個工作表；「核對紀錄」是每次儲存寫的內部追查紀錄。

「原始歸檔」保存收到的文件；「確認版本歸檔」標記經核對、可供寄送的確切文件版本。
「商品資料夾」指公槽 FCN 下名稱剛好是 TDCC 的資料夾，存放該商品的原始歸檔；「批次資料夾」是每次準備核對時另開的資料夾，放這批商品標準命名的 TS／IIS PDF；「準備清單」記錄批次中每份 PDF 的來源檔與指紋；「放回清單」記錄核對後哪些 TS／IIS 已放回商品資料夾：通過的用標準檔名，沒通過的檔名加「_待確認」。「錯訊檔」是放回待確認檔時一起寫進商品資料夾的 `<TDCC>_錯誤訊息_<日期時間>.xlsx`，列出沒通過的原因。「沒通過的版本」指和商品資料夾裡某個「_待確認」檔內容相同的上手文件；上手寄來修正版時，重新準備會排除它；還沒有修正版時沿用它，可以重複核對。

## English

Source: user architecture discussions through 2026-10-18. This document records confirmed business needs; implementation recommendations and unresolved matters are documented separately.

- Inquiry: an existing semi-automated system handles it; excluded here.
- Order placement: send formal Order terms to the trading desk through Outlook.
- Booking: normalize differing issuer Order formats into common records and archive their sources.
- Incoming archive: issuers usually send TS/IIS and other files the next day. Staff currently archive numerous attachments under TDCC-named folders in the shared FCN directory.
- Checking: identify TS/IIS despite opaque filenames, convert Word to PDF, standardize names, batch-check, and archive.
- Distribution: send checked TS/IIS to FCs handling the product; FCs forward to customers. Build a fixed email table from selected inventory-master columns. The inventory master is separate from the checking reference workbook. First version confirmed 2026-10-19: create classic-Outlook drafts only and let a person send them; operator-specified TDCCs; FCN only; recipients are the trade list's FCs for that TDCC (PB skipped), with CCs flagged in the mailbox workbook; one message per product. See [#29](https://github.com/TaylorYam/structured-product-workflow/issues/29).
- Products: prioritize Vanilla FCN, estimated by the user at approximately 85%; later expand to EXPRESS FCN, BEN, and others. Highly customized products may use manual checking.
- Ownership: fcn-term-sheet-checker remains focused on checking; this new repository owns the overall workflow. On 2026-10-20 the user decided to merge the checker, with its history, into this repository (`src/fcn_checker/`), merging first and building the new data flow afterward; the old repository will be archived and `fcn-oos-samples` stays separate and private ([ADR 0005](docs/adr/0005-merge-checker-into-workflow.md)).
- New product data sources (confirmed 2026-10-20, [ADR 0006](docs/adr/0006-new-product-data-from-order.md)): instead of the inventory master, use three sources: trade rows (amounts, branch allocations), Order terms imported after operator review (authoritative for terms, initial pricing, and underlying codes), and next-morning prices (initial price = trade-date open or close; strike/KI/KO prices = initial price × K%/KI%/KO%, rounded half-up to 4 decimals). Checking reads terms from the database and backfills ISIN, issue date, observation dates, and VWAP prices directly; a passing TS check is a second verification of the Order terms. Before the check passes, re-imported Order terms overwrite with a record; afterward they lock. Differences between trade rows and the Order in amount, currency, trade date, or UF are marked 待確認. During the transition 清單更新 is compared without overwriting, the Excel reference workbook stays, mail drafts still need only PDFs, and the progress tab waits for the single PANEL. Price acquisition, the denomination source, and Orders missing the final valuation or maturity date remain open.
- Data direction: select PostgreSQL, develop locally first, then target a company server for two or three collaborators independent of a personal computer. Separate laptop development and operational data; support future four-year FCN analysis and Excel export. See [ADR 0001](docs/adr/0001-postgresql-workflow-storage.md).
- Order term reading: the first version confirmed 2026-10-20 uses our original `(Order)` email (issuer replies are not read), read from the product folder's `.msg` files; each issuer has a fixed rule-based format without AI, and unreadable fields stay blank with a reason; every field is read and normalized into `Order條件清單.xlsx`, without database writes. Also confirmed that day: the first sheet follows the operators' existing "Outlook orders to Excel" layout, with 起單FC left blank; later that day changed to include 待確認 products too, shading problem cells light red. Dates missing from GS and HSBC orders come from GS's "Indicative Docs" email and HSBC's reply quoted below the order (dates only). See [#40](https://github.com/TaylorYam/structured-product-workflow/issues/40).
- Order reading principle (confirmed 2026-10-20): avoid over-customizing. Emails that stray far from the normal format (for example several TDCCs without saying which section is which) are filled in manually; the program only marks them 待確認 with light-red problem cells. Exception confirmed the same day: the trading desk often numbers multi-TDCC sections only as "1." and "2."; these follow the We buy TDCC order only when the section count and one table per section match exactly. Issue dates are never inferred from the offset. Results were checked field by field against the October list (kept only in the private samples repository). A single terms table holding several products without a TDCC column is not matched and is filled in manually.
- Current authorization: TS/IIS preparation and return, the minimal FCN database with daily import (#32), mail drafts (#29, drafts only, no sending), and Order term reading (#40, list output only) are implemented. On 2026-10-20 the repository merge and new product data flow (ADR 0005, 0006) were authorized for planning and step-by-step implementation through issues; automatic sending and other workflows require separate authorization.

Terms: Order means formal trading instructions; TS means term sheet; IIS means investor information sheet; TDCC identifies a product; FC means sales support personnel.

Checker terms (full definitions in the [checker glossary](docs/checker/glossary.md), Chinese only): the issuer prefix (上手編號) is the TDCC's first three digits and selects the issuer template; the reference workbook (參考條件表) is the Excel source of checking terms, read from its first sheet by column name; a missing required column (必要欄) stops the whole batch; backfill (回填) writes the ISIN, issue date, and observation dates (plus prices for VWAP products) read from the term sheet into the result; manual release (人工放行) means an operator reviewed the errors and accepts the document; an error message (錯訊) is one Chinese line stating both sides of a mismatch; the result workbook (核對結果檔) has the 回填後 and 錯誤清單 sheets; the check record (核對紀錄) is the internal audit record written on each save.

Incoming archiving preserves received sources; approved-version archiving identifies the exact reviewed files eligible for distribution.
A product folder is the shared-FCN folder named exactly by a TDCC and holding that product's incoming archive. A batch folder is created for each checking preparation and holds that batch's standard-named TS/IIS PDFs. The preparation list records each batch PDF's source file and hash; the return list records which checked TS/IIS files were copied back into product folders, passing files under standard names and failed ones with `_待確認` appended. The error workbook is the `<TDCC>_錯誤訊息_<timestamp>.xlsx` written into the product folder alongside pending files, listing why they failed. A previously failed version is an issuer document whose content matches a `_待確認` file in the product folder; re-preparation excludes it once a revision arrives and reuses it until then, so it can be rechecked.
