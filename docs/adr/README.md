# 架構決策紀錄 / Architecture Decision Records

> 本文件中英並列：上半為繁體中文，下半為英文。修改時請同步更新兩區。
>
> This document is bilingual: Traditional Chinese first, English below. Update both sections together.

[繁體中文](#繁體中文) | [English](#english)

## 繁體中文

當某個選擇會長期影響系統結構、相依套件、資料、維運、資安或相容性時，使用架構決策紀錄（Architecture Decision Record，ADR）。日常的實作細節留在程式碼與 pull request 中即可。

### 建立紀錄

1. 使用架構決策提案範本開 Issue，討論問題與選項。
2. 做出決定後，新增一個有編號的檔案，例如 `0001-use-managed-database.md`。
3. 記錄背景、決策、考慮過的替代方案與後果。內容保持精簡、具體。
4. 在 `docs/architecture.md` 連結此 ADR；必要時也從相關程式碼或文件連結。

### 建議格式

```markdown
# 0001: 決策標題

- 狀態：Proposed | Accepted | Deprecated | Superseded by [NNNN](NNNN-title.md)
- 日期：YYYY-MM-DD

## 背景
是什麼問題與限制需要做出決策？

## 決策
專案決定了什麼？

## 考慮過的替代方案
評估過哪些其他選項？為什麼沒有採用？

## 後果
帶來哪些好處、成本、風險與後續工作？
```

已採納的 ADR 視為歷史紀錄。決策改變時，新增一份 ADR，並將舊紀錄標示為已被取代（superseded），不要改寫舊紀錄的內容。

---

## English

Use an Architecture Decision Record (ADR) for a choice that has a lasting effect on system structure, dependencies, data, operations, security, or compatibility. Keep routine implementation details in the code and pull request.

### Create a record

1. Discuss the question and options in an issue using the architecture decision proposal template.
2. After the decision is made, add a numbered file such as `0001-use-managed-database.md`.
3. Record the context, decision, alternatives, and consequences. Keep the record concise and specific.
4. Link to the ADR from `docs/architecture.md` and from relevant code or documentation when useful.

### Suggested format

```markdown
# 0001: Decision title

- Status: Proposed | Accepted | Deprecated | Superseded by [NNNN](NNNN-title.md)
- Date: YYYY-MM-DD

## Context
What problem and constraints require a decision?

## Decision
What did the project decide?

## Alternatives considered
What other options were evaluated, and why were they not selected?

## Consequences
What benefits, costs, risks, and follow-up work result?
```

Treat accepted ADRs as historical records. When a decision changes, add a new ADR and mark the earlier record as superseded instead of rewriting its history.
