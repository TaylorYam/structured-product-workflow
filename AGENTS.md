# AI 開發指南 / AI Development Guide

> 本文件中英並列：上半為繁體中文，下半為英文，兩者內容相同。修改時兩區必須同步更新；若內容不一致，以英文版為準。
>
> This document is bilingual: Traditional Chinese first, English below, with the same content. Update both sections together; if they differ, the English version prevails.

[繁體中文](#繁體中文) | [English](#english)

## 繁體中文

這份位於根目錄的檔案是本 repository 共用的 AI 開發政策。它定義**何時**使用各個工作流程；skill 則定義**如何**執行。若明確的任務要求縮小或擴充本指南的範圍，以該要求為準。

### 工作分流

建立 Issue 之前先選定路線：

- 新功能、重要需求不明確：`/grill-with-docs` → `/to-spec` → 只有單一 Issue／PR 太大時才用 `/to-tickets` → `/implement` → `/code-review`。只針對真正不確定的地方追問。
- 新功能、需求明確：檢查相關程式碼 → 建立 GitHub Issue → `/implement <issue>` → `/code-review`。
- Bug、原因與正確行為都明確：檢查相關程式碼 → 建立 GitHub Issue → `/implement <issue>` → `/code-review`。合理時加入回歸測試（regression test）。
- Bug、原因不明：`/diagnosing-bugs` → 建立重現方式 → 找出根本原因 → 確認正確行為 → 建立 GitHub Issue → `/implement <issue>` → `/code-review`。假設與已確認的原因要分開記錄。
- 外部或他人提出的 Issue：既有 Issue → `/triage <issue>` → `ready-for-agent` → `/implement <issue>` → `/code-review`。

`/triage` 主要用於他人建立的 Issue。由本 agent、`/to-spec` 或 `/to-tickets` 建立且已可直接實作的 Issue，不需要再 triage。只有工作量超過單一 Issue／PR 時才使用 `/to-tickets`。

判斷方式：不知道**要做什麼** → Grill；知道**要做什麼** → Issue → Implement；不知道 **bug 為什麼發生** → Diagnose；明確的 bug → Issue → Implement；外部 Issue → Triage；工作量過大 → Tickets。

### 修改檔案之前

- 檢查目前的 branch 與工作目錄狀態。一般專案工作應從最新的 `main` 開始，並在編輯前建立任務 branch 或 worktree。
- 有附上 Issue 時先閱讀。閱讀 `README.md`、`docs/architecture.md` 與相關 ADR，並檢查相關的實作與測試。
- 較大的工作在編輯前先說明預期成果、可能修改的檔案、做法、風險與驗證方式。遵守使用者明確設定的核准界線。
- 若 Issue 或規格（spec）留下未決定、且會實質影響行為、資料正確性、使用者體驗、架構、資安、付款或真實交易、刪除、API 行為或向下相容性的重要選擇，應停在規劃階段，用淺白的繁體中文詢問使用者。低風險、可回復的實作選擇可直接決定，不必打斷。

### GitHub 優先工作流程

`main` 保持為穩定的整合 branch。規劃中的工作依照以下流程：

`Issue → branch 或 worktree → 規劃 → 實作 → 驗證 → commit 與 push → pull request → review 與 CI → merge`

- 實作前先建立可直接實作的 Issue，寫清楚目標、範圍與可觀察的驗收條件。Agent 建立的 Issue 標題與內文所有段落預設使用繁體中文（台灣用語），包含 `/to-spec` 建立的 Issue 與 `/to-tickets` 的子 Issue。正式技術識別名稱保留英文。既有的英文 Issue 不需改寫；直接與說英文的外部貢獻者溝通時使用英文。
- Branch 名稱要能看出用途，例如 `feat/export-report` 或 `fix/timezone-boundary`。
- 變更聚焦在對應的 Issue，並在 PR 中記錄重要的使用者可見或架構上的取捨。
- 執行相關檢查；行為改變時新增或更新測試，並回報無法執行的檢查。
- 在 PR 中連結 Issue。必要的 review 與檢查通過後才 merge。
- 若是尚未建立預設 branch 的初始（bootstrap）repository，可依需要直接建立基準內容；之後的一般工作改用 branch 與 PR。

### 溝通

- 與使用者的一般對話（包含 `/grill-with-docs`）使用簡潔、具體、淺白的繁體中文（台灣用語）。用日常語言提出深入的問題。
- 有幫助時使用技術術語；不常見的術語第一次出現時簡短解釋。Git、Branch、PR、API、Database、SQL、CI 等常見術語不需強行翻譯。函式名稱、類別、API、函式庫、路徑與指令保留原文。

### 設計與實作

- 遵循既有的架構、命名、格式與相依套件，除非任務本身要改變它們。以符合驗收條件的最小且一致的變更為原則；避免無關的重構與新增相依套件。
- 行為、設定方式或介面改變時更新文件。持久的架構決策依 `docs/adr/` 的格式記錄。
- 中英並列的文件（`AGENTS.md`、`CLAUDE.md`、`README.md`、`docs/`）修改時，中文與英文兩區必須同步更新。

### 機密與本機資料

- 憑證、token、金鑰、密碼與填好值的 `.env` 檔不得進入 Git、log 或範例。`.env.example` 只放名稱與安全的佔位值；真實值放在核准的機密儲存處或本機環境。
- 產生的資料、快取、建置產出與特定機器的設定不要 commit，除非專案把該產出視為原始碼。
- 若在工作目錄或歷史紀錄中發現機密，立即停止並回報，不要複製或重述其內容。

### 完成

交回工作前，檢查 diff、執行適用的驗證，並總結變更內容、測試結果與限制。不要動到與任務無關的使用者變更。

---

## English

This root-level file is the repository's shared AI development policy. It defines **when** to use each workflow; skills define **how** to carry it out. Follow an explicit task request when it narrows or extends this guide.

### Work routing

Choose the route before creating an Issue:

- New feature, important requirements unclear: `/grill-with-docs` → `/to-spec` → `/to-tickets` only if one Issue / PR is too large → `/implement` → `/code-review`. Grill only genuine uncertainties.
- New feature, requirements clear: inspect relevant code → create a GitHub Issue → `/implement <issue>` → `/code-review`.
- Bug, cause and correct behavior clear: inspect relevant code → create a GitHub Issue → `/implement <issue>` → `/code-review`. Add a regression test when reasonable.
- Bug, cause unclear: `/diagnosing-bugs` → establish reproduction → identify root cause → confirm correct behavior → create a GitHub Issue → `/implement <issue>` → `/code-review`. Keep hypotheses separate from confirmed causes.
- External or incoming Issue: existing Issue → `/triage <issue>` → `ready-for-agent` → `/implement <issue>` → `/code-review`.

Use `/triage` mainly for Issues created by others. An implementation-ready Issue created by this agent, `/to-spec`, or `/to-tickets` does not need another triage. Use `/to-tickets` only when the work is too large for one Issue / PR.

Decision guide: unknown **what to build** → Grill; known **what to build** → Issue → Implement; unknown **why a bug occurs** → Diagnose; clear bug → Issue → Implement; external Issue → Triage; oversized work → Tickets.

### Before changing files

- Check the current branch and working tree. For normal project work, start from an up-to-date `main` and create a task branch or worktree before editing.
- Read the linked Issue when one is provided. Read `README.md`, `docs/architecture.md`, and relevant ADRs. Inspect related implementation and tests.
- For substantial work, explain the outcome, likely files, approach, risks, and validation before editing. Follow explicit user approval boundaries.
- If an Issue or spec leaves an important choice unresolved that materially affects behavior, data correctness, user experience, architecture, security, payments or real transactions, deletion, API behavior, or backward compatibility, pause at planning and ask the user in plain Traditional Chinese. Make low-risk, reversible implementation choices without interruption.

### GitHub-first workflow

Keep `main` as the stable integration branch. For planned work, follow:

`Issue → branch or worktree → plan → implementation → validation → commit and push → pull request → review and CI → merge`

- Create an implementation-ready Issue before implementation, with a clear goal, scope, and observable acceptance criteria. Agent-created Issue titles and all body sections default to Traditional Chinese (Taiwan usage), including `/to-spec` Issues and `/to-tickets` children. Keep formal technical identifiers in English. Existing English Issues need no rewrite; use English when communicating directly with an English-speaking external contributor.
- Give branches descriptive names, such as `feat/export-report` or `fix/timezone-boundary`.
- Keep changes focused on their Issue and record significant user-visible or architectural tradeoffs in the PR.
- Run relevant checks; add or update tests when behavior changes and report checks that could not run.
- Link the Issue in the PR. Merge after required review and checks pass.
- For a bootstrap repository without an established default branch, initialize the baseline as needed; use branches and PRs for regular work afterward.

### Communication

- Use concise, concrete, plain Traditional Chinese (Taiwan usage) in ordinary conversations with the user, including `/grill-with-docs`. Ask deep questions in everyday language.
- Use technical terms when helpful; explain unfamiliar ones briefly on first use. Common terms such as Git, Branch, PR, API, Database, SQL, and CI need no forced translation. Preserve function names, classes, APIs, libraries, paths, and commands.

### Design and implementation

- Follow existing architecture, naming, formatting, and dependencies unless the task changes them. Make the smallest coherent change that meets acceptance criteria; avoid unrelated refactors and dependencies.
- Update documentation when behavior, setup, or interfaces change. Record lasting architecture decisions in `docs/adr/` using its format.
- When editing a bilingual document (`AGENTS.md`, `CLAUDE.md`, `README.md`, `docs/`), update the Chinese and English sections together.

### Secrets and local data

- Keep credentials, tokens, keys, passwords, and populated `.env` files out of Git, logs, and examples. Put only names and safe placeholders in `.env.example`; use the approved secret store or local environment for real values.
- Keep generated data, caches, build output, and machine-specific configuration out of commits unless the project treats an artifact as source.
- If a secret appears in the working tree or history, stop and report it without copying or repeating it.

### Completion

Review the diff, run applicable validation, and summarize changes, test results, and limitations before handing work back. Leave unrelated user changes untouched.
