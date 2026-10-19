# 0003: 最小 FCN 資料庫與每日更新範本／Minimal FCN Database and Daily Update Template

- 狀態／Status: Accepted（#34 實作；信箱維護方式由 [ADR 0004](0004-mailbox-maintained-in-database.md) 取代／implemented in #34; mailbox maintenance superseded by ADR 0004）
- 日期／Date: 2026-10-19
- 追蹤／Tracking: #32、#29

## 繁體中文

### 背景

[ADR 0001](0001-postgresql-workflow-storage.md) 選定 PostgreSQL，但沒有決定要存什麼。寄 TS／IIS 信需要同時比對交易清單（FC、分公司、金額）、庫存總表（商品條件）與信箱表，每次寄信都重新比對，錯誤要到寄信時才發現。庫存總表欄位多、資料年代久，2026 年 4 月以前的資料錯誤較多；目前沒有歷史分析需求，使用者希望寄信流程盡快上線。交易清單與庫存總表由其他人員維護，格式不完全固定。

### 決策

- **只存寄信與對帳需要的 FCN 資料**：商品條件（含發行機構、ISIN、庫存狀態拆成事件／日期／接股標的）、庫存總表的分公司配置、交易清單的交易列、匯入紀錄。其他商品與存續分析欄位不存；之後以 TDCC 關聯新增資料表擴充。
- **同一個資料庫、兩個 schema**：`mailbox` 放人員、信箱、固定副本與別名；`workflow` 放商品、配置、交易與匯入紀錄。不另開資料庫，以便直接關聯查詢、設定約束、共用連線與備份。
- **唯一的匯入入口是固定格式的 `每日更新.xlsx`**：`交易明細`、`清單更新` 兩張工作表，欄名與順序必須和來源表完全相同；作業人員整列貼上，程式依欄名讀取需要的欄位，欄名不符就整份不匯入。信箱以 `信箱.xlsx` 整份更新。
- **以 TDCC 為單位、第一次寫入為準**：重複匯入不新增；內容不同只提醒不覆蓋。分公司金額對帳只提醒、不擋。整次匯入在單一 transaction 內完成。
- **資料表版本以編號的 SQL 檔管理**，由小工具依序套用並記錄版本。
- **部署採「重建」而非複製**：先在筆電開發，上線時在公司電腦安裝 PostgreSQL、套用 SQL 版本檔、重新匯入；不在電腦之間複製資料庫檔或公司資料。
- 寄信草稿（#29）只讀資料庫；「核對通過」以商品資料夾中標準檔名的 TS／IIS 為準，不依賴交易清單的人工 V。

### 考慮過的替代方案

- **整份庫存總表匯入**：資料完整，但要先清理多年的舊資料，且大多數欄位目前用不到。
- **寄信時直接讀三張 Excel**（#29 原設計）：上線較快，但比對邏輯之後要換成讀資料庫，等於寫兩次。
- **程式直接讀來源 Excel**：不需每日貼上，但來源格式由他人維護、會變動，程式要吸收各種例外。
- **信箱另開資料庫**：權限可分開，但無法跨資料庫關聯與設定約束，連線與備份變成兩套；schema 也能分開權限。
- **複製筆電資料庫到公司電腦**：較快，但公司資料要在電腦之間移動，也無法證明能從零重建。

### 後果

- 每天多一個「貼上＋匯入」的步驟；忘記匯入時寄信會看到舊資料，寄信前需顯示最近一次匯入時間。
- 已存在商品的庫存狀態暫不更新，存續分析要等後續決定更新方式（整份重貼或系統自行比價）。
- 測試需要真的 PostgreSQL：CI 加 PostgreSQL 服務，本機沒有時略過。
- 作業人員的安裝需加入 PostgreSQL 驅動；公司電腦需安裝 PostgreSQL，可能需要 IT 核准。
- 2025 年 Q4 的資料格式不同，需另行整理後補入。

## English

### Context

[ADR 0001](0001-postgresql-workflow-storage.md) selected PostgreSQL without deciding what to store. Sending TS/IIS mail requires cross-checking the trade list (FCs, branches, amounts), the inventory master (product terms), and the mailbox workbook on every send, so errors surface only at send time. The inventory master is wide and spans many years, with more errors before April 2026; there is no current analytics need, and the user wants the mail workflow live quickly. Other staff maintain the trade list and inventory master, whose layouts are not fully stable.

### Decision

- **Store only FCN data needed for mail and reconciliation**: product terms (including issuer, ISIN, and inventory status split into event/date/delivered underlying), inventory branch allocations, trade-list rows, and import records. Other products and lifecycle-analysis columns are excluded; extend later with tables keyed by TDCC.
- **One database, two schemas**: `mailbox` holds people, addresses, fixed CCs, and aliases; `workflow` holds products, allocations, trades, and import records. No separate database, so queries can join, constraints apply, and connections and backups stay single.
- **The only import entry point is the fixed-layout `每日更新.xlsx`**: sheets `交易明細` and `清單更新` whose headers and order must exactly match the sources. Operators paste whole rows; the program reads needed columns by header and rejects the file on header mismatch. People are refreshed wholesale from `信箱.xlsx`.
- **TDCC is the unit and the first write wins**: re-imports add nothing; differences are reported, not overwritten. Branch-amount reconciliation only warns. Each import runs in one transaction.
- **Schema versions are numbered SQL files** applied in order by a small tool that records applied versions.
- **Deploy by rebuilding, not copying**: develop on the laptop; at go-live install PostgreSQL on the company computer, apply the SQL versions, and re-import. Database files and company data are not copied between machines.
- Mail drafts (#29) read only the database; "checked" means the product folder holds standard-named TS/IIS files, not the manual trade-list V.

### Alternatives considered

- **Import the whole inventory master**: complete, but requires cleaning years of old data, and most columns are unused now.
- **Read the three workbooks at send time** (original #29 design): faster to ship, but the matching logic would be rewritten for the database.
- **Read source workbooks directly**: no daily paste, but sources are maintained by others and change, so the program would absorb every exception.
- **Separate mailbox database**: separable permissions, but no cross-database joins or constraints and duplicated connections and backups; schemas can also separate permissions.
- **Copy the laptop database to the company computer**: faster, but moves company data between machines and does not prove a clean rebuild.

### Consequences

- A daily "paste and import" step is added; a missed import leaves mail reading stale data, so drafting should show the last import time.
- Inventory status of existing products is not refreshed yet; lifecycle analysis waits for a later decision (full re-paste or system-side price checks).
- Tests need a real PostgreSQL: CI adds a PostgreSQL service, and local runs skip without one.
- Operator installation must include the PostgreSQL driver; the company computer needs PostgreSQL, possibly with IT approval.
- 2025 Q4 data uses a different layout and will be prepared and imported separately.
