# Structured Product Workflow／結構型商品作業流程

## 繁體中文

狀態：設計與文件初始化，尚未實作業務功能。

本專案管理正式 Order、建檔、上手文件歸檔、核對串接及 FC 寄送。詢價端沿用既有系統。
核對規則由獨立的 [fcn-term-sheet-checker](https://github.com/TaylorYam/fcn-term-sheet-checker) 負責。

閱讀順序：[業務背景](CONTEXT.md) → [架構](docs/architecture.md) → [待定事項](docs/open-questions.md)。
資料保存與部署先讀 [PostgreSQL 決策](docs/adr/0001-postgresql-workflow-storage.md) 與 [開發部署設計](docs/database-and-deployment.md)。選型已確定，尚未實作。
串接工作另讀 [核對工具串接提案](docs/checker-integration.md)。

從 TaylorYam/ai-project-template 建立，初始化日期 2026-10-18。保留範本的 Issue、PR、CI 與 Agent 工作流程。
只提交文件與虛構範例；公司信件、交易、客戶、庫存、公槽檔案及憑證保留在公司環境。

## English

Status: design and documentation bootstrap; no business functionality is implemented.

This project coordinates formal Orders, booking, issuer document archiving, checker integration, and FC distribution. Inquiry remains in the existing system.
Checking rules belong to the independent [fcn-term-sheet-checker](https://github.com/TaylorYam/fcn-term-sheet-checker).

Read [business context](CONTEXT.md), [architecture](docs/architecture.md), then [open questions](docs/open-questions.md).
For persistence/deployment, read the [PostgreSQL decision](docs/adr/0001-postgresql-workflow-storage.md) and [development/deployment design](docs/database-and-deployment.md). Selection is settled; implementation has not started.
For integration, also read the [checker integration proposal](docs/checker-integration.md).

Created from TaylorYam/ai-project-template on 2026-10-18, retaining its issue, PR, CI, and agent workflows.
Commit documentation and synthetic examples only. Company messages, trades, customers, inventory, network files, and credentials remain in the company environment.
