# 0019: 作業進度、步驟紀錄與存續事件／Workflow Progress, Step Records, and Lifecycle Events

- 狀態／Status: Accepted（「每次比價把自動事件全部重算」由 [ADR 0021](0021-stop-observing-exited-products.md) 取代／"each run recalculates the automatic events" superseded by ADR 0021）
- 日期／Date: 2026-10-22
- 追蹤／Tracking: 實作 Issue 由本 ADR 的 PR 之後拆出／implementation issues are split off after this ADR's PR
- 補充／Completes: [ADR 0003](0003-minimal-fcn-database.md)「已存在商品的庫存狀態暫不更新」、[ADR 0006](0006-new-product-data-from-order.md)「進度分頁等合成一個 PANEL 再做」、[待定事項](../open-questions.md)的「案件及狀態名稱」／ADR 0003's "inventory status is not refreshed yet", ADR 0006's "the progress tab waits for the single PANEL", and the open question on case and status naming

## 繁體中文

### 背景

一檔商品走到哪一步，現在要看六個地方：交易明細有沒有這檔、商品條件有沒有、`workflow.check_terms` 的狀態、`workflow.check_result`、`locked_at`，以及商品資料夾裡有沒有標準檔名的 TS／IIS PDF。「3 準備」「5 放回」「6 寄信草稿」只寫 Excel 清單與檔案，資料庫沒有紀錄；寄信草稿判斷「核對通過」看的是資料夾有沒有 PDF，不是資料庫的核對結果。

`workflow.product.status_event` 建檔時一律寫「在庫」，清單更新停用（[ADR 0008](0008-daily-prices-denomination-and-order-dates.md)）之後沒有任何流程再改它。KO、接股、到期是一檔商品的多筆事件，[資料庫與部署](../database-and-deployment.md)早已寫明它們是一對多，但資料表沒有事件表。待定事項裡的「案件及狀態名稱」一直沒決定；ADR 0006 把進度分頁留到合成一個 PANEL 之後，PANEL 合成（[ADR 0007](0007-single-workflow-window.md)）後沒有再回頭。

使用者的目標：從起點到終點都自動化，每檔商品在每一步都看得到進度，落後的能單獨處理（回補資料、重新核對）；存續狀態由比價自動更新（[ADR 0020](0020-close-prices-and-observation-rules.md)）。

### 決策（2026-10-22 使用者確認）

**名詞**

- 「核對條件狀態」照舊：完成／等股價／等交易明細／待確認。
- 「作業進度」：一檔商品在五個階段的哪一個（#186）。
- 「存續狀態」：在庫／KO／接股／到期還本／顧客贖回；取代舊文件的「庫存狀態」，庫存總表已經退役。
- 「比價」：每天用收盤價對 KO 價、下限價判定存續事件（ADR 0020）；「比價日」維持原義，是可以 KO 的觀察日。

**作業進度由資料庫事實推出，不另外手填**。每檔只有一個進度，照下表的順序判斷，第一個符合的就是目前的階段（2026-10-23 從九個縮成五個，#186）：

| 進度 | 判定 |
|---|---|
| 完成 | 有「已寄出」；或以前清單更新建立、已鎖住的舊商品（ADR 0020），還沒有任何步驟紀錄（2026-10-23 確認，#184：以前在 Excel 走完） |
| 1 資料準備中 | 核對前的三件裡有任一件還沒齊 |
| 2 待核對 | 三件都齊了，還沒有核對結果 |
| 3 待修正 | 有核對結果，但還沒鎖住（沒通過，等修正後再核一次） |
| 4 待寄信 | 已鎖住（核對通過），還沒有「已寄出」 |
| 5 完成 | 見第一列 |

- 核對前的三件彼此平行、沒有先後，進度頁各一欄打勾：交易明細（有這檔的交易明細）；商品條件（有，而且不缺必填欄，[ADR 0016](0016-partial-product-terms.md)；每個標的都已有進場價時，期初價格空白不算缺，#182）；股價（核對條件是「完成」）。資料準備中的「卡住的原因」寫出缺哪幾件、缺什麼。
- 核對後一定會放回 PDF（通過放正式檔，沒通過放待確認檔），所以放回不再單獨當一個階段；寄信由其他人負責。準備、核對、放回、建草稿等各步日期照舊顯示，同一步有多筆紀錄取最新一筆，但不再決定階段。

**步驟紀錄**：新增 `workflow.step_record`（TDCC、步驟、時間、Windows 使用者名稱、結果、關聯檔案），只新增、不修改。「3 準備」寫「已準備」、「5 放回」寫「已放回」（含放回待確認檔）、「6 寄信草稿」寫「草稿已建立」，各自在寫 Excel 清單的同時寫；Excel 清單照舊產出。「已寄出」由作業人員在進度頁手動標記，不讀 Outlook。

**存續事件**：新增 `workflow.product_event`（TDCC、事件、事件日、標的、來源、備註、誰、何時）。存續狀態 = 最新一筆事件，沒有事件就是在庫。事件日是觸價或判定的那一個觀察日，不存上手實際贖回付款日。

- 自動事件（KO、接股、到期還本）只由比價產生：每次比價把這檔的自動事件全部重算，和資料庫不同才整組取代，改變寫 `workflow.product_event_change`（誰、何時、舊值、新值）。人工不能新增、修改或刪除自動事件；結果和上手通知不同時只能「提出異議」（`workflow.event_objection`：針對的事件、備註、誰、何時、解除時間），進度頁顯示待確認，解法是修資料或修程式。
- 人工事件只有「顧客贖回」（事件日、備註），登記後這檔停止比價。
- 舊清單匯入的狀態轉成來源「舊清單」的事件（ADR 0020）；`status_event`、`status_date`、`status_underlying`、`status_raw` 不再寫入，之後另行移除。

**進度頁**：「8 進度」分頁：篩選交易日區間、作業進度、存續狀態、上手；每列一檔，顯示各步日期、目前進度、距交易日天數；可匯出 Excel（整張篩選結果）；按鈕「登記已寄出」「登記顧客贖回」「提出異議」，登記的入口只在這一頁。「批次登記已寄出」（#186）選一個交易日，把交易日在這天以前（含）、目前是待寄信的商品各寫一筆「已寄出」：先列出這批有幾檔、哪些 TDCC，確認後才寫；寫的時候已經登記過或已經解鎖的跳過；完成後提示同一個範圍裡還沒核對完、這次沒有登記的檔數。落後以距交易日天數排序，不設門檻。

**實作順序**：先完成 #138（每個分頁一個 module）再加進度頁。寄信草稿的建立條件不變，仍以商品資料夾的標準檔名 PDF 為準。

### 考慮過的替代方案

- **進度手填在交易清單或 Excel**：資料和程式分離，落後要人記得填；交易清單的「TS/IIS 核對」V 由人打，ADR 0003 已決定不依賴它。
- **「已寄出」讀 Outlook 寄件備份**：每人信箱不同、主旨可改，脆弱；手動標記就夠。
- **存續狀態只覆寫一個欄位**：歷史不見，KO 之後再接股這類多筆事件表示不了。
- **允許人工覆寫自動事件**：方便，但錯的會是程式邏輯或資料，覆寫會把問題蓋掉；改用異議。
- **每一步各一張表**：欄位幾乎相同，查進度要 join 三張。

### 後果

- 新增三張表與一個分頁，更新程式後要跑 migration。
- 作業人員每天多一個動作：寄出後到進度頁標記；忘了標會停在「待寄信」，進度頁看得到；寄信由其他人負責時，可以用「批次登記已寄出」一次補登某天以前的（#186）。
- 進度只反映資料庫知道的事：以前只用 Excel 跑過的步驟沒有紀錄，會顯示在較早的進度，可用「登記已寄出」補到完成。2026-10-23 起（#184）已鎖住的舊商品沒有步驟紀錄時直接算完成。
- 寄信草稿改成以「核對通過並回填」為準（ADR 0006 的過渡期）仍是另一個決定。
- CONTEXT.md 名詞段新增上述名詞。

## English

### Context

Finding out which step a product has reached means looking in six places: whether the trade rows have it, whether the product terms exist, the status in `workflow.check_terms`, `workflow.check_result`, `locked_at`, and whether the product folder holds standard-named TS/IIS PDFs. "3 準備", "5 放回", and "6 寄信草稿" write only Excel lists and files, with no database record; mail drafts decide "checked" by the presence of PDFs in the folder, not by the database's check results.

`workflow.product.status_event` is always written as 在庫 at creation, and since 清單更新 was retired ([ADR 0008](0008-daily-prices-denomination-and-order-dates.md)) no flow changes it. KO, delivery, and maturity are several events per product; [database and deployment](../database-and-deployment.md) already states they are one-to-many, but there is no event table. The open question on case and status naming was never settled; ADR 0006 deferred the progress tab until the single PANEL existed, and after the merge ([ADR 0007](0007-single-workflow-window.md)) nobody came back to it.

The user's goal: automate the whole path from start to finish, see every product's progress at every step, handle the laggards individually (backfill data, recheck), and let observation update the lifecycle status automatically ([ADR 0020](0020-close-prices-and-observation-rules.md)).

### Decision (confirmed by the user 2026-10-22)

**Terms**

- "Check-term status" (核對條件狀態) stays: 完成／等股價／等交易明細／待確認.
- "Workflow progress" (作業進度): which of the five stages a product is in (#186).
- "Lifecycle status" (存續狀態): 在庫／KO／接股／到期還本／顧客贖回; it replaces the older "inventory status" (庫存狀態), since the inventory master is retired.
- "Observation" (比價): the daily comparison of closes against KO and KI prices that produces lifecycle events (ADR 0020); 比價日 keeps its meaning, a date on which KO can occur.

**Workflow progress is derived from database facts, never filled in by hand.** Each product has exactly one progress value, evaluated in the order below; the first rule that applies is the current stage (cut from nine to five on 2026-10-23, #186):

| Progress | Rule |
|---|---|
| 完成 | 已寄出 exists; or a locked legacy product created by 清單更新 (ADR 0020) with no step records yet (confirmed 2026-10-23, #184: finished in Excel) |
| 1 資料準備中 | any of the three items needed before checking is not ready |
| 2 待核對 | all three are ready, no check result yet |
| 3 待修正 | check results exist but the product is not locked (it failed and waits to be fixed and checked again) |
| 4 待寄信 | locked (passed the check), no 已寄出 yet |
| 5 完成 | see the first row |

- The three items needed before checking run in parallel, with no order, and each has a tick column on the progress tab: trade rows (the product has trade rows); product terms (present with no required field blank, [ADR 0016](0016-partial-product-terms.md); a blank 期初價格 does not count once every underlying has an initial price, #182); prices (check terms are 完成). For 資料準備中, the reason column says which items are missing and what is missing.
- After checking, the PDFs are always returned (the final file on a pass, the pending file otherwise), so returning is no longer a stage of its own; mailing is done by someone else. The dates of preparation, checking, return, draft creation, and the other steps are still shown, the latest record of a step winning, but no longer decide the stage.

**Step records**: a new `workflow.step_record` (TDCC, step, time, Windows user name, result, related file), insert-only. "3 準備" writes 已準備, "5 放回" writes 已放回 (including pending files returned), and "6 寄信草稿" writes 草稿已建立, each at the moment it writes its Excel list; the Excel lists are still produced. 已寄出 is marked by the operator on the progress tab; Outlook is not read.

**Lifecycle events**: a new `workflow.product_event` (TDCC, event, event date, underlying, source, note, who, when). The lifecycle status is the latest event; no event means 在庫. The event date is the observation day on which the trigger or the determination happened; the issuer's actual redemption or payment date is not stored.

- Automatic events (KO, 接股, 到期還本) come only from observation: each run recalculates the product's automatic events in full and replaces them as a set only when they differ from the database, recording the change in `workflow.product_event_change` (who, when, old, new). Nobody may add, edit, or delete an automatic event by hand; when the result differs from the issuer's notice the operator can only raise an objection (`workflow.event_objection`: the event, note, who, when, resolved at), which shows as 待確認 on the progress tab; the fix is to correct the data or the program.
- The only manual event is 顧客贖回 (event date, note); after it is recorded the product is no longer observed.
- Statuses imported from the old 清單更新 become events with source 舊清單 (ADR 0020); `status_event`, `status_date`, `status_underlying`, and `status_raw` are no longer written and will be removed separately.

**Progress tab**: an "8 進度" tab filtered by trade-date range, workflow progress, lifecycle status, and issuer; one row per product showing each step's date, the current progress, and days since the trade date; export to Excel (the whole filtered result); buttons 登記已寄出, 登記顧客贖回, and 提出異議, the only place these are entered. 批次登記已寄出 (batch sent, #186) takes a trade date and writes one 已寄出 for every product traded on or before it that is currently 待寄信: it lists how many and which TDCCs first and writes only after confirmation, skips products already recorded or no longer locked at write time, and afterwards says how many products in the same range are not checked yet and were not recorded. Laggards are sorted by days since the trade date, with no threshold.

**Order of work**: finish #138 (one module per tab) before adding the progress tab. The mail-draft criteria are unchanged and still rely on standard-named PDFs in the product folder.

### Alternatives considered

- **Hand-filled progress in the trade list or an Excel file**: data and program drift apart, and laggards depend on someone remembering to fill it in; the trade list's "TS/IIS 核對" V is typed by hand, and ADR 0003 already decided not to rely on it.
- **Reading 已寄出 from Outlook's sent items**: mailboxes differ per person and subjects can be edited; fragile, and a manual mark is enough.
- **Overwriting a single status column**: history is lost, and sequences such as KO followed by delivery cannot be expressed.
- **Allowing manual overrides of automatic events**: convenient, but a wrong event means a program or data error, and overriding would hide it; objections instead.
- **One table per step**: nearly identical columns, and progress would need a three-way join.

### Consequences

- Three new tables and one new tab; run the migration after updating.
- Operators gain one daily action: marking 已寄出 after sending; a forgotten mark leaves the product at 待寄信, visible on the progress tab; when someone else does the mailing, 批次登記已寄出 records everything up to a day at once (#186).
- Progress reflects only what the database knows: steps run earlier through Excel alone have no records and show an earlier progress, which 登記已寄出 can bring to 完成. Since 2026-10-23 (#184), locked legacy products with no step records count as 完成 directly.
- Switching mail drafts to "passed and backfilled" (ADR 0006's transition) remains a separate decision.
- The CONTEXT.md glossary gains the terms above.
