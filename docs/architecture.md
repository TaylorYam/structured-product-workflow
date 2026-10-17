# 架構概觀 / Architecture Overview

> 本文件中英並列：上半為繁體中文，下半為英文。修改時請同步更新兩區。
>
> This document is bilingual: Traditional Chinese first, English below. Update both sections together.

[繁體中文](#繁體中文) | [English](#english)

## 繁體中文

本文件需與目前的系統保持一致。描述重要的邊界與其背後的理由；容易從程式碼直接看出的細節，改用連結指向程式碼。

### 目的與範圍

說明本專案要解決的問題、使用者，以及不在範圍內的事項。

### 系統脈絡

列出主要的外部系統、使用者，以及進出本專案的資料。

### 元件與邊界

說明主要元件、各自的職責，以及彼此如何溝通。若圖表能讓邊界更容易理解，請加上圖表。

### 資料與狀態

說明重要的資料實體、持久化方式、資料擁有者、保存期限，以及必須保留在本機或由程式產生的執行期資料。

### 執行與部署

記錄支援的環境、部署架構、維運相依項目，以及設定值的提供方式。本文件不得放入憑證。

### 品質屬性與限制

列出影響設計選擇的需求，例如資安、可用性、效能、隱私、相容性與成本。

### 重要決策

連結到 `docs/adr/` 下已採納的架構決策紀錄。

### 更新本文件

當變更影響系統邊界、資料流、部署方式或重要限制時，更新本概觀。長期有效的選擇記錄在 ADR 中，並從這裡連結。

---

## English

Keep this document aligned with the current system. Describe the important boundaries and reasons behind them; link to code for details that are easy to inspect there.

### Purpose and scope

Describe the problem this project solves, its users, and what is outside its scope.

### System context

List the main external systems, users, and data entering or leaving the project.

### Components and boundaries

Describe the major components, their responsibilities, and how they communicate. Add a diagram when it makes the boundaries easier to understand.

### Data and state

Describe important data entities, persistence, ownership, retention, and any runtime data that must remain local or generated.

### Runtime and deployment

Record the supported environments, deployment shape, operational dependencies, and how configuration is supplied. Keep credentials out of this document.

### Quality attributes and constraints

List the requirements that shape design choices, such as security, availability, performance, privacy, compatibility, and cost.

### Important decisions

Link to accepted architecture decision records under `docs/adr/`.

### Updating this document

Update this overview when a change alters system boundaries, data flow, deployment, or an important constraint. Record durable choices in an ADR and link them here.
