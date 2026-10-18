# 0001: 採用 PostgreSQL 保存流程資料／Use PostgreSQL for Workflow Data

- 狀態／Status: Accepted（選型；功能尚未實作／selection only; not implemented）
- 日期／Date: 2026-10-18
- 追蹤／Tracking: #3

## 繁體中文

### 背景

使用者希望兩到三人共同作業、跨天接手，不依賴個人電腦，並在未來分析過去四年發行的 FCN。原先希望把資料庫放公槽，是為了共用與接手；不代表必須採共用資料檔架構。筆電開發後透過 GitHub 部署到另一台作業主機。

### 決策

採用 PostgreSQL 作為本流程專案的資料庫方向。本機先開發與驗證，之後可在公司伺服器安裝同一引擎並搬遷資料。資料庫由流程專案管理；獨立 fcn-term-sheet-checker 不直接依賴此資料庫。

開發資料庫與作業資料庫分開，兩邊可各自安裝 PostgreSQL，以相同主要版本與必要擴充降低差異。GitHub 同步程式碼、遷移檔與虛構資料建立程序，不同步資料庫內容、憑證或公司文件。一般部署更新程式與資料表，不以筆電開發資料覆蓋正式資料。

運作資料目錄規劃放在執行 PostgreSQL 的主機所管理的本機磁碟，不讓各使用者直接開啟公槽資料庫檔案。公槽保存原始／確認文件、Excel 交換檔及一致的備份副本。特殊網路儲存可行性需 IT 另行驗證，不是本專案預設。

### 替代方案

- Excel：保留既有輸入、庫存總表及輸出分析用途；不再以多份 Excel 副本作為流程進度的長期集中管理方案。既有庫存 Excel 的正式資料地位，直到另有切換決定前不變。
- SQLite：適合本機工具或單服務輕量系統，但不選作本專案預設，避免後續換引擎。小型多人服務也可使用 SQLite；排除的是讓多台電腦直接讀寫公槽資料檔的方案。
- MySQL／SQL Server：均為可行替代；PostgreSQL 不是因四年資料或兩三人需求而唯一可行。若 IT 限制或既有維護環境要求改選，先新增取代此決策的 ADR，不在實作中默默更換。

### 後果與未定事項

需建立連線設定、權限、遷移程序、備份還原及部署說明。共享服務、最終主機、IT 核准與具體工具仍待定；選型不代表已獲公司部署許可或已完成安裝驗證。桌面 Word／Outlook 自動化需另評估，不能假設可原樣搬到背景伺服器。

實作與資料模型提案見 [資料庫與部署](../database-and-deployment.md)。

## English

### Context

The user wants two or three collaborators, cross-day handoffs, independence from a personal computer, and future analysis of four years of FCN issuance. The shared-folder request expresses sharing and handoff needs, not a requirement for shared database files. Code is developed on a laptop and deployed through GitHub to a separate operational host.

### Decision

Select PostgreSQL for this workflow project. Develop and validate locally, then install the same engine and migrate data to a company server. The workflow project owns persistence; the independent fcn-term-sheet-checker does not directly depend on this database.

Separate development and operational databases. Both machines may run PostgreSQL with matching major versions and required extensions. GitHub carries code, migrations, and synthetic seed procedures, never database contents, credentials, or company documents. Routine deployment updates code and schema without replacing production records with laptop data.

Keep the live data directory on host-managed local storage. Users do not open database files through a network share. Shared folders hold original/approved documents, Excel exchange files, and consistent backup copies. Specialized network storage requires separate IT validation and is not the default.

### Alternatives

- Excel remains useful for existing inputs, the inventory master, exports, and analysis; multiple workbook copies will not be the long-term central workflow-state store. Existing inventory Excel remains authoritative until an explicit transition decision.
- SQLite suits local tools and small single-service systems, including some collaborative applications. It is not the project default to avoid a later engine migration. Direct multi-machine access to shared database files is the rejected deployment pattern.
- MySQL and SQL Server remain viable. Neither four years of data nor two or three users makes PostgreSQL uniquely necessary. If IT requirements favor another engine, supersede this ADR explicitly instead of silently switching.

### Consequences and unresolved matters

Implement configuration, access control, schema migrations, backup/restore, and deployment instructions. Shared services, final host, IT approval, and tooling remain open. Selection does not establish deployment authorization or a verified installation. Desktop Word/Outlook automation requires separate evaluation before server deployment.

See [database and deployment](../database-and-deployment.md) for implementation and data-model proposals.
