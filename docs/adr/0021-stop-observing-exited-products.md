# 0021: 已出場的商品不再比價／Stop Observing Exited Products

- 狀態／Status: Accepted
- 日期／Date: 2026-10-23
- 追蹤／Tracking: #169
- 取代／Supersedes: [ADR 0019](0019-progress-step-records-and-lifecycle-events.md)「每次比價把這檔的自動事件全部重算，和資料庫不同才整組取代」；[ADR 0020](0020-close-prices-and-observation-rules.md) 舊商品「之後比價照樣重算，結果和舊清單事件不同時標待確認」與比價狀態的待確認原因「和舊清單事件不同」／ADR 0019's "each run recalculates the automatic events in full and replaces them as a set when they differ"; ADR 0020's old-product rule "observation then recalculates them, and a result that differs from an old-list event is 待確認" and the 待確認 reason "disagreement with an old-list event"

## 繁體中文

### 背景

ADR 0019 寫每次比價把自動事件全部重算、不同才整組取代；ADR 0020 寫已有存續事件的商品停止比價，舊清單事件的商品卻要重算並比對。兩份互相矛盾（PR #150 總結的待確認第 2 項）。

### 決策（2026-10-23 使用者確認）

- 已有存續事件的商品已出場，不再比價：任何來源都算，包括比價算出的 KO、接股、到期還本，人工登記的顧客贖回，舊清單事件。比價狀態是已出場，原因寫出事件與事件日，已比到的日期沿用上一次；不寫、也不取代自動事件。
- 自動事件只在第一次算出時寫入（照舊經 `lifecycle.replace_auto_events`，改變寫 `workflow.product_event_change`）。
- 前提：收盤價每天直接從彭博來，事後更正的可能性非常低，先假設收盤價不會事後更正；真的遇到時另外處理。
- 庫存清單匯入的舊商品視為已核定完成：有舊清單事件的也停止比價，不再和舊清單比對。
- 已出場之後更正商品條件（例如 KO 價）不重新比價、事件不動；存檔結果提醒這檔已出場、事件不會跟著改，和上手通知不同時到進度頁提出異議（ADR 0019）。
- 核對條件後來不是「完成」的：已出場的照樣已出場，其他的照舊待確認。

### 考慮過的替代方案

- **每次重算、不同才取代（ADR 0019 原本的寫法）**：收盤價或條件事後改了，事件會自己跟著變，已經通知出去的結果可能被悄悄改掉；而且只為了極少發生的收盤價更正。
- **舊清單事件照樣重算、不同標待確認（ADR 0020 原本的寫法）**：多一次復核，但庫存清單的舊商品已核定過，標出來的待確認要一檔一檔看，價值不高；驗證找到的那幾檔改由人工查證。
- **條件更正後自動重比**：已出場的事件會被改掉；改成只提醒、由人提出異議。

### 後果

- 已出場的商品每次比價只讀事件、不再算，比價變快。
- 收盤價真的事後更正時，事件不會自己改；要另外處理（修資料或修程式），在那之前只能提出異議。
- 已出場商品的條件更正只會提醒，事件要對時由人看。
- 舊商品的出場事件只有舊清單事件、不會有比價事件，所以「9 出場清單」改成列那天所有還有效的出場事件，來源不限（比價、舊清單、人工），並加「來源」欄，才和進度頁的存續狀態對得起來（2026-10-24 確認，#188）。

## English

### Context

ADR 0019 says each run recalculates the automatic events in full and replaces them when they differ; ADR 0020 says products with a lifecycle event are no longer observed, yet old-list products are recalculated and compared. The two contradict each other (open item 2 in the PR #150 summary).

### Decision (confirmed by the user 2026-10-23)

- A product with a lifecycle event has exited and is no longer observed, whatever the source: KO, 接股, or 到期還本 from observation, a recorded 顧客贖回, or an old-list event. Its observation status is 已出場 with the event and its date as the reason, the compared-through date stays at the previous value, and no automatic event is written or replaced.
- Automatic events are written only when first found (still through `lifecycle.replace_auto_events`, recording the change in `workflow.product_event_change`).
- Assumption: close prices come straight from Bloomberg every day and are very unlikely to be corrected later, so they are assumed not to be corrected; a correction, if it ever happens, will be handled separately.
- Old products from the inventory list are treated as already checked: products with old-list events also stop being observed and are no longer compared with the old list.
- Correcting the terms of an exited product (for example its KO price) does not observe it again and leaves its events alone; the save result reminds that the product has exited and the events do not follow, and an objection is raised on the progress tab when they differ from the issuer's notice (ADR 0019).
- Products whose check terms are no longer 完成 stay 已出場 when they have exited and are otherwise 待確認 as before.

### Alternatives considered

- **Recalculate every run, replace when different (ADR 0019 as written)**: a later change to a close or a term would silently change events whose results were already sent, only to cover a very rare price correction.
- **Recalculate old-list products and mark differences 待確認 (ADR 0020 as written)**: one more review, but old products from the inventory list were already checked, and each 待確認 would have to be looked at one by one for little value; the products the validation found are checked by hand instead.
- **Re-observe automatically after a term correction**: would change the events of an exited product; a reminder and a manual objection instead.

### Consequences

- Exited products only have their events read on each run, so observation is faster.
- If a close price is ever corrected later, events do not change by themselves; it needs separate handling (fix the data or the program), and until then only an objection is possible.
- A term correction on an exited product only gives a reminder; a person checks whether the events are still right.
- Old products only have old-list exit events and never get observation events, so "9 出場清單" now lists every current exit event of the day whatever its source (比價, 舊清單, 人工), with a source column, to match the lifecycle status on the progress tab (confirmed 2026-10-24, #188).
