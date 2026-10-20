# 待定事項／Open Questions

## 繁體中文

以下尚未決定，不應由 Agent 當作既定需求：

- 起單與建檔的正式確認時點、Order 修訂優先順序及人工確認方式。各家 Order 格式的第一版讀法已決定：以我方 `(Order)` 原信為準、規則式不用 AI（[#40](https://github.com/TaylorYam/structured-product-workflow/issues/40)）；上手回覆信是否比對待定；Citi 的 Non Callable Periods（樣本為 0）是否等同其他家的 Guaranteed Periods 待確認。
- TDCC 取得時點、資料夾結構、候選附件配對與正式版本選擇規則。
- 掃描文件處理、範本及座標解析相容性。Word 轉檔工具已選 Microsoft Word（[ADR 0002](adr/0002-word-to-pdf-with-microsoft-word.md)）。
- 回填 Excel 的實際目的地、庫存匯入由誰執行、總表結構與讀取時點。
- 自動寄出的時機與條件、重複寄送防護、寄送紀錄與不確定結果的處理。第一版寄信草稿已決定（傳統版 Outlook COM、只建草稿、FC 對照與郵件欄位），見 [#29](https://github.com/TaylorYam/structured-product-workflow/issues/29)。
- PostgreSQL 已選定，本機開發後目標為公司伺服器協作。第一階段的資料範圍、schema 分法與版本管理方式見 [ADR 0003](adr/0003-minimal-fcn-database.md)。仍待定：主要版本、正式主機與 IT 核准、共享服務介面、權限、備份還原與保存期限、案件及狀態名稱。見 [資料庫與部署](database-and-deployment.md)。
- CLI 串接正式契約與支援版本；EXPRESS FCN／BEN 規則歸屬與驗證範圍。
- 正式 Order 歸一化第一版採規則式、不用 AI（#40）；範本中的 AI 指輔助開發，並不代表 runtime 必須使用 AI。

新增最小切片建議：本機 PostgreSQL＋虛構案件／版本資料＋一次核對結果入庫，尚未授權實作。歷史分析資料盤點、庫存 Excel 的正式來源切換與匯入規則另待確認。

建議開發順序：驗證 Order／附件樣本與商品身分 → 文件準備及核對串接 → 歸檔與庫存 → 郵件草稿 → 評估自動寄送。此順序是建議，尚未承諾實作。

## English

Unresolved items, not established requirements:

- Formal confirmation points for placement/booking, Order revision precedence, and human confirmation. The first issuer-format reading is decided: our original `(Order)` email is authoritative and parsing is rule-based without AI ([#40](https://github.com/TaylorYam/structured-product-workflow/issues/40)); whether to compare issuer replies remains open, and whether Citi's Non Callable Periods (0 in the sample) equals other issuers' Guaranteed Periods needs confirmation.
- TDCC availability, folder hierarchy, attachment matching, and official-version selection.
- Scanned documents, template and coordinate compatibility. Word conversion uses Microsoft Word ([ADR 0002](adr/0002-word-to-pdf-with-microsoft-word.md)).
- Actual Excel writeback destination, inventory import ownership, master structure, and read timing.
- When and under what conditions to send automatically, duplicate prevention, send records, and uncertain outcomes. The first mail-draft version is decided (classic Outlook COM, drafts only, FC mapping, and email columns); see [#29](https://github.com/TaylorYam/structured-product-workflow/issues/29).
- PostgreSQL is selected, starting locally and targeting company-server collaboration. First-phase scope, schema split, and schema versioning are in [ADR 0003](adr/0003-minimal-fcn-database.md). Open: major version, production host/IT approval, shared-service interface, access, backup/restore, retention, and case/status naming. See [database and deployment](database-and-deployment.md).
- Supported CLI contract/versions and ownership/validation of EXPRESS FCN and BEN rules.
- The first Order normalization is rule-based without AI (#40). AI in the template means development assistance, not a runtime requirement.

Additional recommended slice: local PostgreSQL, synthetic cases/version records, and one persisted check run; implementation is not yet authorized. Historical-source inventory, inventory-Excel authority transition, and import rules require separate decisions.

Suggested sequence: validate Order/attachment samples and product identity; document preparation/checker integration; archives/inventory; email drafts; evaluate automatic sending. This is a recommendation, not an implementation commitment.
