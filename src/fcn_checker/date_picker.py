"""日期欄位旁的小日曆（#192）：輸入框照樣可以打字，旁邊「日曆」按鈕跳出月曆，點一天填入 YYYY-MM-DD。

月曆停在欄位裡已有的日期那個月（沒有或看不懂時停在這個月），可以換上一月、下一月，有「今天」與「清除」。
選了（含清除）就關掉月曆並呼叫 `on_pick`，和在欄位按 Enter 做一樣的事；按 Esc 或點月曆外面只關掉、不改欄位。
不用額外套件，用 tkinter 自己畫；核對畫面與流程分頁共用。
"""

from __future__ import annotations

import calendar
import datetime as dt
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from .ui import ACCENT, BORDER, FONT, HOVER, SURFACE, TEXT, TEXT_SECONDARY

WEEKDAYS = "日一二三四五六"  # 星期日開頭，和台灣的月曆一樣


def month_weeks(year: int, month: int) -> list[list[dt.date | None]]:
    """這個月的每一週（星期日開頭），不是這個月的格子是 None。"""
    weeks = calendar.Calendar(firstweekday=6).monthdayscalendar(year, month)
    return [[dt.date(year, month, day) if day else None for day in week] for week in weeks]


def parse_day(text: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(text.strip())
    except ValueError:
        return None


def shift_month(year: int, month: int, step: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + step
    return index // 12, index % 12 + 1


class DatePopup(tk.Toplevel):
    """跳在日期欄位下方的月曆；選了一天就把 YYYY-MM-DD 寫進 `var`，再呼叫 `on_pick`。"""

    def __init__(
        self,
        anchor: tk.Widget,
        var: tk.StringVar,
        on_pick: Callable[[], object] | None = None,
        *,
        today: dt.date | None = None,
    ) -> None:
        super().__init__(anchor)
        self.var = var
        self.on_pick = on_pick
        self.today = today or dt.date.today()
        self.chosen = parse_day(var.get())
        start = self.chosen or self.today
        self.year, self.month = start.year, start.month
        self._previous_grab = anchor.grab_current()  # 從對話框打開時，關掉後把 grab 還給它
        self.withdraw()  # 算好位置再顯示；沒有標題列的視窗先顯示會停在螢幕左上角
        self.overrideredirect(True)
        self.configure(background=BORDER)
        self.transient(anchor.winfo_toplevel())
        frame = tk.Frame(self, background=SURFACE, padx=8, pady=8)
        frame.pack(padx=1, pady=1)
        top = tk.Frame(frame, background=SURFACE)
        top.pack(fill="x")
        ttk.Button(top, text="‹", width=3, command=lambda: self.show_month(-1)).pack(side="left")
        self.title_var = tk.StringVar()
        tk.Label(top, textvariable=self.title_var, background=SURFACE, foreground=TEXT, font=(FONT, 10, "bold")).pack(
            side="left", expand=True
        )
        ttk.Button(top, text="›", width=3, command=lambda: self.show_month(1)).pack(side="right")
        self.grid_frame = tk.Frame(frame, background=SURFACE)
        self.grid_frame.pack(pady=(6, 6))
        bottom = tk.Frame(frame, background=SURFACE)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="今天", command=lambda: self.pick(self.today)).pack(side="left")
        ttk.Button(bottom, text="清除", command=lambda: self.pick(None)).pack(side="right")
        self.cells: dict[dt.date, tk.Label] = {}
        self.show_month(0)
        self.update_idletasks()
        x, y = anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height() + 2
        x = min(x, self.winfo_screenwidth() - self.winfo_reqwidth())
        if y + self.winfo_reqheight() > self.winfo_screenheight():
            y = anchor.winfo_rooty() - self.winfo_reqheight() - 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.deiconify()
        self.lift()  # 沒有標題列的視窗在 Windows 上可能排到主視窗後面
        self.bind("<Escape>", lambda _event: self.close())
        self.bind("<ButtonPress>", self._click_outside)
        try:
            self.grab_set()  # 點月曆外面也送到這裡，才能關掉
        except tk.TclError:  # 看不到的視窗（例如測試）抓不了；月曆照樣能用
            pass
        self.focus_force()

    def show_month(self, step: int) -> None:
        """換到前（-1）、後（1）一個月，0 是重畫這個月。"""
        self.year, self.month = shift_month(self.year, self.month, step)
        self.title_var.set(f"{self.year} 年 {self.month} 月")
        for widget in self.grid_frame.winfo_children():
            widget.destroy()
        self.cells = {}
        for column, name in enumerate(WEEKDAYS):
            tk.Label(
                self.grid_frame, text=name, width=3, background=SURFACE, foreground=TEXT_SECONDARY, font=(FONT, 9)
            ).grid(row=0, column=column)
        for row, week in enumerate(month_weeks(self.year, self.month), 1):
            for column, day in enumerate(week):
                if day is None:
                    continue
                chosen = day == self.chosen
                cell = tk.Label(
                    self.grid_frame,
                    text=str(day.day),
                    width=3,
                    pady=3,
                    cursor="hand2",
                    background=ACCENT if chosen else SURFACE,
                    foreground=SURFACE if chosen else ACCENT if day == self.today else TEXT,
                    font=(FONT, 10, "bold" if day == self.today else "normal"),
                )
                cell.grid(row=row, column=column)
                cell.bind("<Button-1>", lambda _event, day=day: self.pick(day))
                if not chosen:
                    cell.bind("<Enter>", lambda _event, cell=cell: cell.configure(background=HOVER))
                    cell.bind("<Leave>", lambda _event, cell=cell: cell.configure(background=SURFACE))
                self.cells[day] = cell

    def pick(self, day: dt.date | None) -> None:
        """選了這一天（None 是清除）：寫進欄位、關掉月曆，再呼叫 `on_pick`。"""
        self.var.set(day.isoformat() if day else "")
        self.close()
        if self.on_pick is not None:
            self.on_pick()

    def close(self) -> None:
        previous = self._previous_grab
        self.grab_release()
        self.destroy()
        if previous is not None and previous.winfo_exists():
            previous.grab_set()

    def _click_outside(self, event: tk.Event) -> None:
        if not self.winfo_exists():
            return
        inside = self.winfo_containing(event.x_root, event.y_root)
        if inside is None or not str(inside).startswith(str(self)):
            self.close()


class DateEntry(ttk.Frame):
    """日期輸入框＋旁邊的「日曆」按鈕（#192）；`entry` 是輸入框本身，呼叫端照舊 bind Enter 等事件。"""

    def __init__(
        self,
        parent: tk.Misc,
        var: tk.StringVar,
        *,
        width: int = 12,
        on_pick: Callable[[], object] | None = None,
        style: str = "",
    ) -> None:
        super().__init__(parent, **({"style": style} if style else {}))
        self.var = var
        self.on_pick = on_pick
        self.popup: DatePopup | None = None
        self.entry = ttk.Entry(self, textvariable=var, width=width)
        self.entry.pack(side="left")
        self.button = ttk.Button(self, text="日曆", width=4, command=self.open)
        self.button.pack(side="left", padx=(4, 0))

    def open(self) -> DatePopup:
        """跳出月曆（已經開著就先關掉舊的）。"""
        if self.popup is not None and self.popup.winfo_exists():
            self.popup.close()
        self.popup = DatePopup(self.entry, self.var, self.on_pick)
        return self.popup
