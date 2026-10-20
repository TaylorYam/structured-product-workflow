# 0005: 核對專案併入本專案／Merge the Checker into This Repository

- 狀態／Status: Accepted
- 日期／Date: 2026-10-20
- 追蹤／Tracking: #54（範圍改寫）
- 取代／Supersedes: [核對工具串接提案](../checker-integration.md)「兩個 repository 各自版本化、以 CLI 與檔案交換串接」的建議；AGENTS.md「核對規則保留在獨立核對專案」一條／the checker-integration proposal's "version the two repositories independently and integrate through CLI and file exchange" recommendation, and the AGENTS.md rule to keep checking rules in the independent checker repository

## 繁體中文

### 背景

TS／IIS 核對在 `fcn-term-sheet-checker`，準備、放回、每日匯入、Order 條件與寄信草稿在本專案。新流程（[ADR 0006](0006-new-product-data-from-order.md)）要讓核對從資料庫讀條件、核對完直接回填商品資料，橫跨兩個 repository；分開維護要另訂跨專案的結果格式、版本對照與兩套安裝。使用者希望作業人員在同一套程式裡一步一步做完整個流程（2026-10-20 確認）。

### 決策

- 合併成一個 repository：保留 `structured-product-workflow`，把核對專案連同 commit 歷史搬進來，核對程式成為 `src/fcn_checker/`，與 `src/sp_workflow/` 並排。
- 先合併、再做新的資料流程；合併初期兩個套件各自保留測試與 PANEL 入口，之後再合成一個 PANEL。
- 原本的 `fcn-term-sheet-checker` 封存（只讀），Issue 與 PR 紀錄留在那裡；還沒關的 Issue 搬過來重開或改寫。
- 真實樣本的私人倉庫 `fcn-oos-samples` 維持獨立、私人，不併入。

### 考慮過的替代方案

- **操作上合併、程式分開**：一個 PANEL 呼叫獨立安裝的核對工具；不用搬 repo，但跨專案的結果格式、版本對照與兩套安裝都還在。
- **保留核對專案、把本專案搬過去**：核對專案歷史較長，但本專案負責整體流程、資料庫 migration 與安裝，名稱也較貼切。
- **開全新 repository**：兩邊都要搬，沒有額外好處。

### 後果

- 跨專案契約（`schema_version`、結果匯出指令）不再需要；核對與回填在同一個 PR 修改、同一套測試驗證。
- 合併本身是一次大的機械性搬移：安裝（`setup_panel`、`constraints.txt`）、設定檔與 `config/`、本機資料夾（`data/`、`runtime/`）、CI 與文件都要整理；合併期間不改核對規則。
- 一個 repository 的 CI 與測試時間變長；核對專案的本機真實樣本測試照舊只在本機有資料時執行。

## English

### Context

TS/IIS checking lives in `fcn-term-sheet-checker`; preparation, returning, daily import, Order terms, and mail drafts live here. The new flow ([ADR 0006](0006-new-product-data-from-order.md)) has checking read terms from the database and backfill product data directly, spanning both repositories; keeping them apart would require a cross-project result format, a version matrix, and two installations. The user wants operators to finish the whole flow step by step in one program (confirmed 2026-10-20).

### Decision

- Merge into one repository: keep `structured-product-workflow` and move the checker in with its commit history as `src/fcn_checker/`, beside `src/sp_workflow/`.
- Merge first, then build the new data flow. Initially both packages keep their own tests and PANEL entry points; a single PANEL comes later.
- Archive the original `fcn-term-sheet-checker` (read-only), keeping its issues and PRs; reopen or rewrite still-open issues here.
- The private real-sample repository `fcn-oos-samples` stays separate and private.

### Alternatives considered

- **Merge operations, keep code separate**: one PANEL calling a separately installed checker; no repository move, but the cross-project result format, version matrix, and two installations remain.
- **Keep the checker repository and move this one into it**: the checker has the longer history, but this repository owns the overall flow, database migrations, and installation, and its name fits better.
- **A new repository**: both sides move with no added benefit.

### Consequences

- No cross-project contract (`schema_version`, result export command) is needed; checking and backfill change in one PR and are validated by one test suite.
- The merge is a large mechanical move: installation (`setup_panel`, `constraints.txt`), configuration and `config/`, local folders (`data/`, `runtime/`), CI, and documentation all need consolidation; checking rules do not change during the merge.
- One repository means longer CI and test runs; the checker's local real-sample tests still run only where the data exists.
