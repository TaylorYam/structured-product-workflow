# 0007: 合併成一個 FCN Workflow 視窗，作業人員也使用核對／One FCN Workflow Window, Checking Included for Operators

- 狀態／Status: Accepted
- 日期／Date: 2026-10-20
- 追蹤／Tracking: #73、#74
- 取代／Supersedes: [核對 ADR 0006](../checker/adr/0006-panel-maintainer-only-no-self-update.md)「PANEL 只供維護者使用」那一點；移除自動更新照舊／the "PANEL is for the maintainer only" point of checker ADR 0006; removing self-update still stands

## 繁體中文

### 背景

併入核對專案後（[ADR 0005](0005-merge-checker-into-workflow.md)）有兩個視窗：「TS／IIS 準備與放回」（準備、放回、每日匯入、寄信草稿、Order 條件）與核對 PANEL。核對 ADR 0006 決定核對 PANEL 只有維護者本人使用，審查通過日期、匯出回報包與設定檔路徑都直接放在畫面上。使用者希望作業人員照每天的順序，在同一個視窗做完整個流程，包括核對（2026-10-20 確認，#73）。

### 決策

- 一個「FCN Workflow」視窗，6 個分頁照作業時間排：1 Order 條件 → 2 每日匯入 → 3 準備 → 4 核對 → 5 放回 → 6 寄信草稿。核對頁沿用原核對視窗的版面；各分頁的功能不變。
- 作業人員也使用核對。審查通過日期、匯出回報包與使用的設定檔不是每天的步驟，收進視窗上方的「維護」選單，不設密碼。
- 視窗外殼在 `sp_workflow.app`；核對頁是 `fcn_checker.panel.CheckTab`，建在分頁裡，不再自己擁有 Tk 主視窗。視窗大小、字型、DPI 與圖示沿用原核對視窗，並記住上次調整的大小。
- 關閉視窗時，有還沒存檔的核對結果先提醒；切換分頁不清除各頁內容。
- 開發用指令保留：`spw-panel` 開合併視窗；`fcn-panel` 與核對的雙擊入口開合併視窗並停在核對頁，參數照舊。
- 更新方式不變：`git pull` 後重新執行 `setup_panel.cmd`（核對 ADR 0006）。

### 考慮過的替代方案

- **保留兩個視窗，只加互相開啟的按鈕**：作業人員還是要在兩個視窗之間搬路徑，做完一步帶到下一步（#76）做不到。
- **維護功能加密碼**：只有一位維護者，密碼要另外保管，只多擋「不小心按到」；收進選單就夠了。
- **核對頁另開一個程式，由外殼啟動**：兩個程式之間要另外傳資料，又回到 ADR 0005 想避免的問題。

### 後果

- 作業人員每天只開一個視窗；核對的規則、輸出與測試不變。
- 作業人員在自己的電腦改審查通過日期時，`config/review_standard.toml` 只改在那台電腦；要請維護者提交到 main，否則之後 `git pull` 會衝突。
- 後續工作：根目錄整個視窗只設一次（#75）、做完一步帶到下一步（#76）、只留「FCN Workflow」捷徑（#77）。

## English

### Context

After the checker merge ([ADR 0005](0005-merge-checker-into-workflow.md)) there were two windows: "TS／IIS 準備與放回" (preparation, returning, daily import, mail drafts, Order terms) and the checker PANEL. Checker ADR 0006 made the checker PANEL maintainer-only, with approval dates, report bundle export, and configuration paths shown directly on screen. The user wants operators to finish the whole flow, checking included, in one window following the daily order (confirmed 2026-10-20, #73).

### Decision

- One "FCN Workflow" window with six tabs in working order: 1 Order 條件 → 2 每日匯入 → 3 準備 → 4 核對 → 5 放回 → 6 寄信草稿. The check tab keeps the former checker window layout; every tab keeps its behavior.
- Operators also use checking. Approval dates, report bundle export, and the configuration paths are not daily steps, so they move into a 「維護」 (maintenance) menu at the top of the window, without a password.
- The window shell lives in `sp_workflow.app`; the check tab is `fcn_checker.panel.CheckTab`, built inside a tab instead of owning the Tk root. Window size, fonts, DPI, and icon follow the former checker window, and the last size is remembered.
- Closing the window warns when a check result has not been saved; switching tabs keeps each tab's state.
- Development commands remain: `spw-panel` opens the merged window; `fcn-panel` and the checker double-click entry open it on the check tab, with the same arguments as before.
- Updating is unchanged: `git pull`, then rerun `setup_panel.cmd` (checker ADR 0006).

### Alternatives considered

- **Keep two windows with buttons that open each other**: operators would still carry paths between windows, and handing results to the next step (#76) would not be possible.
- **Password-protect maintenance**: with one maintainer, the password needs separate custody and only guards against accidental clicks; a menu is enough.
- **Run the check tab as a separate program launched by the shell**: the programs would need their own data exchange, the problem ADR 0005 set out to avoid.

### Consequences

- Operators open one window each day; checking rules, outputs, and tests are unchanged.
- When an operator edits approval dates on their own computer, `config/review_standard.toml` changes only there; the maintainer must commit it to main, or a later `git pull` will conflict.
- Follow-up work: set the root folder once for the whole window (#75), hand each step's results to the next (#76), and keep only the "FCN Workflow" shortcut (#77).
