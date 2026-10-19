# 待定事項／Open Questions

## 繁體中文

以下尚未決定，不應由 Agent 當作既定需求：

- 起單與建檔的正式確認時點、Order 修訂優先順序、各家格式及人工確認方式。
- TDCC 取得時點、資料夾結構、候選附件配對與正式版本選擇規則。
- 掃描文件處理、範本及座標解析相容性。Word 轉檔工具已選 Microsoft Word（[ADR 0002](adr/0002-word-to-pdf-with-microsoft-word.md)）。
- 回填 Excel 的實際目的地、庫存匯入由誰執行、總表結構與讀取時點。
- FC 與 Email 對照來源、同商品多列彙整、郵件欄位及一檔一封或逐 FC 寄送。
- Outlook 傳統 COM 或 Microsoft Graph；草稿或直接寄送；權限、重複寄送與不確定結果的處理。草稿優先目前只是建議。
- PostgreSQL 已選定，本機開發後目標為公司伺服器協作。仍待定：schema、主要版本、遷移工具、正式主機與 IT 核准、共享服務介面、權限、備份還原與保存期限、案件及狀態名稱。見 [資料庫與部署](database-and-deployment.md)。
- CLI 串接正式契約與支援版本；EXPRESS FCN／BEN 規則歸屬與驗證範圍。
- 正式 Order 歸一化是否需要 AI；範本中的 AI 指輔助開發，並不代表 runtime 必須使用 AI。既有核對專案採規則式 runtime；其限制不自動等同整個新專案的已決政策。

新增最小切片建議：本機 PostgreSQL＋虛構案件／版本資料＋一次核對結果入庫，尚未授權實作。歷史分析資料盤點、庫存 Excel 的正式來源切換與匯入規則另待確認。

建議開發順序：驗證 Order／附件樣本與商品身分 → 文件準備及核對串接 → 歸檔與庫存 → 郵件草稿 → 評估自動寄送。此順序是建議，尚未承諾實作。

## English

Unresolved items, not established requirements:

- Formal confirmation points for placement/booking, Order revision precedence, issuer formats, and human confirmation.
- TDCC availability, folder hierarchy, attachment matching, and official-version selection.
- Scanned documents, template and coordinate compatibility. Word conversion uses Microsoft Word ([ADR 0002](adr/0002-word-to-pdf-with-microsoft-word.md)).
- Actual Excel writeback destination, inventory import ownership, master structure, and read timing.
- FC/email mapping, repeated product rows, email columns, and product-wide versus per-FC messages.
- Classic Outlook COM versus Graph; drafts versus direct sending; permissions, duplicate prevention, and uncertain outcomes. Draft-first is only a recommendation.
- PostgreSQL is selected, starting locally and targeting company-server collaboration. Open: schema, major version, migration tooling, production host/IT approval, shared-service interface, access, backup/restore, retention, and case/status naming. See [database and deployment](database-and-deployment.md).
- Supported CLI contract/versions and ownership/validation of EXPRESS FCN and BEN rules.
- Whether Order normalization needs AI. AI in the template means development assistance, not a runtime requirement. The existing checker is deterministic; its policy does not automatically settle runtime policy for this entire project.

Additional recommended slice: local PostgreSQL, synthetic cases/version records, and one persisted check run; implementation is not yet authorized. Historical-source inventory, inventory-Excel authority transition, and import rules require separate decisions.

Suggested sequence: validate Order/attachment samples and product identity; document preparation/checker integration; archives/inventory; email drafts; evaluate automatic sending. This is a recommendation, not an implementation commitment.
