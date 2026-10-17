# 待定事項／Open Questions

## 繁體中文

以下尚未決定，不應由 Agent 當作既定需求：

- 起單與建檔的正式確認時點、Order 修訂優先順序、各家格式及人工確認方式。
- TDCC 取得時點、資料夾結構、候選附件配對與正式版本選擇規則。
- Word 轉檔工具、掃描文件處理、範本及座標解析相容性。
- 回填 Excel 的實際目的地、庫存匯入由誰執行、總表結構與讀取時點。
- FC 與 Email 對照來源、同商品多列彙整、郵件欄位及一檔一封或逐 FC 寄送。
- Outlook 傳統 COM 或 Microsoft Graph；草稿或直接寄送；權限、重複寄送與不確定結果的處理。草稿優先目前只是建議。
- 本機或多人部署、資料儲存、權限與保存期限、案件及狀態名稱。
- CLI 串接正式契約與支援版本；EXPRESS FCN／BEN 規則歸屬與驗證範圍。
- 正式 Order 歸一化是否需要 AI；範本中的 AI 指輔助開發，並不代表 runtime 必須使用 AI。既有核對專案採規則式 runtime；其限制不自動等同整個新專案的已決政策。

建議開發順序：驗證 Order／附件樣本與商品身分 → 文件準備及核對串接 → 歸檔與庫存 → 郵件草稿 → 評估自動寄送。此順序是建議，尚未承諾實作。

## English

Unresolved items, not established requirements:

- Formal confirmation points for placement/booking, Order revision precedence, issuer formats, and human confirmation.
- TDCC availability, folder hierarchy, attachment matching, and official-version selection.
- Word conversion, scanned documents, template and coordinate compatibility.
- Actual Excel writeback destination, inventory import ownership, master structure, and read timing.
- FC/email mapping, repeated product rows, email columns, and product-wide versus per-FC messages.
- Classic Outlook COM versus Graph; drafts versus direct sending; permissions, duplicate prevention, and uncertain outcomes. Draft-first is only a recommendation.
- Local versus multi-user deployment, persistence, access, retention, and case/status naming.
- Supported CLI contract/versions and ownership/validation of EXPRESS FCN and BEN rules.
- Whether Order normalization needs AI. AI in the template means development assistance, not a runtime requirement. The existing checker is deterministic; its policy does not automatically settle runtime policy for this entire project.

Suggested sequence: validate Order/attachment samples and product identity; document preparation/checker integration; archives/inventory; email drafts; evaluate automatic sending. This is a recommendation, not an implementation commitment.
