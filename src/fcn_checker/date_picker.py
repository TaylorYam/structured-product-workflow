"""日期欄位的小日曆（#192、#198）：輸入框照樣可以打字，右邊的小月曆圖示跳出月曆，點一天填入 YYYY-MM-DD。

輸入框和圖示是同一格（聚焦時外框變藍）。月曆停在欄位裡已有的日期那個月（沒有或看不懂時停在這個月），
可以換上一月、下一月（也可以滾滑鼠滾輪），前後月的日子淡灰、也能點；選到的日期藍底圓圈、今天藍框，底下有「今天」與「清除」。
選了（含清除）就關掉月曆並呼叫 `on_pick`，和在欄位按 Enter 做一樣的事；按 Esc 或點月曆外面只關掉、不改欄位。
不用額外套件，用 tkinter 自己畫；核對畫面與流程分頁共用。
"""

from __future__ import annotations

import datetime as dt
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from .ui import (
    ACCENT,
    ACCENT_ACTIVE,
    ACCENT_SOFT,
    BODY_FONT,
    BORDER,
    FONT,
    HOVER,
    NUMBER_FONT,
    SURFACE,
    TEXT,
    TEXT_DISABLED,
    TEXT_SECONDARY,
    FieldBox,
)

WEEKDAYS = "日一二三四五六"  # 星期日開頭，和台灣的月曆一樣
CELL = 34  # 一天一格的邊長（96 DPI 的像素，依螢幕縮放）
WEEK_ROWS = 6  # 固定六週，換月時月曆不會忽大忽小


def month_grid(year: int, month: int) -> list[dt.date]:
    """月曆上的 42 天（六週、星期日開頭），前後補上個月與下個月的日子。"""
    first = dt.date(year, month, 1)
    start = first - dt.timedelta(days=(first.weekday() + 1) % 7)
    return [start + dt.timedelta(days=i) for i in range(7 * WEEK_ROWS)]


def parse_day(text: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(text.strip())
    except ValueError:
        return None


def shift_month(year: int, month: int, step: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + step
    return index // 12, index % 12 + 1


def _scale(widget: tk.Misc) -> float:
    return widget.winfo_fpixels("1i") / 96


def _link(parent: tk.Misc, text: str, color: str, command: Callable[[], object], **options) -> tk.Label:
    """平的文字按鈕：滑過變底色，點了呼叫 `command`。"""
    label = tk.Label(parent, text=text, background=SURFACE, foreground=color, cursor="hand2", **options)
    label.bind("<Button-1>", lambda _event: command())
    label.bind("<Enter>", lambda _event: label.configure(background=HOVER))
    label.bind("<Leave>", lambda _event: label.configure(background=SURFACE))
    return label


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
        scale = _scale(self)
        self.cell = round(CELL * scale)
        pad = round(12 * scale)
        frame = tk.Frame(self, background=SURFACE, padx=pad, pady=pad)
        frame.pack(padx=1, pady=1)

        top = tk.Frame(frame, background=SURFACE)
        top.pack(fill="x", pady=(0, round(6 * scale)))
        self.title_var = tk.StringVar()
        tk.Label(top, textvariable=self.title_var, background=SURFACE, foreground=TEXT, font=(FONT, 11, "bold")).pack(
            side="left", padx=(round(4 * scale), 0)
        )
        arrow = {"font": (NUMBER_FONT, 16), "width": 2}
        _link(top, "›", TEXT_SECONDARY, lambda: self.show_month(1), **arrow).pack(side="right")
        _link(top, "‹", TEXT_SECONDARY, lambda: self.show_month(-1), **arrow).pack(side="right")

        header = round(24 * scale)
        self.top_offset = header
        self.canvas = tk.Canvas(
            frame,
            width=self.cell * 7,
            height=header + self.cell * WEEK_ROWS,
            background=SURFACE,
            highlightthickness=0,
            cursor="hand2",
        )
        self.canvas.pack()
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Leave>", lambda _event: self.canvas.itemconfigure("hover", state="hidden"))
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<MouseWheel>", lambda event: self.show_month(-1 if event.delta > 0 else 1))

        tk.Frame(frame, background=BORDER, height=1).pack(fill="x", pady=(round(6 * scale), round(4 * scale)))
        bottom = tk.Frame(frame, background=SURFACE)
        bottom.pack(fill="x")
        link = {"font": (FONT, 10), "padx": round(8 * scale), "pady": round(3 * scale)}
        _link(bottom, "今天", ACCENT, lambda: self.pick(self.today), **link).pack(side="left")
        _link(bottom, "清除", TEXT_SECONDARY, lambda: self.pick(None), **link).pack(side="right")

        self.days: list[dt.date] = []
        self.cells: dict[dt.date, int] = {}  # 每一天的數字（canvas 上的文字）
        self.show_month(0)
        self.update_idletasks()
        x, y = anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height() + 4
        x = min(x, self.winfo_screenwidth() - self.winfo_reqwidth())
        if y + self.winfo_reqheight() > self.winfo_screenheight():
            y = anchor.winfo_rooty() - self.winfo_reqheight() - 4
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
        canvas, cell, top = self.canvas, self.cell, self.top_offset
        canvas.delete("all")
        for column, name in enumerate(WEEKDAYS):
            canvas.create_text((column + 0.5) * cell, top / 2, text=name, fill=TEXT_SECONDARY, font=(FONT, 9))
        radius = cell / 2 - max(2, cell // 12)
        canvas.create_oval(0, 0, 0, 0, fill=HOVER, outline="", state="hidden", tags="hover")
        self.days = month_grid(self.year, self.month)
        self.cells = {}
        for index, day in enumerate(self.days):
            x = (index % 7 + 0.5) * cell
            y = top + (index // 7 + 0.5) * cell
            this_month = day.month == self.month
            color = TEXT if this_month else TEXT_DISABLED
            weight = "normal"
            if day == self.chosen:
                canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=ACCENT, outline="")
                color, weight = SURFACE, "bold"
            elif day == self.today:
                canvas.create_oval(
                    x - radius, y - radius, x + radius, y + radius, fill=ACCENT_SOFT, outline=ACCENT, width=1
                )
                color, weight = ACCENT, "bold"
            self.cells[day] = canvas.create_text(x, y, text=str(day.day), fill=color, font=(NUMBER_FONT, 10, weight))

    def day_at(self, x: float, y: float) -> dt.date | None:
        """canvas 上這一點是哪一天（星期列與外面是 None）。"""
        row, column = int((y - self.top_offset) // self.cell), int(x // self.cell)
        if y < self.top_offset or not (0 <= row < WEEK_ROWS and 0 <= column < 7):
            return None
        return self.days[row * 7 + column]

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

    def _hover(self, event: tk.Event) -> None:
        day = self.day_at(event.x, event.y)
        if day is None or day in (self.chosen, self.today):
            self.canvas.itemconfigure("hover", state="hidden")
            return
        x, y = self.canvas.coords(self.cells[day])
        radius = self.cell / 2 - max(2, self.cell // 12)
        self.canvas.coords("hover", x - radius, y - radius, x + radius, y + radius)
        self.canvas.itemconfigure("hover", state="normal")

    def _click(self, event: tk.Event) -> None:
        day = self.day_at(event.x, event.y)
        if day is not None:
            self.pick(day)

    def _click_outside(self, event: tk.Event) -> None:
        if not self.winfo_exists():
            return
        inside = self.winfo_containing(event.x_root, event.y_root)
        if inside is None or not str(inside).startswith(str(self)):
            self.close()


class CalendarIcon(tk.Canvas):
    """日期欄位右邊的小月曆圖示；滑過變藍，點了呼叫 `command`。"""

    def __init__(self, parent: tk.Misc, command: Callable[[], object]) -> None:
        size = round(18 * _scale(parent))
        super().__init__(parent, width=size, height=size, background=SURFACE, highlightthickness=0, cursor="hand2")
        unit = size / 18
        line = max(1.0, 1.4 * unit)

        def at(*points: float) -> list[float]:
            return [p * unit for p in points]

        self.create_rectangle(*at(2.5, 4, 15.5, 15.5), width=line, tags="frame")
        self.create_line(*at(2.5, 7.5, 15.5, 7.5), width=line, tags="stroke")
        self.create_line(*at(6, 2, 6, 5.5), width=line, capstyle="round", tags="stroke")
        self.create_line(*at(12, 2, 12, 5.5), width=line, capstyle="round", tags="stroke")
        for x in (6, 9, 12):
            for y in (10.5, 13):
                self.create_rectangle(*at(x - 0.8, y - 0.6, x + 0.8, y + 0.6), width=0, tags="dot")
        self.paint(TEXT_SECONDARY)
        self.bind("<Button-1>", lambda _event: command())
        self.bind("<Enter>", lambda _event: self.paint(ACCENT))
        self.bind("<Leave>", lambda _event: self.paint(TEXT_SECONDARY))

    def paint(self, color: str) -> None:
        self.itemconfigure("frame", outline=color)
        self.itemconfigure("stroke", fill=color)
        self.itemconfigure("dot", fill=color)


class DateEntry(ttk.Frame):
    """日期輸入框＋右邊的小月曆圖示，看起來是同一格（#192、#198）；`entry` 是輸入框本身，呼叫端照舊 bind Enter 等事件。"""

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
        scale = _scale(self)
        self.box = FieldBox(self)  # 和其他輸入框一樣的圓角外框，聚焦時變藍
        self.box.pack()
        self.entry = tk.Entry(
            self.box,
            textvariable=var,
            width=width,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            background=SURFACE,
            foreground=TEXT,
            insertbackground=TEXT,
            selectbackground=ACCENT_SOFT,
            selectforeground=TEXT,
            disabledbackground=SURFACE,
            font=BODY_FONT,
        )
        self.entry.pack(side="left", padx=(round(4 * scale), round(4 * scale)), pady=round(2 * scale))
        self.button = CalendarIcon(self.box, self.open)
        self.button.pack(side="left", padx=(0, round(4 * scale)))
        self.box.watch(self.entry)
        self.box.bind("<Button-1>", lambda _event: self.entry.focus_set())

    def open(self) -> DatePopup:
        """跳出月曆（已經開著就先關掉舊的）。"""
        if self.popup is not None and self.popup.winfo_exists():
            self.popup.close()
        self.button.paint(ACCENT_ACTIVE)
        self.popup = DatePopup(self.box, self.var, self.on_pick)
        self.popup.bind("<Destroy>", lambda _event: self._closed(), add="+")
        return self.popup

    def _closed(self) -> None:
        if self.button.winfo_exists():
            self.button.paint(TEXT_SECONDARY)
