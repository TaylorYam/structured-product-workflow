# AI 輔助專案範本 / AI-Assisted Project Template

> 本文件中英並列：上半為繁體中文，下半為英文，兩者內容相同。修改時請同步更新兩區。
>
> This document is bilingual: Traditional Chinese first, English below, with the same content. Update both sections together.

[繁體中文](#繁體中文) | [English](#english)

## 繁體中文

這是一個以 GitHub 為核心、供人員與 AI coding agent 共同開發專案的起始範本。內容包含輕量的貢獻流程、Issue 與 pull request 表單、入門用的 CI 檢查，以及避免把憑證與本機執行資料放進 Git 的指引。

### 從這個範本建立專案

1. 在 GitHub 開啟此 repository，選擇 **Use this template → Create a new repository**，設定新 repository 的擁有者、名稱、可見性與其他選項。
2. Clone 新的 repository，並用編輯器或 coding agent 開啟。
3. 建立一個 **Project bootstrap** Issue，說明專案目的、使用者、選定的技術堆疊與第一個交付成果。
4. 請 coding agent 閱讀 `AGENTS.md`、本 README、`.github/` 與 `docs/`，檢查 repository 後，先提出針對此專案的修改建議再動手編輯。
5. 依專案需求調整 `.gitignore`、`.env.example`、`docs/architecture.md` 與 `.github/workflows/ci.yml`。開始開發功能前，先加入測試與該技術堆疊的品質檢查。
6. Review 並 merge bootstrap pull request，之後透過 Issue 與 pull request 進行功能開發。

如果 GitHub 沒有顯示 **Use this template**，擁有者可以在 **Settings → General → Template repository** 啟用。

### 貢獻流程

規劃中的變更依照以下流程：

`Issue → branch 或 worktree → 規劃 → 實作 → 驗證 → commit 與 push → pull request → review 與 CI → merge`

`main` 保持為穩定的整合 branch。日常變更在任務 branch 上進行，並透過經過 review 的 pull request 合併。小變更同樣走 branch 與 review 流程；規劃與測試的份量依變更範圍調整。

使用 Issue 範本建立工作項目、錯誤回報與架構決策提案。在 pull request 中連結 Issue，讓需求與實作保持關聯。

Agent 建立 Issue 之前應先依 `AGENTS.md` 的工作分流判斷路線。Agent 對話預設使用淺白的繁體中文（台灣用語）；agent 建立的 GitHub Issue 預設使用繁體中文。

### Repository 導覽

- `AGENTS.md` — Claude Code、Codex 與人類貢獻者共用的 AI 開發政策。
- `CLAUDE.md` — Claude Code 的入口檔，會匯入 `AGENTS.md`。
- `.github/ISSUE_TEMPLATE/` — 工作項目、錯誤回報與架構決策提案表單。
- `.github/pull_request_template.md` — Review 檢查清單與驗證紀錄。
- `.github/workflows/ci.yml` — 入門用的空白字元檢查；專案的 formatter、測試、型別檢查與建置請加在這裡。
- `.gitignore` — 排除常見的產生檔、本機設定、憑證與執行期資料。
- `.env.example` — 環境變數的名稱與安全佔位值；切勿放入真實機密。
- `docs/architecture.md` — 目前系統的概觀，以及重要設計選擇的索引。
- `docs/adr/` — 重大架構決策的長期紀錄。

### 範本維護

保持此 repository 不綁定特定技術堆疊。修改工作流程時，更新相關原始檔；若變更會影響新專案的初始化方式，也要更新本指南。中英並列的文件修改時，兩種語言要同步更新。使用 `v1.0.0` 這類 Git tag 標示新 repository 所使用的範本版本。

---

## English

A GitHub-first starting point for projects built by people and AI coding agents. It provides a lightweight contribution workflow, issue and pull request forms, a starter CI check, and guidance for keeping credentials and local runtime data out of Git.

### Start a project from this template

1. On GitHub, open this repository and select **Use this template → Create a new repository**. Choose the new repository's owner, name, visibility, and other settings.
2. Clone the new repository and open it in your editor or coding agent.
3. Create a **Project bootstrap** issue describing the purpose, users, chosen stack, and first deliverable.
4. Ask the coding agent to read `AGENTS.md`, this README, `.github/`, and `docs/`; inspect the repository; then propose project-specific changes before editing.
5. Tailor `.gitignore`, `.env.example`, `docs/architecture.md`, and `.github/workflows/ci.yml` to the project. Add tests and stack-specific quality checks before feature work.
6. Review and merge the bootstrap pull request, then begin feature work through issues and pull requests.

If GitHub does not show **Use this template**, an owner can enable it in **Settings → General → Template repository**.

### Contribution flow

For planned changes, use:

`Issue → branch or worktree → plan → implementation → validation → commit and push → pull request → review and CI → merge`

Keep `main` as the stable integration branch. Make routine changes on a task branch and merge them through a reviewed pull request. Small changes still follow the same branch and review path; the amount of planning and testing should match their scope.

Use the issue templates for tasks, bugs, and architecture decision proposals. Link the issue from the pull request so the intent and implementation stay connected.

Agents should follow the Work Routing in `AGENTS.md` before creating an Issue. Agent conversations default to plain Traditional Chinese (Taiwan usage); agent-created GitHub Issues default to Traditional Chinese.

### Repository guide

- `AGENTS.md` — shared AI development policy for Claude Code, Codex, and human contributors.
- `CLAUDE.md` — Claude Code entry point that imports `AGENTS.md`.
- `.github/ISSUE_TEMPLATE/` — task, bug, and architecture decision forms.
- `.github/pull_request_template.md` — review checklist and validation record.
- `.github/workflows/ci.yml` — starter whitespace check; add the project's formatter, tests, type checks, and build here.
- `.gitignore` — common generated files, local configuration, credentials, and runtime data exclusions.
- `.env.example` — names and safe placeholders for environment variables; never put real secrets here.
- `docs/architecture.md` — current system overview and pointers to important design choices.
- `docs/adr/` — durable records of significant architecture decisions.

### Template maintenance

Keep this repository stack-neutral. When changing the workflow, update the relevant source file and this guide if the change affects how a new project is bootstrapped. When editing a bilingual document, update both languages together. Use Git tags such as `v1.0.0` to identify template versions used by new repositories.
