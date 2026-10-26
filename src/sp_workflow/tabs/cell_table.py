"""人工維護的檢查結果表 `CellTable`（#115）：用 Canvas 畫的表，每一格可以上色，TDCC 那一欄固定在左邊。"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from tkinter import font as tkfont
from tkinter import ttk

from fcn_checker.ui import (
    ACCENT_SOFT,
    BACKGROUND,
    BORDER,
    FONT,
    HEADING_FONT,
    SURFACE,
    TEXT,
    TEXT_SECONDARY,
    CopyMenu,
    FitTreeview,
    Scrollbar,
    tab_separated,
    to_clipboard,
)
from sp_workflow.tabs.editor import PROBLEM_BG


class CellTable(ttk.Frame):
    """人工維護的檢查結果表（#115）：一列一檔，可以替每一格上色（缺的格子淺紅底）。

    `ttk.Treeview` 只能整列上色，所以用 Canvas 自己畫：第一欄（TDCC）固定在左邊，其他欄可以左右捲動，
    表頭跟著左右捲、不跟著上下捲；上下捲時左右兩塊一起動。點一列就選它並通知 `on_select`（傳那一列的 key）。
    按右鍵可以複製那一格、那一列或整張表（含表頭），不改變選取的列（#121）。
    """

    PAD = 8  # 格子左右的留白
    MIN_WIDTH = 48

    def __init__(
        self,
        parent: tk.Misc,
        first: tuple[str, str],
        columns: Sequence[tuple[str, str]],
        on_select: Callable[[str], None],
    ) -> None:
        super().__init__(parent, style="CardBody.TFrame")  # 外面的 CopyArea 已經是白色卡片
        self.first = first  # (欄, 表頭)：固定在左邊
        self.columns = tuple(columns)  # [(欄, 表頭)]：可以左右捲動
        self.on_select = on_select
        self.font = tkfont.Font(family=FONT, size=10)
        self.header_font = tkfont.Font(font=HEADING_FONT)  # 和其他結果表的表頭一樣
        self.row_height = self.font.metrics("linespace") + 14
        self.keys: list[str] = []
        self.selected: str | None = None
        self._rects: dict[tuple[str, str], tuple[tk.Canvas, int]] = {}  # (key, 欄) → 格子的方塊
        self._labels: dict[tuple[str, str], tuple[tk.Canvas, int]] = {}  # (key, 欄) → 格子的字
        self._missing: dict[str, frozenset[str]] = {}
        self._cells: dict[str, Mapping[str, str]] = {}  # key → {欄: 顯示的字}（複製用）
        self._edges: list[tuple[int, str]] = []  # 可以左右捲的欄：(右邊界, 欄)
        self.menu = CopyMenu(self, self.copy_all)

        def canvas() -> tk.Canvas:
            return tk.Canvas(self, background=SURFACE, highlightthickness=0, borderwidth=0)

        self.corner, self.header, self.side, self.body = canvas(), canvas(), canvas(), canvas()
        for scrolling in (self.side, self.body):
            scrolling.configure(yscrollincrement=self.row_height)
        self.vbar = Scrollbar(self, orient=tk.VERTICAL, command=self._yview)
        self.hbar = Scrollbar(self, orient=tk.HORIZONTAL, command=self._xview)
        self.body.configure(yscrollcommand=self.vbar.set, xscrollcommand=self.hbar.set)
        self.corner.grid(row=0, column=0, sticky="nsew")
        self.header.grid(row=0, column=1, sticky="ew")
        self.side.grid(row=1, column=0, sticky="ns")
        self.body.grid(row=1, column=1, sticky="nsew")
        self.vbar.grid(row=1, column=2, sticky="ns")
        self.hbar.grid(row=2, column=1, sticky="ew")
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        self.placeholder = ttk.Label(self, text=FitTreeview.EMPTY, style="Placeholder.TLabel")  # 和其他結果表一樣
        for widget in (self.side, self.body):
            widget.bind("<Button-1>", self._click)
            widget.bind("<Button-3>", self._popup)
            widget.bind("<MouseWheel>", lambda event: self._yview("scroll", self._steps(event), "units"))
            widget.bind("<Shift-MouseWheel>", lambda event: self._xview("scroll", self._steps(event) * 3, "units"))
        self.set_rows(())

    def set_rows(self, rows: Sequence[tuple[str, Mapping[str, str], Collection[str]]]) -> None:
        """換成這些列：(key, {欄: 顯示的字}, 缺的欄)；key 也是第一欄顯示的字。選中的那列還在就照樣選著。"""
        for widget in (self.corner, self.header, self.side, self.body):
            widget.delete("all")
        self._rects, self._labels, self._edges = {}, {}, []
        self.keys = [key for key, _, _ in rows]
        self._missing = {key: frozenset(gone) for key, _, gone in rows}
        self._cells = {key: cells for key, cells, _ in rows}
        first, first_label = self.first
        first_width = self._width(first_label, self.keys)
        widths = [self._width(label, [cells.get(name, "") for _, cells, _ in rows]) for name, label in self.columns]
        height = self.row_height
        self._header(self.corner, 0, first_width, first_label)
        x = 0
        for (name, label), width in zip(self.columns, widths, strict=True):
            self._header(self.header, x, width, label)
            x += width
            self._edges.append((x, name))
        for i, (key, cells, _) in enumerate(rows):
            y = i * height
            self._cell(self.side, key, first, 0, y, first_width, key)
            x = 0
            for (name, _), width in zip(self.columns, widths, strict=True):
                self._cell(self.body, key, name, x, y, width, cells.get(name, ""))
                x += width
        total = len(rows) * height
        self.corner.configure(width=first_width, height=height, scrollregion=(0, 0, first_width, height))
        self.side.configure(width=first_width, scrollregion=(0, 0, first_width, total))
        self.header.configure(height=height, scrollregion=(0, 0, x, height))
        self.body.configure(scrollregion=(0, 0, x, total))
        if self.selected not in self._missing:
            self.selected = None
        if rows:
            self.placeholder.place_forget()
        else:
            self.placeholder.place(relx=0.5, rely=0.5, anchor="center")
        self._paint(self.selected, selected=True)

    def select(self, key: str | None) -> None:
        """選這一列（淺藍底，缺的格子照樣淺紅）；不在表上或 None 時不選任何一列。不通知 `on_select`。"""
        self._paint(self.selected, selected=False)
        self.selected = key if key in self._missing else None
        self._paint(self.selected, selected=True)

    def background(self, key: str, column: str) -> str:
        canvas, item = self._rects[(key, column)]
        return str(canvas.itemcget(item, "fill"))

    def text(self, key: str, column: str) -> str:
        canvas, item = self._labels[(key, column)]
        return str(canvas.itemcget(item, "text"))

    def rows(self, keys: Iterable[str] | None = None, *, header: bool = False) -> list[list[str]]:
        """表上的列（照畫面的順序）；`header` 時第一列是表頭。"""
        names = [name for name, _ in self.columns]
        rows = [[self.first[1], *(label for _, label in self.columns)]] if header else []
        for key in self.keys if keys is None else keys:
            rows.append([key, *(self._cells[key].get(name, "") for name in names)])
        return rows

    def copy_all(self) -> int:
        """整張表（含表頭）放進剪貼簿；回傳幾列（不算表頭），表上沒有列時不動剪貼簿。"""
        if self.keys:
            to_clipboard(self, tab_separated(self.rows(header=True)))
        return len(self.keys)

    def fill_menu(self, key: str | None, column: str | None) -> None:
        """右鍵點到的格子（點到表外是 None）→ 選單的項目；不改變選取的列。"""
        if key is None:
            self.menu.fill(None)
            return
        cell = key if column == self.first[0] else self._cells[key].get(column or "", "")
        self.menu.fill(self.rows([key])[0], cell)

    def _popup(self, event: tk.Event) -> None:
        canvas = event.widget
        index = int(canvas.canvasy(event.y) // self.row_height)
        key = self.keys[index] if 0 <= index < len(self.keys) else None
        if canvas is self.side:
            column = self.first[0]
        else:
            x = canvas.canvasx(event.x)
            column = next((name for right, name in self._edges if x < right), None)
        self.fill_menu(key, column)
        self.menu.tk_popup(event.x_root, event.y_root)

    def _choose(self, key: str) -> None:
        """點了一列：選它，再通知 `on_select`（分頁決定要不要換檔，不換時用 `select` 改回去）。"""
        self.select(key)
        self.on_select(key)

    def _click(self, event: tk.Event) -> None:
        index = int(event.widget.canvasy(event.y) // self.row_height)
        if 0 <= index < len(self.keys):
            self._choose(self.keys[index])

    def _yview(self, *args: object) -> None:
        self.side.yview(*args)
        self.body.yview(*args)

    def _xview(self, *args: object) -> None:
        self.header.xview(*args)
        self.body.xview(*args)

    @staticmethod
    def _steps(event: tk.Event) -> int:
        """滾輪轉一格（Windows 是 ±120）→ 捲一列；方向和 Windows 一樣，往下轉是往下捲。"""
        return -1 if event.delta > 0 else 1

    def _width(self, label: str, texts: Iterable[str]) -> int:
        measured = [self.header_font.measure(label), *(self.font.measure(text) for text in texts)]
        return max(self.MIN_WIDTH, max(measured) + 2 * self.PAD)

    def _header(self, canvas: tk.Canvas, x: int, width: int, label: str) -> None:
        canvas.create_rectangle(x, 0, x + width, self.row_height, fill=BACKGROUND, outline=BORDER)
        canvas.create_text(
            x + self.PAD, self.row_height // 2, text=label, anchor="w", font=self.header_font, fill=TEXT_SECONDARY
        )

    def _cell(self, canvas: tk.Canvas, key: str, name: str, x: int, y: int, width: int, text: str) -> None:
        fill = PROBLEM_BG if name in self._missing[key] else SURFACE
        rect = canvas.create_rectangle(x, y, x + width, y + self.row_height, fill=fill, outline=BORDER)
        label = canvas.create_text(
            x + self.PAD, y + self.row_height // 2, text=text, anchor="w", font=self.font, fill=TEXT
        )
        self._rects[(key, name)] = (canvas, rect)
        self._labels[(key, name)] = (canvas, label)

    def _paint(self, key: str | None, *, selected: bool) -> None:
        if key is None:
            return
        for (row, name), (canvas, rect) in self._rects.items():
            if row == key and name not in self._missing[key]:
                canvas.itemconfigure(rect, fill=ACCENT_SOFT if selected else SURFACE)
