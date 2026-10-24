"""整個 FCN Workflow 視窗共用的外觀與背景作業（#129 從 `fcn_checker.panel` 搬出來）。

配色與 `apply_style`（#83）、Windows DPI 與圖示、各分頁共用的頁首、提示條、下一步按鈕、重點數字、
結果表的複製元件（#121）與自動欄寬（#192），以及全視窗唯一的背景作業寫法 `Background`（測試用同步的
`ImmediateBackground`，#206）。核對分頁（`fcn_checker.panel`）與
視窗外殼（`sp_workflow.app`）都從這裡 import；這裡不 import 任何核對或流程的業務邏輯。
"""

from __future__ import annotations

import ctypes
import re
import sys
import tkinter as tk
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk
from typing import TypeVar

T = TypeVar("T")


def enable_windows_dpi_awareness() -> None:
    """在建立 Tk 視窗前啟用 system DPI awareness，避免 Windows 點陣放大。

    Tk 8.6 不在此處承諾跨螢幕動態 DPI 排版；已由宿主設定 awareness 時維持其設定。
    """
    if sys.platform != "win32":
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        set_awareness = user32.SetProcessDpiAwarenessContext
    except AttributeError:
        user32.SetProcessDPIAware()
        return
    set_awareness.argtypes = [ctypes.c_void_p]
    set_awareness.restype = ctypes.c_bool
    set_awareness(ctypes.c_void_p(-2))  # DPI_AWARENESS_CONTEXT_SYSTEM_AWARE


PANEL_ICON = Path(__file__).parent / "assets" / "panel.ico"
APP_USER_MODEL_ID = "TaylorYam.FcnTermSheetChecker.Panel"


def set_windows_app_id() -> None:
    """在建立 Tk 視窗前設定專屬 AppUserModelID，工作列才顯示 PANEL 圖示，不與其他 Python 程式合併成一組。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def apply_window_icon(root: tk.Misc) -> None:
    """標題列、Alt-Tab、工作列與之後開啟的對話框都使用 PANEL 圖示；找不到或無法載入時沿用 Tk 預設圖示。"""
    try:
        root.iconbitmap(default=str(PANEL_ICON))
    except tk.TclError:
        return
    if sys.platform == "win32":
        root.update_idletasks()  # 建立外框視窗後 wm_frame 才是實際的視窗代碼
        _set_window_icons(int(root.wm_frame(), 16))


def _set_window_icons(hwnd: int) -> None:
    """Tk 只把圖示設在視窗類別上，Windows 11 工作列會改拿小圖示放大而變糊（#111）。

    依目前縮放從 .ico 載入剛好的大、小圖示尺寸，直接設給視窗。
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.LoadImageW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    for kind, metric in ((1, 11), (0, 49)):  # (ICON_BIG, SM_CXICON)、(ICON_SMALL, SM_CXSMICON)
        size = user32.GetSystemMetrics(metric)
        icon = user32.LoadImageW(None, str(PANEL_ICON), 1, size, size, 0x10)  # IMAGE_ICON、LR_LOADFROMFILE
        if icon:
            user32.SendMessageW(hwnd, 0x80, kind, icon)  # WM_SETICON


# 整個視窗的配色（#83）：淺灰底、白色卡片、深藍字；只用一個亮藍當強調色，紅色只給有問題的、綠色只給完成
BACKGROUND = "#F5F5F7"
SURFACE = "#FFFFFF"
FIELD_READONLY = "#FAFAFC"
HOVER = "#F2F2F7"
PRESSED = "#E8E8ED"
BORDER = "#E3E3E8"
CONTROL_BORDER = "#D2D2D7"
TEXT = "#1D1D1F"
TEXT_SECONDARY = "#6E6E73"
TEXT_DISABLED = "#A1A1A6"
NAVY = "#0B1F3A"
ACCENT = "#2251FF"
ACCENT_ACTIVE = "#1A3FCC"
ACCENT_DISABLED = "#B9C7F5"
ACCENT_SOFT = "#EAF0FF"
DANGER = "#C4262E"
SUCCESS = "#1E7A4C"
SUCCESS_SOFT = "#E8F5EE"
FONT = "Microsoft JhengHei UI"  # 公司 Windows 都有；數字另用 Segoe UI
NUMBER_FONT = "Segoe UI"


def flat(color: str) -> dict[str, str]:
    """clam 主題的元件有立體邊（lightcolor／darkcolor）；三個設成同一色就是平的。"""
    return {"background": color, "lightcolor": color, "darkcolor": color}


def flat_states(states: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    """style.map 用：各狀態的底色與立體邊一起換，維持平的。"""
    return {"background": states, "lightcolor": states, "darkcolor": states}


def apply_style(root: tk.Tk) -> Callable[[float], int]:
    """整個視窗共用的配色、字型、按鈕與表格樣式（#74 起每個分頁都用這一套，#83 改成蘋果 × 麥肯錫風格）。

    回傳 pixels：把 96 DPI 下的像素依目前螢幕縮放換算成實際像素。視窗外殼（頂端列、步驟欄）的樣式在 sp_workflow.app。
    """
    scale = root.winfo_fpixels("1i") / 96

    def pixels(value):
        return round(value * scale)

    root.configure(background=BACKGROUND)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        ".", background=BACKGROUND, foreground=TEXT, font=(FONT, 11), focuscolor=ACCENT_SOFT, bordercolor=BORDER
    )
    style.configure(".", lightcolor=BORDER, darkcolor=BORDER)
    style.configure("Card.TFrame", background=SURFACE, relief="solid", borderwidth=1)
    style.configure("CardBody.TFrame", background=SURFACE)
    style.configure("Muted.TLabel", foreground=TEXT_SECONDARY)
    style.configure("Step.TLabel", foreground=TEXT_SECONDARY, font=(FONT, 10))
    style.configure("Title.TLabel", foreground=NAVY, font=(FONT, 18, "bold"))
    style.configure("Card.TLabel", background=SURFACE)
    style.configure("Card.TRadiobutton", background=SURFACE)
    style.configure("Card.TCheckbutton", background=SURFACE)
    style.configure("CardMuted.TLabel", background=SURFACE, foreground=TEXT_SECONDARY, font=(FONT, 10))
    style.configure("KpiCaption.TLabel", background=SURFACE, foreground=TEXT_SECONDARY, font=(FONT, 10))
    style.configure("KpiValue.TLabel", background=SURFACE, foreground=NAVY, font=(NUMBER_FONT, 18, "bold"))
    style.configure("KpiBad.TLabel", background=SURFACE, foreground=DANGER, font=(NUMBER_FONT, 18, "bold"))
    style.configure("TButton", padding=(14, 7), bordercolor=CONTROL_BORDER, **flat(SURFACE))
    style.map(
        "TButton",
        foreground=[("disabled", TEXT_DISABLED)],
        **flat_states([("disabled", BACKGROUND), ("pressed", PRESSED), ("active", HOVER)]),
    )
    style.configure("Primary.TButton", foreground=SURFACE, font=(FONT, 11, "bold"), bordercolor=ACCENT, **flat(ACCENT))
    accent_states = [("disabled", ACCENT_DISABLED), ("pressed", ACCENT_ACTIVE), ("active", ACCENT_ACTIVE)]
    style.map(
        "Primary.TButton", foreground=[("disabled", SURFACE)], bordercolor=accent_states, **flat_states(accent_states)
    )
    style.configure("Next.TButton", foreground=ACCENT, font=(FONT, 11, "bold"))
    style.configure("TEntry", padding=6, fieldbackground=SURFACE, bordercolor=CONTROL_BORDER, **flat(SURFACE))
    style.map("TEntry", fieldbackground=[("readonly", FIELD_READONLY)], bordercolor=[("focus", ACCENT)])
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, rowheight=pixels(32))
    style.configure(
        "Treeview.Heading", background=BACKGROUND, foreground=TEXT_SECONDARY, font=(FONT, 10, "bold"), relief="flat"
    )
    style.map("Treeview.Heading", background=[("active", PRESSED)])
    style.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", TEXT)])
    style.configure("TNotebook", borderwidth=0)
    style.configure("TNotebook.Tab", foreground=TEXT_SECONDARY, padding=(14, 6))
    style.map("TNotebook.Tab", background=[("selected", SURFACE)], foreground=[("selected", TEXT)])
    style.configure("TScrollbar", troughcolor=SURFACE, arrowcolor=TEXT_SECONDARY)
    style.configure("Hint.TFrame", background=ACCENT_SOFT)
    style.configure("Hint.TLabel", background=ACCENT_SOFT, foreground=NAVY)
    # 這一步做完時，狀態列變成綠色結果條（#83）
    style.configure(
        "Done.TLabel", background=SUCCESS_SOFT, foreground=SUCCESS, font=(FONT, 11, "bold"), padding=(12, 7)
    )
    return pixels


class PageHeader(ttk.Frame):
    """分頁最上方：「步驟 N／總數」、標題（只寫功能，#85）與一行說明（#83）。

    步驟編號由視窗依分頁順序填入 step；畫面需要騰出高度時可用 show_description 收起說明（#108）。
    """

    def __init__(self, parent: tk.Misc, title: str, description: str):
        super().__init__(parent)
        self.step = tk.StringVar()
        ttk.Label(self, textvariable=self.step, style="Step.TLabel").pack(anchor="w")
        ttk.Label(self, text=title, style="Title.TLabel").pack(anchor="w", pady=(2, 4))
        self.description = text = ttk.Label(self, text=description, style="Muted.TLabel", wraplength=900)
        text.pack(anchor="w", fill="x")
        self.bind("<Configure>", lambda event: text.configure(wraplength=max(300, event.width - 20)))

    def show_description(self, shown: bool) -> None:
        if shown:
            self.description.pack(anchor="w", fill="x")
        else:
            self.description.pack_forget()


class KpiRow(ttk.Frame):
    """完成後的重點數字（#83）：一格一個數字，有問題的用紅色；沒有結果時不顯示。

    放在白色卡片裡；建立後由呼叫端 grid，再呼叫 hide()。values 是目前顯示的（標題, 數字, 是否有問題）。
    """

    def __init__(self, parent: tk.Misc):
        super().__init__(parent, style="CardBody.TFrame")
        self.values: tuple[tuple[str, int, bool], ...] = ()

    def show(self, values: Iterable[tuple[str, int, bool]]) -> None:
        for tile in self.winfo_children():
            tile.destroy()
        self.values = tuple(values)
        for column, (caption, value, bad) in enumerate(self.values):
            tile = ttk.Frame(self, style="Card.TFrame", padding=(14, 6, 26, 6))
            tile.grid(row=0, column=column, sticky="w", padx=(0, 10))
            ttk.Label(tile, text=caption, style="KpiCaption.TLabel").pack(anchor="w")
            ttk.Label(tile, text=str(value), style="KpiBad.TLabel" if bad else "KpiValue.TLabel").pack(anchor="w")
        self.grid()

    def hide(self) -> None:
        self.values = ()
        self.grid_remove()


class HintBar(ttk.Frame):
    """分頁上方的藍色提示條：寫出從上一步帶入了什麼，旁邊「改選…」可以換掉（#76）；沒有帶入時不顯示。

    建立後由呼叫端 grid 到分頁最上方，再呼叫 hide()。
    """

    def __init__(self, parent: tk.Misc, change: Callable[[], None]):
        super().__init__(parent, style="Hint.TFrame", padding=(10, 6))
        self.text = tk.StringVar()
        label = ttk.Label(self, textvariable=self.text, style="Hint.TLabel", wraplength=900)
        label.pack(side="left", fill="x", expand=True)
        ttk.Button(self, text="改選…", command=change).pack(side="left", padx=(10, 0))
        self.bind("<Configure>", lambda event: label.configure(wraplength=max(300, event.width - 180)))

    @property
    def shown(self) -> bool:
        return bool(self.text.get())

    def show(self, text: str) -> None:
        self.text.set(text)
        self.grid()

    def hide(self) -> None:
        self.text.set("")
        self.grid_remove()


class NextButton(ttk.Button):
    """完成訊息旁的「下一步：… →」：做完這一步才出現，按了才切到下一頁，不會自動執行（#76）。

    放在用 pack 排列的按鈕列最後面。
    """

    def __init__(self, parent: tk.Misc, text: str, command: Callable[[], None]):
        super().__init__(parent, text=text, command=command, style="Next.TButton")

    def offer(self) -> None:
        self.pack(side="left", padx=(12, 0))

    def withdraw(self) -> None:
        self.pack_forget()


def tab_separated(rows: Iterable[Iterable[object]]) -> str:
    """表格 → 放進剪貼簿的文字（#121）：Tab 分欄、換行分列，貼到 Excel 會照格子分開；格子裡的 Tab、換行換成空白。"""
    return "\n".join("\t".join(re.sub(r"\s*[\t\r\n]+\s*", " ", str(cell)) for cell in row) for row in rows)


def to_clipboard(widget: tk.Misc, text: str) -> None:
    widget.clipboard_clear()
    widget.clipboard_append(text)


def _shorten(text: str, limit: int = 30) -> str:
    """右鍵選單上顯示的格子內容：太長的截掉。"""
    return text if len(text) <= limit else text[: limit - 1] + "…"


class CopyMenu(tk.Menu):
    """結果表的右鍵選單（#121）：複製那一格、那一列、選取的列（可以多選的表）或整張表（含表頭）。

    一格、一列和整張表一樣用 `tab_separated`：格子裡的換行、Tab 換成空白，貼到 Excel 不會錯位。
    """

    COPY_ALL = "全部複製（含表頭）"

    def __init__(self, widget: tk.Misc, copy_all: Callable[[], int]):
        super().__init__(widget, tearoff=False)
        self.widget = widget
        self.copy_all = copy_all

    def fill(self, row: Sequence[str] | None, cell: str = "", selected: tuple[int, Callable[[], object]] | None = None):
        """右鍵點到的那一列（點到表外是 None）與那一格的字；`selected` 是（選取了幾列, 複製選取的列）。"""
        self.delete(0, tk.END)
        if cell:
            text = tab_separated([[cell]])
            self.add_command(label=f"複製「{_shorten(text)}」", command=lambda: to_clipboard(self.widget, text))
        if row is not None:
            self.add_command(label="複製這一列", command=lambda: self._copy([row]))
        if selected is not None:
            count, copy_selected = selected
            self.add_command(
                label=f"複製選取的列（{count} 列）", command=copy_selected, state="normal" if count else "disabled"
            )
        self.add_command(label=self.COPY_ALL, command=self.copy_all)

    def labels(self) -> list[str]:
        return [str(self.entrycget(i, "label")) for i in range(self.index(tk.END) + 1)]

    def _copy(self, rows: Sequence[Sequence[str]]) -> None:
        to_clipboard(self.widget, tab_separated(rows))


class TableCopy:
    """結果表可以複製（#121）：右鍵選單（`CopyMenu`）；Ctrl+A 全選、Ctrl+C 複製選取的列。

    值直接讀 Tk 裡的字串：`item(..., "values")` 在部分 Python 版本會把 0 開頭的 TDCC 轉成整數、掉了前導零。
    一次只能選一列（`browse`）的表不全選。
    """

    def __init__(self, table: ttk.Treeview):
        self.table = table
        self.menu = CopyMenu(table, self.copy_all)
        table.bind("<Button-3>", self._popup, add="+")
        for key in ("<Control-a>", "<Control-A>"):
            table.bind(key, lambda _event: self._break(self.select_all))
        for key in ("<Control-c>", "<Control-C>"):
            table.bind(key, lambda _event: self._break(self.copy_selected))

    def rows(self, items: Iterable[str] | None = None, *, header: bool = False) -> list[list[str]]:
        """表上的列（照畫面的順序）；`header` 時第一列是表頭。"""
        columns = [str(column) for column in self.table["columns"]]
        rows = [[self._tcl_text("heading", column, "-text") for column in columns]] if header else []
        for item in self.table.get_children() if items is None else items:
            rows.append([self._tcl_text("set", item, column) for column in columns])
        return rows

    def copy_all(self) -> int:
        """整張表（含表頭）放進剪貼簿；回傳幾列（不算表頭），表上沒有列時不動剪貼簿。"""
        rows = self.rows(header=True)
        if len(rows) > 1:
            to_clipboard(self.table, tab_separated(rows))
        return len(rows) - 1

    def copy_selected(self) -> int:
        """選取的列（照畫面的順序，不含表頭）放進剪貼簿；回傳幾列。"""
        selected = set(self.table.selection())
        items = [item for item in self.table.get_children() if item in selected]
        if items:
            to_clipboard(self.table, tab_separated(self.rows(items)))
        return len(items)

    def select_all(self) -> None:
        if str(self.table.cget("selectmode")) == "extended":
            self.table.selection_set(self.table.get_children())

    def fill_menu(self, item: str, column: str) -> None:
        """右鍵點到的格子（`item` 是列、`column` 是 `#N`；點到空白處是空字串）→ 選單的項目。"""
        row = self.rows([item])[0] if item else None
        cell = self._tcl_text("set", item, self._tcl_text("column", column, "-id")) if item and column else ""
        selected = (len(self.table.selection()), self.copy_selected)
        self.menu.fill(row, cell, selected if str(self.table.cget("selectmode")) == "extended" else None)

    def _popup(self, event: tk.Event) -> None:
        self.fill_menu(self.table.identify_row(event.y), self.table.identify_column(event.x))
        self.menu.tk_popup(event.x_root, event.y_root)

    def _tcl_text(self, *args: str) -> str:
        """Treeview 的 Tcl 指令（`set`、`heading`…）回傳的原始字串。"""
        return str(self.table.tk.call(str(self.table), *args))

    @staticmethod
    def _break(action: Callable[[], object]) -> str:
        action()
        return "break"


def _text_weight(text: str) -> int:
    """估字的寬度：中文等全形字算兩個；只用來挑出最長的幾格去量。"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)


class FitTreeview(ttk.Treeview):
    """欄寬依內容自動調整的結果表（#192）：列有變動（新增、刪除、改值）後，等畫面空下來重算一次欄寬。

    每一欄 = 表頭與內容最寬的那個加左右留白；內容最寬算到 `MAX_WIDTH`（96 DPI 下的像素），太長的照舊左右捲動或右鍵複製。
    最後一欄照舊跟著視窗變寬，但不縮到比內容窄（#179）。
    """

    PAD = 24
    MAX_WIDTH = 420
    SAMPLES = 8  # 每欄只量估起來最長的幾格，表很長時不用每格都量

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._fit_job: str | None = None
        self._fonts: tuple[tkfont.Font, tkfont.Font] | None = None
        self._schedule_fit()  # 表頭設好、還沒有列時也照表頭寬度

    def insert(self, parent, index, iid=None, **kw):
        item = super().insert(parent, index, iid, **kw)
        self._schedule_fit()
        return item

    def delete(self, *items) -> None:
        super().delete(*items)
        self._schedule_fit()

    def item(self, item, option=None, **kw):
        result = super().item(item, option, **kw)
        if "values" in kw:
            self._schedule_fit()
        return result

    def set(self, item, column=None, value=None):
        result = super().set(item, column, value)
        if value is not None:
            self._schedule_fit()
        return result

    def destroy(self) -> None:
        if self._fit_job is not None:
            self.after_cancel(self._fit_job)
            self._fit_job = None
        super().destroy()

    def fit_columns(self) -> None:
        """依現在表上的列重算每一欄的寬度。"""
        self._fit_job = None
        body, heading = self._measuring_fonts()
        scale = self.winfo_fpixels("1i") / 96
        pad, limit = round(self.PAD * scale), round(self.MAX_WIDTH * scale)
        columns = [str(column) for column in self["columns"]]
        texts: list[list[str]] = [[] for _ in columns]
        for item in self.get_children():
            for i, value in enumerate(self.tk.splitlist(self.tk.call(str(self), "item", item, "-values"))):
                if i < len(texts):
                    texts[i].append(str(value))
        shown = [str(column) for column in self["displaycolumns"]]
        last = (columns if shown in (["#all"], []) else shown)[-1] if columns else None
        for column, values in zip(columns, texts, strict=True):
            longest = sorted(set(values), key=_text_weight, reverse=True)[: self.SAMPLES]
            content = max((body.measure(text) for text in longest), default=0)
            title = heading.measure(str(self.tk.call(str(self), "heading", column, "-text")))
            width = max(title + pad, min(content + pad, limit))
            if column == last:
                self.column(column, width=width, minwidth=width)
            else:
                self.column(column, width=width)

    def _schedule_fit(self) -> None:
        if self._fit_job is None:
            self._fit_job = self.after_idle(self.fit_columns)

    def _measuring_fonts(self) -> tuple[tkfont.Font, tkfont.Font]:
        """表身與表頭的字型（照 `apply_style` 的 Treeview 樣式；沒設時用 Tk 預設字型）。"""
        if self._fonts is None:
            style = ttk.Style(self)
            body = style.lookup("Treeview", "font") or "TkDefaultFont"
            heading = style.lookup("Treeview.Heading", "font") or "TkHeadingFont"
            self._fonts = (tkfont.Font(self, font=body), tkfont.Font(self, font=heading))
        return self._fonts


class CopyButton(ttk.Button):
    """結果表右上方的「全部複製」（#121）：一鍵把整張表（含表頭）放進剪貼簿，按完短暫顯示複製了幾列。"""

    TEXT = "全部複製"

    def __init__(self, parent: tk.Misc, copy_all: Callable[[], int]):
        super().__init__(parent, text=self.TEXT, command=self.copy, width=12)
        self.copy_all = copy_all
        self._reset_job: str | None = None  # 換回「全部複製」的 after 排程

    def copy(self) -> None:
        count = self.copy_all()
        self.configure(text=f"已複製 {count} 列" if count else "表上沒有資料")
        if self._reset_job is not None:
            self.after_cancel(self._reset_job)
        self._reset_job = self.after(1500, self._reset)

    def _reset(self) -> None:
        self._reset_job = None
        self.configure(text=self.TEXT)


class CopyArea(ttk.Frame):
    """結果表那一塊（#121）：第 0 列左邊寫怎麼複製、右邊「全部複製」；表由呼叫端建在這裡面、放在第 1 列。"""

    def __init__(self, parent: tk.Misc, hint: str, copy_all: Callable[[], int]):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(bar, text=hint, style="Muted.TLabel").pack(side="left")
        self.button = CopyButton(bar, copy_all)
        self.button.pack(side="right")


class Background:
    """全視窗唯一的背景作業寫法（#129）：一個分頁一個，動作依序在背景執行緒做，做完回主執行緒呼叫 done。

    Tk 只能在主執行緒動，背景執行緒不碰畫面；`run` 回傳的 Future 可以拿來看還在不在做。
    `close` 之後不再呼叫 done，還沒開始的動作取消。
    """

    INTERVAL = 80  # 毫秒：多久看一次做完了沒

    def __init__(self, widget: tk.Misc):
        self.widget = widget
        self.closed = False
        self._executor = ThreadPoolExecutor(max_workers=1)

    def run(self, action: Callable[[], T], done: Callable[[Future[T]], None] | None = None) -> Future[T]:
        """在背景做 action；有 done 時，做完（含丟出例外）在主執行緒以 Future 呼叫它，一定在 run 回傳之後。"""
        future = self._executor.submit(action)
        if done is not None:
            self.widget.after(self.INTERVAL, lambda: self._wait(future, done))
        return future

    def _wait(self, future: Future[T], done: Callable[[Future[T]], None]) -> None:
        if self.closed:
            return
        if not future.done():
            self.widget.after(self.INTERVAL, lambda: self._wait(future, done))
            return
        done(future)

    def close(self) -> None:
        self.closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)


class ImmediateBackground:
    """測試用、介面和 `Background` 相同（#206）：動作在同一個執行緒馬上做完，接著直接呼叫 done，不等 Tk 的事件迴圈。"""

    def __init__(self, widget: tk.Misc):
        self.widget = widget
        self.closed = False

    def run(self, action: Callable[[], T], done: Callable[[Future[T]], None] | None = None) -> Future[T]:
        future: Future[T] = Future()
        try:
            future.set_result(action())
        except Exception as exc:  # noqa: BLE001 - 和 Background 一樣放進 Future 交給 done
            future.set_exception(exc)
        if done is not None and not self.closed:
            done(future)
        return future

    def close(self) -> None:
        self.closed = True
