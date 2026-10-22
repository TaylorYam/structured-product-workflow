"""FCN Workflow 視窗的流程分頁，一頁一個 module（#138）；視窗外殼在 `sp_workflow.app`。

- `base`：分頁版面 `Tab`、根目錄、資料庫登入、選檔對話框（各分頁共用）
- `daily`、`orders`、`prepare`、`returning`、`mail`、`manual`：1、2、3、5、6、7 頁（名稱同 `PanelWindow` 的屬性）
- `editor`：2、7 頁右邊的編輯區；`cell_table`：7 頁的檢查結果表；`recalc`：1、2 頁共用的核對條件重算顯示與「放行差異…」

核對頁「4 核對」是 `fcn_checker.panel.CheckTab`，不在這裡。
"""
