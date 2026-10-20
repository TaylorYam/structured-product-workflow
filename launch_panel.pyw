"""Windows 雙擊入口：開 FCN Workflow 視窗；設定與上次選的資料夾以這個安裝資料夾為準，不依賴啟動工作目錄。"""

import tkinter as tk
from tkinter import messagebox

TITLE = "FCN Workflow"


def _show_error(message: str) -> None:
    root = tk.Tk()
    root.withdraw()  # 只顯示錯誤視窗，不多跳一個空白視窗
    messagebox.showerror(TITLE, message, parent=root)
    root.destroy()


try:
    from panel_bootstrap import arguments

    from sp_workflow.app import main
except Exception as error:
    _show_error(f"無法啟動，請先雙擊 setup_panel.cmd 完成安裝。\n\n{error}")
else:
    try:
        main(arguments(__file__))
    except Exception as error:
        _show_error(f"執行時發生錯誤，請把這段訊息交給維護人員。\n\n{error!r}")
