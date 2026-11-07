"""新增、修改用的小視窗底座（#481）：信箱維護、發行機構評等、拆股登記的表單都繼承 `Form`，用 `ask_form` 開。

`Form` 由上往下一列一列排（`entry`、`day_field`、`note`，或子類別自己 grid 在 `self.row` 再加一），
最後 `finish_layout` 放錯誤訊息與確定／取消、置中在開它的視窗上。`read` 丟的 `OperatorError` 寫在視窗裡、不關。
進度頁的 `progress_dialogs.FormDialog` 版面不同，只共用置中 `center_over`。
"""

from __future__ import annotations

import datetime as dt
import tkinter as tk
from tkinter import ttk

from fcn_checker.date_picker import DateEntry, parse_day
from fcn_checker.ui import DANGER, WrapLabel
from sp_workflow.database import OperatorError


def center_over(dialog: tk.Toplevel, parent: tk.Misc) -> None:
    """把小視窗放在 parent 所在視窗的中間偏上（水平置中、垂直三分之一）。"""
    dialog.update_idletasks()
    top = parent.winfo_toplevel()
    x = top.winfo_rootx() + (top.winfo_width() - dialog.winfo_width()) // 2
    y = top.winfo_rooty() + (top.winfo_height() - dialog.winfo_height()) // 3
    dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")


class Form(tk.Toplevel):
    """新增、修改用的小視窗：按確定（或 Enter）時讀輸入，有錯寫在視窗裡、不關；取消（或 Esc）關掉。"""

    def __init__(self, parent: tk.Misc, title: str) -> None:
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.result: object = None
        self.body = ttk.Frame(self, padding=16)
        self.body.pack(fill=tk.BOTH, expand=True)
        self.body.columnconfigure(1, weight=1)
        self.error_var = tk.StringVar()
        self.fields: list[tk.Widget] = []
        self.row = 0

    def label(self, text: str) -> None:
        """這一列左邊的欄名；右邊的輸入由呼叫端 grid 在 `self.row`、column 1。"""
        ttk.Label(self.body, text=text).grid(row=self.row, column=0, sticky="w", pady=(0, 8), padx=(0, 12))

    def entry(self, label: str, value: str, width: int = 40) -> tk.StringVar:
        var = tk.StringVar(value=value)
        self.label(label)
        field = ttk.Entry(self.body, textvariable=var, width=width)
        field.grid(row=self.row, column=1, sticky="w" if width < 40 else "ew", pady=(0, 8))
        self.fields.append(field)
        self.row += 1
        return var

    def day_field(self, label: str, value: str) -> tk.StringVar:
        var = tk.StringVar(value=value)
        self.label(label)
        day = DateEntry(self.body, var, width=14)
        day.grid(row=self.row, column=1, sticky="w", pady=(0, 8))
        self.fields.append(day.entry)
        self.row += 1
        return var

    def note(self, text: str, wraplength: int = 400) -> None:
        WrapLabel(self.body, text=text, wraplength=wraplength, justify="left").grid(
            row=self.row, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        self.row += 1

    def finish_layout(self) -> None:
        ttk.Label(self.body, textvariable=self.error_var, foreground=DANGER).grid(
            row=self.row, column=0, columnspan=2, sticky="w"
        )
        buttons = ttk.Frame(self.body)
        buttons.grid(row=self.row + 1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="確定", command=self.confirm).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side=tk.LEFT)
        self.bind("<Escape>", lambda _: self.destroy())
        self.bind("<Return>", lambda _: self.confirm())
        center_over(self, self.master)  # 置中在開它的視窗上
        self.fields[0].focus_set()

    def read(self) -> object:
        raise NotImplementedError

    def confirm(self) -> None:
        try:
            self.result = self.read()
        except OperatorError as exc:
            self.error_var.set(str(exc))
            return
        self.destroy()


def ask_form(form: Form) -> object:
    """開著表單等作業人員按確定或取消；回傳 `read` 的結果，取消時 None。"""
    form.grab_set()
    form.master.wait_window(form)
    return form.result


def read_day(text: str, *, blank: dt.date | None = None) -> dt.date:
    """生效日欄：YYYY-MM-DD；`blank` 有值時空白（或「最初」）就是它。"""
    if blank is not None and text.strip() in ("", "最初"):
        return blank
    day = parse_day(text)
    if day is None:
        raise OperatorError("生效日請填 YYYY-MM-DD（可以點右邊的小月曆）。")
    return day
