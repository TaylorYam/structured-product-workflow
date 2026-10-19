"""Windows 雙擊入口：上次選的資料夾記在這個安裝資料夾底下，不依賴啟動工作目錄。"""

import tkinter as tk
from pathlib import Path
from tkinter import messagebox

TITLE = "TS／IIS 準備與放回"


def _show_error(message: str) -> None:
    root = tk.Tk()
    root.withdraw()  # 只顯示錯誤視窗，不多跳一個空白視窗
    messagebox.showerror(TITLE, message, parent=root)
    root.destroy()


try:
    from sp_workflow.app import main
except Exception as error:
    _show_error(f"無法啟動，請先雙擊 setup_panel.cmd 完成安裝。\n\n{error}")
else:
    try:
        main(["--install-root", str(Path(__file__).resolve().parent)])
    except Exception as error:
        _show_error(f"執行時發生錯誤，請把這段訊息交給維護人員。\n\n{error!r}")
