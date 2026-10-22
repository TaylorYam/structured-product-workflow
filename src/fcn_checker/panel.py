"""Tkinter 核對分頁：參考條件表＋多份說明書的預覽、核對、逐份結果與手動儲存。

#74 起核對是 FCN Workflow 視窗（`sp_workflow.app`）的一個分頁；這裡提供分頁、對話框與整個視窗共用的樣式、圖示。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import tkinter as tk
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .approval_dates import parse_input_date
from .check_config import CONFIG_DIR, DEFAULTS
from .ingestion import IngestionError, SourceSnapshot
from .panel_workflow import PanelOutcome, PanelSession, ReleasePlan, ReleaseState
from .saving import SaveReceipt


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
    """分頁最上方：「步驟 N／6」、標題（只寫功能，#85）與一行說明（#83）。

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


FOLDERS_RECORD = Path(".local") / "panel_folders.json"  # 兩個選檔按鈕各自記住的上次資料夾（根目錄下）


class LastFolders:
    """選檔按鈕各自記住上一次選檔的資料夾；讀寫失敗時當作沒有記錄。"""

    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def initial(self, kind: str) -> str | None:
        """對話框的起始資料夾；沒有記錄或資料夾已不存在時回傳 None，交給 Windows 決定。"""
        folder = self._read().get(kind)
        return folder if isinstance(folder, str) and Path(folder).is_dir() else None

    def remember(self, kind: str, selected: Path) -> None:
        """記下選取檔案所在的資料夾；寫不進去時略過，下次照舊由 Windows 決定。"""
        data = self._read()
        data[kind] = str(selected.parent)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


def _table(
    parent, columns: tuple[tuple[str, str, int], ...], pixels, height: int = 6, selectmode: str = "browse"
) -> ttk.Treeview:
    frame = ttk.Frame(parent)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    table = ttk.Treeview(
        frame, columns=[c for c, _, _ in columns], show="headings", selectmode=selectmode, height=height
    )
    for column, label, width in columns:
        table.heading(column, text=label)
        table.column(column, width=pixels(width), minwidth=pixels(80), anchor="w")
    table.grid(row=0, column=0, sticky="nsew")
    scroll = ttk.Scrollbar(frame, command=table.yview)
    scroll.grid(row=0, column=1, sticky="ns")
    horizontal = ttk.Scrollbar(frame, orient="horizontal", command=table.xview)
    horizontal.grid(row=1, column=0, sticky="ew")
    table.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
    table.frame = frame
    return table


def _text(parent, height: int) -> tk.Text:
    return tk.Text(
        parent,
        height=height,
        wrap="word",
        font=("Microsoft JhengHei UI", 11),
        relief="flat",
        padx=8,
        pady=6,
        state="disabled",
    )


def _show_grid(widgets: Iterable[tk.Misc], shown: bool) -> None:
    """顯示或收起已 grid 過的元件，收起時保留原本的位置設定。"""
    for widget in widgets:
        if shown:
            widget.grid()
        else:
            widget.grid_remove()


def _write(widget: tk.Text, text: str) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", text)
    widget.configure(state="disabled")


class ResultPane(ttk.Frame):
    """左側逐份說明書清單（問題優先）；右側選取那份的逐欄結果，選取列可查看完整值與原文證據。

    下方「人工放行」按鈕對選取的說明書操作，清單可用 Shift／Ctrl 多選（Issue #219）。選一份時能否放行與不能放行的原因由 release_state 提供，
    說明文字由 PanelOutcome.release_note 組好；多選時由 release_plan 決定要處理哪幾份，文字由 PanelOutcome.release_plan_* 組好。
    按下時以選取的那幾份呼叫 on_release；明細區顯示游標所在那份。
    """

    def __init__(
        self,
        parent,
        pixels,
        release_state: Callable[..., ReleaseState],
        release_plan: Callable[..., ReleasePlan],
        on_release: Callable[..., None],
    ):
        super().__init__(parent, padding=8)
        self.release_state, self.release_plan, self.on_release = release_state, release_plan, on_release
        self.items: dict[str, Any] = {}  # 結果清單列 → PanelOutcome.ordered_items 的一份
        self.rows: dict[str, str] = {}  # 明細清單列 → 選取時顯示的明細
        self.outcome: PanelOutcome | None = None
        self.columnconfigure(1, weight=1)
        # 上方內容變多時，結果清單也不會被擠掉；整批份數由核對頁的重點數字與狀態列顯示（#83）
        self.rowconfigure(1, weight=1, minsize=pixels(170))
        self.item_table = _table(
            self,
            (("status", "狀態", 150), ("kind", "種類", 90), ("file", "PDF 檔名", 220), ("problems", "問題數", 70)),
            pixels,
            height=8,
            selectmode="extended",
        )
        self.item_table.frame.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        self.table = _table(
            self,
            (
                ("status", "狀態", 110),
                ("field", "欄位", 190),
                ("expected", "參考條件表／標準值", 190),
                ("actual", "PDF 值", 190),
                ("pages", "PDF 頁碼", 110),
            ),
            pixels,
            height=8,
        )
        self.table.frame.grid(row=1, column=1, sticky="nsew")
        ttk.Label(self, text="選取項目查看原因、參考條件表儲存格與 PDF 原文；下方另列回填欄位：").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(8, 4)
        )
        self.detail = _text(self, 6)
        self.detail.grid(row=3, column=0, columnspan=2, sticky="ew")
        actions = ttk.Frame(self)
        actions.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.release_button = ttk.Button(actions, text="人工放行…", command=self._release, state="disabled")
        self.release_button.grid(row=0, column=0, sticky="w")
        self.release_reason = tk.StringVar(value="")
        ttk.Label(actions, textvariable=self.release_reason, wraplength=pixels(900)).grid(
            row=0, column=1, sticky="w", padx=(12, 0)
        )
        self.item_table.bind("<<TreeviewSelect>>", self._item_selected)
        self.table.bind("<<TreeviewSelect>>", self._row_selected)

    def clear(self):
        self.items.clear()
        self.rows.clear()
        self.outcome = None
        self.item_table.delete(*self.item_table.get_children())
        self.table.delete(*self.table.get_children())
        _write(self.detail, "")
        self._release_state(())

    def show(self, outcome: PanelOutcome, select: tuple = (), focus: Any = None):
        """顯示結果並選取 select 裡的那幾份（沒給或都不在結果中時選第一份）；focus 也在選取中時游標停在那份，否則停在選取的第一份。"""
        self.clear()
        self.outcome = outcome
        chosen = []
        for item in outcome.ordered_items:
            key = self.item_table.insert("", "end", values=outcome.item_row(item))
            self.items[key] = item
            if any(item is s for s in select):
                chosen.append(key)
        if self.items:
            chosen = chosen or [next(iter(self.items))]
            cursor = next((k for k in chosen if self.items[k] is focus), chosen[0])
            self.item_table.selection_set(chosen)
            self.item_table.focus(cursor)
            self.item_table.see(cursor)
            self._item_selected(None)

    def _release_state(self, items: tuple) -> None:
        if len(items) > 1:
            plan = self.release_plan(items)
            self.release_button.configure(
                text=PanelOutcome.release_plan_button(plan), state="normal" if plan.targets else "disabled"
            )
            self.release_reason.set(PanelOutcome.release_plan_note(plan))
            return
        item = items[0] if items else None
        if item is None:
            self.release_button.configure(text="人工放行…", state="disabled")
            self.release_reason.set("")
        elif item.released:
            self.release_button.configure(text="取消放行", state="normal")
            self.release_reason.set(PanelOutcome.release_note(item, ""))
        else:
            allowed, reason = self.release_state(item)
            self.release_button.configure(text="人工放行…", state="normal" if allowed else "disabled")
            self.release_reason.set(PanelOutcome.release_note(item, reason))

    def _release(self):
        items = self.selected_items()
        if items:
            self.on_release(items)

    def selected_items(self) -> tuple:
        """選取的那幾份，依清單順序。"""
        return tuple(self.items[key] for key in self.item_table.selection() if key in self.items)

    def focused_item(self):
        """明細區顯示的那份：游標所在且仍選取的那份，否則選取的第一份。"""
        selection = self.item_table.selection()
        focus = self.item_table.focus()
        key = focus if focus in selection else (selection[0] if selection else None)
        return self.items.get(key) if key else None

    def _item_selected(self, _):
        self._release_state(self.selected_items())
        self.rows.clear()
        self.table.delete(*self.table.get_children())
        item = self.focused_item()
        if item is None or self.outcome is None:  # Ctrl 點選取消了全部選取
            _write(self.detail, "")
            return
        for row in self.outcome.result_rows(item):
            self.rows[self.table.insert("", "end", values=row.values)] = row.detail
        _write(self.detail, self.outcome.backfill_text(item))

    def _row_selected(self, _):
        selection = self.table.selection()
        if selection and selection[0] in self.rows:
            _write(self.detail, self.rows[selection[0]])


class ConfirmDialog(tk.Toplevel):
    """內文可捲動的確認視窗（多選人工放行逐份列錯訊時可能超過螢幕高度）；ask 回傳是否按下確認。"""

    def __init__(self, parent: tk.Misc, title: str, text: str, confirm: str, pixels):
        super().__init__(parent)
        self.confirmed = False
        self.title(title)
        self.transient(parent)
        frame = ttk.Frame(self, padding=pixels(16))
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        body = _text(frame, 20)
        body.configure(width=80)
        body.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, command=body.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        body.configure(yscrollcommand=scroll.set)
        _write(body, text)
        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky="e", pady=(pixels(12), 0))
        ttk.Button(buttons, text=confirm, command=self._confirm).pack(side="left", padx=(0, pixels(8)))
        cancel = ttk.Button(buttons, text="取消", command=self.destroy)
        cancel.pack(side="left")
        self.bind("<Escape>", lambda _: self.destroy())
        self.update_idletasks()  # 置中在主視窗上
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        cancel.focus_set()
        self.grab_set()

    def _confirm(self) -> None:
        self.confirmed = True
        self.destroy()

    @classmethod
    def ask(cls, parent: tk.Misc, title: str, text: str, confirm: str, pixels) -> bool:
        dialog = cls(parent, title, text, confirm, pixels)
        parent.wait_window(dialog)
        return dialog.confirmed


class ApprovalDatesDialog(tk.Toplevel):
    """審查通過日期：列出歷次日期，新增晚於最新一筆的日期，或修改／刪除最新一筆（Issue #120）。"""

    def __init__(self, parent: tk.Misc, session: PanelSession, pixels, changed: Callable[[], None]):
        dates = session.approval_dates()  # 設定檔有問題時由呼叫端顯示原因，不開視窗
        super().__init__(parent)
        self.session, self.changed = session, changed
        self.title("審查通過日期")
        self.transient(parent)
        self.resizable(False, False)
        frame = ttk.Frame(self, padding=pixels(16))
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="每份說明書依交易日，核對當天或之前最近一次的審查通過日期。\n"
            "重新審查後新增新的日期；較早的日期不能修改，只能修改或刪除最新一筆。",
            wraplength=pixels(520),
        ).grid(row=0, column=0, columnspan=4, sticky="w")
        self.listbox = tk.Listbox(frame, height=8, font=("Microsoft JhengHei UI", 11), activestyle="none")
        self.listbox.grid(row=1, column=0, columnspan=4, sticky="ew", pady=pixels(10))
        ttk.Label(frame, text="日期（YYYY-MM-DD）").grid(row=2, column=0, sticky="w")
        self.entry = ttk.Entry(frame, width=14, font=("Microsoft JhengHei UI", 11))
        self.entry.grid(row=2, column=1, sticky="w", padx=(pixels(8), 0))
        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=4, sticky="w", pady=(pixels(12), 0))
        for text, command in (
            ("新增", self._add),
            ("把最新一筆改成這個日期", self._change),
            ("刪除最新一筆", self._remove),
            ("關閉", self.destroy),
        ):
            ttk.Button(buttons, text=text, command=command).pack(side="left", padx=(0, pixels(8)))
        self._show(dates)
        self.update_idletasks()  # 置中在主視窗上
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.entry.focus_set()
        self.grab_set()

    def _show(self, dates: tuple) -> None:
        self.dates = dates
        self.listbox.delete(0, "end")
        for i, d in enumerate(dates):
            self.listbox.insert("end", f"{d}{'（最新）' if i == len(dates) - 1 else ''}")

    def _run(self, action: Callable[[], str], confirm: str | None = None) -> None:
        if confirm and not messagebox.askyesno("審查通過日期", confirm, parent=self):
            return
        try:
            message = action()
        except IngestionError as e:
            messagebox.showerror("審查通過日期", str(e), parent=self)
            return
        self.entry.delete(0, "end")
        self._show(self.session.approval_dates())
        self.changed()
        messagebox.showinfo("審查通過日期", message, parent=self)

    def _date(self):
        return parse_input_date(self.entry.get())

    def _add(self) -> None:
        self._run(lambda: self.session.add_approval_date(self._date()))

    def _change(self) -> None:
        self._run(lambda: self.session.change_latest_approval_date(self._date()))

    def _remove(self) -> None:
        self._run(self.session.remove_latest_approval_date, f"確定刪除最新一筆審查通過日期 {self.dates[-1]}？")


EXCEL_SOURCE, DATABASE_SOURCE = "excel", "database"  # 核對分頁的條件來源（#68）


class CheckTab(ttk.Frame):
    """「核對」分頁：選參考條件表與 PDF、預覽、核對、人工放行與儲存（#74 前是獨立的核對視窗）。

    視窗本身（標題、大小、關閉）由 `sp_workflow.app` 建立；審查通過日期、匯出回報包與使用的設定檔
    在視窗的「維護」選單，選單呼叫 edit_approval_dates、export_bundle、show_config_paths。
    上一步帶入的 PDF 用 receive_pdfs；存檔成功時呼叫 on_saved(核對結果檔)，下一步按鈕由 add_next_step 加上（#76）。
    工作階段有資料庫來源時（#68），卡片最上面多一列「條件來源」：資料庫（預設，#108）或 Excel 參考條件表；
    選資料庫時「參考條件表」那一列收起來。資料庫來源存檔後寫回資料庫，寫回失敗時出現「重新寫回資料庫」。
    載入預覽後頁首說明與選檔的幾列收成一行摘要，讓預覽與結果有更多高度；按「變更…」或換來源、換 PDF 時展開（#108）。
    """

    def __init__(
        self,
        parent: tk.Misc,
        session: PanelSession,
        pixels: Callable[[float], int],
        *,
        on_saved: Callable[[Path], None] | None = None,
    ):
        super().__init__(parent, padding=(pixels(28), pixels(22)))
        self.root, self.session, self.pixels = self.winfo_toplevel(), session, pixels
        self.on_saved = on_saved
        self.next_button: NextButton | None = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.pending: Future | None = None
        self.check_pending: Future[PanelOutcome] | None = None
        self.save_pending: Future[SaveReceipt] | None = None
        self.export_pending: Future[str] | None = None
        self.release_pending: Future[IngestionError | None] | None = None
        self.write_back_pending: Future[str] | None = None
        self.has_result = False
        self.validation: Future[bool] | None = None  # 背景只計算來源快照是否仍一致，不改工作階段
        self.checking_for: SourceSnapshot | None = None
        self.closed = False
        self.shown = None  # 目前顯示的預覽
        self.sheet_path: Path | None = None
        self.pdf_paths: tuple[Path, ...] = ()
        self.folders = LastFolders(session.install_root / FOLDERS_RECORD)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self.header = PageHeader(
            self,
            "核對說明書與投資人須知",
            "選條件來源與這批 PDF（同一檔商品的 _TS 與 _IIS 一起選），先載入預覽確認每份對到的條件，再開始核對。"
            "上手由 PDF 檔名前三碼決定。按「儲存核對結果」才會寫出核對結果檔（Excel 來源不改原參考條件表；資料庫來源存檔後寫回資料庫）；錯誤清單上的說明書請交由人工核對。",
        )
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self.hint = HintBar(self, lambda: self.choose_files("pdf"))
        self.hint.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.hint.hide()
        card = ttk.Frame(self, style="Card.TFrame", padding=(18, 14))
        card.grid(row=2, column=0, sticky="ew")
        card.columnconfigure(1, weight=1)
        self.sheet_text = tk.StringVar()
        self.pdf_text = tk.StringVar()
        self.controls: list[ttk.Widget] = []
        self.source = tk.StringVar(value=EXCEL_SOURCE)
        self.folded = False  # 設定區收成一行摘要（#108）
        self.setup_rows: list[tuple[tk.Misc, ...]] = []  # 展開時才顯示的幾列；參考條件表那列另外看條件來源
        if session.database is not None:  # 條件來源（#68）：預設資料庫（#108），由 __init__ 最後套用
            label = ttk.Label(card, text="條件來源", style="Card.TLabel")
            label.grid(row=0, column=0, sticky="w", padx=(0, 16), pady=5)
            choices = ttk.Frame(card, style="CardBody.TFrame")
            choices.grid(row=0, column=1, columnspan=2, sticky="w", pady=5)
            self.setup_rows.append((label, choices))
            for text, value in (("資料庫（依 PDF 的 TDCC）", DATABASE_SOURCE), ("Excel 參考條件表", EXCEL_SOURCE)):
                radio = ttk.Radiobutton(
                    choices,
                    text=text,
                    value=value,
                    variable=self.source,
                    command=self._source_changed,
                    style="Card.TRadiobutton",
                )
                radio.pack(side="left", padx=(0, 18))
                self.controls.append(radio)
        for row, text, variable, kind in (
            (1, "參考條件表", self.sheet_text, "sheet"),
            (2, "說明書／投資人須知 PDF", self.pdf_text, "pdf"),
        ):
            label = ttk.Label(card, text=text, style="Card.TLabel")
            label.grid(row=row, column=0, sticky="w", padx=(0, 16), pady=5)
            entry = ttk.Entry(card, textvariable=variable, state="readonly")
            entry.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=5)
            button = ttk.Button(card, text="選取檔案…", command=lambda k=kind: self.choose_files(k))
            button.grid(row=row, column=2, pady=5)
            self.controls.append(button)
            if kind == "sheet":
                self.sheet_button, self.sheet_row = button, (label, entry, button)
            else:
                self.pdf_button = button
                self.setup_rows.append((label, entry, button))
        self.summary = ttk.Frame(card, style="CardBody.TFrame")  # 收起時的一行摘要：來源＋已選的 PDF＋變更…
        self.summary.grid(row=3, column=0, columnspan=3, sticky="ew")
        self.summary.columnconfigure(1, weight=1)
        self.summary_source = tk.StringVar()
        ttk.Label(self.summary, textvariable=self.summary_source, style="Hint.TLabel", padding=(8, 2)).grid(
            row=0, column=0, sticky="w", padx=(0, 12)
        )
        ttk.Label(self.summary, textvariable=self.pdf_text, style="Card.TLabel").grid(row=0, column=1, sticky="w")
        self.expand_button = ttk.Button(self.summary, text="變更…", command=self._expand)
        self.expand_button.grid(row=0, column=2, sticky="e", padx=(12, 0))
        bottom = ttk.Frame(card, style="CardBody.TFrame")  # 左邊按鈕、右邊核對完的重點數字
        bottom.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        bottom.columnconfigure(1, weight=1)
        self.actions = ttk.Frame(bottom, style="CardBody.TFrame")
        self.actions.grid(row=0, column=0, sticky="sw")
        self.reload = ttk.Button(self.actions, text="載入／重新載入預覽", command=self.load)
        self.reload.pack(side="left", padx=(0, 12))
        self.check_button = ttk.Button(
            self.actions, text="開始核對", command=self.start_check, state="disabled", style="Primary.TButton"
        )
        self.check_button.pack(side="left")
        self.save_button = ttk.Button(self.actions, text="儲存核對結果…", command=self.save, state="disabled")
        self.save_button.pack(side="left", padx=(12, 0))
        self.write_back_button = ttk.Button(self.actions, text="重新寫回資料庫", command=self.retry_write_back)
        self.controls += [self.reload]
        self.kpis = KpiRow(bottom)
        self.kpis.grid(row=0, column=2, sticky="e")
        self.kpis.hide()
        self.tabs = ttk.Notebook(self)
        self.tabs.grid(row=3, column=0, sticky="nsew", pady=(14, 8))
        preview_frame = ttk.Frame(self.tabs, padding=8)
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.tabs.add(preview_frame, text="預覽（唯讀）")
        self.preview_table = _table(
            preview_frame,
            (
                ("file", "PDF 檔名", 230),
                ("kind", "種類", 90),
                ("issuer", "上手", 90),
                ("code", "PDF 商品代號", 150),
                ("row", "參考條件表列", 110),
                ("problem", "狀態", 420),
            ),
            pixels,
        )
        self.preview_table.frame.grid(row=0, column=0, sticky="nsew")
        self.results = ResultPane(
            self.tabs, pixels, self.session.release_state, self.session.release_plan, self.toggle_release
        )
        self.tabs.add(self.results, text="核對結果")
        self.not_covered = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(self.not_covered, text="待處理")
        self.not_covered.columnconfigure(0, weight=1)
        self.not_covered.rowconfigure(1, weight=1)
        ttk.Label(self.not_covered, text="以下項目未涵蓋，不代表通過，仍需人工核對。").grid(row=0, column=0, sticky="w")
        self.pending_text = _text(self.not_covered, 6)
        self.pending_text.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.status = tk.StringVar(value=session.message)
        self.status_label = ttk.Label(self, textvariable=self.status, wraplength=960)
        self.status_label.grid(row=4, column=0, sticky="ew")
        self.bind("<Configure>", self._resize)
        self.root.after(1000, self._watch_sources)
        if session.database is not None:
            self.source.set(DATABASE_SOURCE)
            self._source_changed()
        else:
            self._expand()

    def _resize(self, event):
        if event.widget is self:
            self.status_label.configure(wraplength=max(300, event.width - 60))

    def _busy_any(self) -> bool:
        pending = (
            self.pending,
            self.check_pending,
            self.save_pending,
            self.release_pending,
            self.export_pending,
            self.write_back_pending,
        )
        return any(f is not None for f in pending)

    def _clear(self):
        self.shown = None
        self.preview_table.delete(*self.preview_table.get_children())
        self.check_button.configure(state="disabled")
        self._clear_results()

    def _reset(self):
        """清除預覽與結果並展開設定區（#108）：要重新載入時，作業人員看得到條件來源與選了哪些 PDF。"""
        self._clear()
        self._expand()

    def _clear_results(self):
        self.has_result = False
        self.save_button.configure(state="disabled")
        self._refresh_next()
        self.kpis.hide()
        self.results.clear()
        self.tabs.tab(self.not_covered, text="待處理")
        _write(self.pending_text, "")

    def _selection_changed(self):
        self.session.select(self.sheet_path, self.pdf_paths)
        self._reset()
        self.status.set(self.session.message)

    def _fold(self) -> None:
        """頁首說明、提示條與選檔的幾列收成一行摘要，讓預覽與結果有更多高度（#108）。"""
        name = self.sheet_path.name if self.sheet_path is not None else "未選參考條件表"
        self.summary_source.set("資料庫" if self.uses_database else f"Excel：{name}")
        self._set_folded(True)

    def _expand(self) -> None:
        """展開完整設定區；參考條件表那列只在 Excel 來源時顯示（#108）。"""
        self._set_folded(False)

    def _set_folded(self, folded: bool) -> None:
        self.folded = folded
        self.header.show_description(not folded)
        if self.hint.shown:  # 上一步帶入的提示條也一起收起，展開時再出現
            _show_grid((self.hint,), not folded)
        for row in self.setup_rows:
            _show_grid(row, not folded)
        _show_grid(self.sheet_row, not folded and not self.uses_database)
        _show_grid((self.summary,), folded)

    @property
    def uses_database(self) -> bool:
        return self.session.use_database

    def _source_changed(self):
        """條件來源換了：資料庫來源不用選參考條件表（由資料庫產生）；清除預覽與結果。"""
        self.session.select_source(self.source.get() == DATABASE_SOURCE)
        self._reset()
        if self.uses_database:
            self.sheet_text.set("由資料庫產生（載入預覽時依 PDF 檔名的 TDCC 讀取）")
        else:
            self.sheet_text.set(str(self.sheet_path) if self.sheet_path is not None else "")
        self._busy(False)
        self.status.set(self.session.message)

    def choose_files(self, kind):
        if kind == "sheet":
            selected = filedialog.askopenfilename(
                parent=self.root,
                title="選取參考條件表",
                filetypes=[("Excel 參考條件表", "*.xlsx *.xlsm")],
                initialdir=self.folders.initial(kind),
            )
            if selected:
                self.sheet_path = Path(selected)
                self.folders.remember(kind, self.sheet_path)
                self.sheet_text.set(selected)
                self._selection_changed()
            return
        selected = filedialog.askopenfilenames(
            parent=self.root,
            title="選取說明書與投資人須知 PDF（可多選）",
            filetypes=[("PDF 說明書／投資人須知", "*.pdf")],
            initialdir=self.folders.initial(kind),
        )
        if selected:
            self.folders.remember(kind, Path(selected[0]))
            self.hint.hide()  # 自己選的 PDF 取代上一步帶入的
            self._select_pdfs(Path(p) for p in selected)

    def _select_pdfs(self, paths: Iterable[Path]) -> None:
        self.pdf_paths = tuple(paths)
        names = "、".join(p.name for p in self.pdf_paths[:3])
        more = f" 等 {len(self.pdf_paths)} 份" if len(self.pdf_paths) > 3 else ""
        self.pdf_text.set(f"已選 {len(self.pdf_paths)} 份：{names}{more}")
        self._selection_changed()

    def receive_pdfs(self, paths: Iterable[Path], hint: str) -> bool:
        """上一步帶入的 PDF（#76）：換掉目前選的 PDF、清除舊預覽並顯示提示條；參考條件表沿用目前選的。

        不自動載入預覽或核對。核對頁正在處理時不帶入；有還沒存檔的核對結果時先問。回傳是否帶入。
        """
        if self.working:
            messagebox.showinfo(
                "核對", "核對頁正在處理，這批 PDF 沒有帶入；處理完後請在核對頁自己選。", parent=self.root
            )
            return False
        if self.session.has_unsaved_result and not messagebox.askyesno(
            "核對",
            "核對頁還有沒存檔的核對結果，帶入新的 PDF 會清掉它。\n\n要帶入嗎？",
            icon=messagebox.WARNING,
            default=messagebox.NO,
            parent=self.root,
        ):
            return False
        self._select_pdfs(paths)
        self.hint.show(hint)
        return True

    def add_next_step(self, text: str, command: Callable[[], None]) -> None:
        """存檔成功後，在按鈕列最後出現「下一步」；按了才切頁（#76）。"""
        self.next_button = NextButton(self.actions, text, command)
        self._refresh_next()

    def _refresh_next(self) -> None:
        """目前的結果已經寫出核對結果檔時：狀態列變綠（#83）、顯示下一步；重新核對、改了放行或清除結果就恢復。"""
        receipt = self.session.receipt
        saved = receipt is not None and receipt.output is not None and not self._busy_any()
        self.status_label.configure(style="Done.TLabel" if saved else "TLabel")
        if self.next_button is None:
            return
        if saved:
            self.next_button.offer()
        else:
            self.next_button.withdraw()

    def _busy(self, busy):
        self.save_button.configure(state="disabled" if busy or not self.has_result else "normal")
        for control in self.controls:
            control.configure(state="disabled" if busy else "normal")
        if self.uses_database:
            self.sheet_button.configure(state="disabled")  # 資料庫來源不選參考條件表
        self.check_button.configure(state="disabled" if busy or self.shown is None else "normal")
        if self.session.write_back_error:
            self.write_back_button.pack(side="left", padx=(12, 0))
            self.write_back_button.configure(state="disabled" if busy else "normal")
        else:
            self.write_back_button.pack_forget()
        self._refresh_next()

    def _after(self, name: str, finish: Callable[[object], None], failure: str) -> None:
        """背景工作完成後在主執行緒呼叫 finish；IngestionError 顯示原因並清除結果。"""
        if self.closed or getattr(self, name) is None:
            return
        future = getattr(self, name)
        if not future.done():
            self.root.after(80, lambda: self._after(name, finish, failure))
            return
        setattr(self, name, None)
        try:
            finish(future.result())
        except IngestionError as e:
            self._reset()
            self.status.set(str(e))
        except Exception as e:
            if self.shown is None:  # 預覽讀取失敗（load 已先清掉預覽）：展開設定區方便改選
                self._expand()
            self.status.set(f"{failure}：{e}")
        self._busy(False)

    def _database_ready(self) -> bool:
        """資料庫來源：在主執行緒先登入（需要時問密碼）；取消時不開始。"""
        if not self.uses_database or self.session.database is None or self.session.database.ready():
            return True
        self.status.set("沒有登入資料庫，沒有開始。")
        return False

    def load(self):
        if self._busy_any() or not self._database_ready():
            return
        self._clear()
        self._busy(True)
        self.status.set("正在讀取參考條件表與說明書，請稍候…")
        self.pending = self.executor.submit(self.session.load_preview)
        self.root.after(
            80, lambda: self._after("pending", self._show_preview, "預覽讀取失敗，請確認檔案與設定後重新載入")
        )

    def _show_preview(self, preview):
        self.shown = preview
        for row in preview.rows:
            self.preview_table.insert("", "end", values=PanelOutcome.preview_row(row))
        self._fold()
        self.status.set(self.session.message)
        self.tabs.select(0)

    def start_check(self):
        if self._busy_any() or self.shown is None:
            return
        self._clear_results()
        self._busy(True)
        self.status.set("正在核對，請稍候…")
        self.tabs.select(self.results)
        self.check_pending = self.executor.submit(self.session.start_check)
        self.root.after(
            80, lambda: self._after("check_pending", self._show_outcome, "核對失敗，請確認檔案與設定後重新載入預覽")
        )

    def _show_outcome(self, outcome: PanelOutcome):
        self.results.show(outcome)
        self.kpis.show(outcome.kpis)
        self.has_result = True
        groups = outcome.not_covered
        _write(self.pending_text, "\n\n".join(f"【{g.issuer}】\n" + "\n".join(g.descriptions) for g in groups))
        self.tabs.tab(self.not_covered, text=f"待處理（{outcome.not_covered_count}）")
        self.status.set(outcome.headline)

    def toggle_release(self, items):
        """人工放行（先確認全部錯訊）或取消放行選取的那幾份；放行前的來源檢查在背景執行，結果失效時清空畫面並顯示原因。

        選一份照原本的確認視窗；多選時依 release_plan 只處理可以放行（或全部已放行時取消放行）的那幾份，
        放行前以可捲動視窗列出逐份錯訊與略過的原因（Issue #219）。
        """
        if self._busy_any():
            return
        if len(items) == 1:
            cancel, targets = items[0].released, tuple(items)
            if not cancel and not messagebox.askyesno(
                "人工放行", PanelOutcome.release_prompt(items[0]), parent=self.root
            ):
                return
        else:
            plan = self.session.release_plan(items)
            cancel, targets = plan.cancel, plan.targets
            if not targets:
                return
            prompt, confirm = PanelOutcome.release_plan_prompt(plan), PanelOutcome.release_plan_confirm(plan)
            if not cancel and not ConfirmDialog.ask(self.root, "人工放行", prompt, confirm, self.pixels):
                return
        focus = self.results.focused_item()  # 取消放行後游標留在原本那份
        action = self.session.cancel_release if cancel else self.session.release

        def run() -> IngestionError | None:
            try:
                action(*targets)
            except IngestionError as e:
                return e
            return None

        self._busy(True)
        self.status.set("確認來源中…")
        self.release_pending = self.executor.submit(run)
        self.root.after(
            80, lambda: self._after("release_pending", lambda e: self._show_release(targets, focus, e), "人工放行失敗")
        )

    def _show_release(self, items, focus, error: IngestionError | None):
        if error is not None:
            if self.session.outcome is None:  # 來源已變更、結果失效：清空畫面，不顯示空結果
                self._reset()
            self.status.set(str(error))
            return
        outcome = self.session.outcome
        self.results.show(outcome, select=outcome.selection_after_release_change(items), focus=focus)
        self.kpis.show(outcome.kpis)  # 放行改了類別的份數
        self.status.set(self.session.message)

    def save(self):
        if not self.has_result or self._busy_any() or not self._database_ready():
            return
        self._busy(True)
        destination = filedialog.askdirectory(parent=self.root, title="選取核對結果檔的保存資料夾")
        if not destination:
            self.status.set(self.session.save(None).summary)
            self._busy(False)
            return
        self.status.set("儲存中…")
        self.save_pending = self.executor.submit(self.session.save, Path(destination))
        self.root.after(80, lambda: self._after("save_pending", self._show_receipt, "儲存失敗，核對結果仍保留"))

    def _show_receipt(self, receipt: SaveReceipt):
        if self.session.outcome is None:
            self._reset()
        written = self.session.use_database and receipt.output is not None  # 資料庫來源：另寫寫回的結果
        self.status.set(receipt.summary + (f"\n{self.session.message}" if written else ""))
        if receipt.output is not None and self.on_saved is not None:
            self.on_saved(receipt.output)

    def retry_write_back(self):
        """寫回資料庫失敗、排除後再寫一次（核對結果檔不重寫）。"""
        if self._busy_any() or not self.session.write_back_error or not self._database_ready():
            return
        self._busy(True)
        self.status.set("寫回資料庫中…")

        def run() -> str:
            try:
                return self.session.retry_write_back()
            except IngestionError as e:
                return str(e)

        self.write_back_pending = self.executor.submit(run)
        self.root.after(80, lambda: self._after("write_back_pending", self.status.set, "寫回資料庫失敗"))

    @property
    def working(self) -> bool:
        """正在背景讀檔、核對、放行、存檔或匯出；關閉視窗前提醒用。"""
        return self._busy_any()

    def can_edit_approval_dates(self) -> bool:
        """「維護」選單：核對頁在背景處理時不能改審查通過日期（改了會清除預覽與結果）。"""
        return not self.working

    def can_export_bundle(self) -> bool:
        """「維護」選單：有核對結果、其中有未通過的商品，而且核對頁沒在處理時才能匯出回報包。"""
        return self.has_result and not self._busy_any() and self.session.can_export_bundle

    def export_bundle(self):
        """未通過的商品匯出成回報包；資料夾已存在或寫不進去時只顯示原因，來源已變更時清空結果。"""
        if not self.can_export_bundle():
            messagebox.showinfo(
                "匯出回報包", "核對完、有未通過的商品時才能匯出回報包；核對頁正在處理時請等它完成。", parent=self.root
            )
            return
        self._busy(True)
        destination = filedialog.askdirectory(parent=self.root, title="選取回報包的保存資料夾")
        if not destination:
            self.status.set("已取消匯出回報包，核對結果仍保留。")
            self._busy(False)
            return

        def run() -> str:
            try:
                self.session.export_bundle(Path(destination))
            except IngestionError as e:
                if self.session.outcome is None:  # 來源已變更、結果失效：交給 _after 清空畫面
                    raise
                return f"回報包未匯出：{e}"
            return self.session.message

        self.status.set("匯出回報包中…")
        self.export_pending = self.executor.submit(run)
        self.root.after(80, lambda: self._after("export_pending", self.status.set, "匯出回報包失敗，核對結果仍保留"))

    def edit_approval_dates(self):
        if not self.can_edit_approval_dates():
            messagebox.showinfo("審查通過日期", "核對頁正在處理，請等它完成再修改。", parent=self.root)
            return
        try:
            ApprovalDatesDialog(self.root, self.session, self.pixels, self._approval_dates_changed)
        except IngestionError as e:
            messagebox.showerror("審查通過日期", str(e), parent=self.root)

    def _approval_dates_changed(self):
        """設定檔已改寫：工作階段已清除預覽與結果，畫面跟著清空。"""
        self._reset()
        self.status.set(self.session.message)
        self._busy(False)

    def show_config_paths(self):
        paths = "\n".join(f"{label}：{path}" for label, path in self.session.config_paths)
        messagebox.showinfo("使用的設定檔", paths, parent=self.root)

    def _watch_sources(self):
        if self.closed:
            return
        if self.validation is not None and self.validation.done():
            try:
                valid = self.validation.result()
            except Exception:
                valid = False
            if not self._busy_any() and not valid:  # 回到主執行緒才清除；快照已換過（重新載入）就不動
                self.session.invalidate(self.checking_for)
                if self.session.preview is None and self.shown is not None:
                    self._reset()
                    self.status.set(self.session.message)
            self.validation = None
            self.checking_for = None
            self.root.after(1500, self._watch_sources)
            return
        snapshot = self.session.snapshot
        if not self._busy_any() and snapshot is not None and self.validation is None:
            self.checking_for = snapshot
            self.validation = self.executor.submit(snapshot.still_valid)
        self.root.after(100 if self.validation is not None else 1500, self._watch_sources)

    def close(self):
        """視窗關閉前：停止背景工作與來源檢查（視窗由外殼關閉）。"""
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FCN Workflow 視窗：每日匯入、起單資料、準備、核對、放回與寄信草稿；"
        "核對時以參考條件表預覽並核對多份說明書 PDF，按儲存才寫出核對結果檔。"
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="設定檔資料夾（預設 config）；缺少參考條件表格式或上手編號對照時改用程式內建設定",
    )
    parser.add_argument(
        "--builtin-config-dir", type=Path, default=None, help="程式內建設定資料夾（雙擊入口傳入專案的 config）"
    )
    parser.add_argument("--order-formats-dir", type=Path, help=argparse.SUPPRESS)  # 舊版啟動器仍會傳入
    parser.add_argument("--review-standard", type=Path, default=DEFAULTS.review_standard, help="審查標準設定檔")
    parser.add_argument(
        "--install-root", type=Path, help="雙擊入口提供的安裝目錄；上次選的資料夾、.env 與核對紀錄都在它底下"
    )
    args = parser.parse_args(argv)
    if args.config_dir is None:
        args.config_dir = args.order_formats_dir.parent if args.order_formats_dir else CONFIG_DIR
    return args


def session_from_args(args: argparse.Namespace) -> PanelSession:
    """根目錄與設定檔一致：雙擊入口給的專案根目錄，直接啟動時為執行目錄。"""
    return PanelSession(
        args.review_standard,
        args.config_dir,
        builtin_config_dir=args.builtin_config_dir,
        install_root=args.install_root,
    )


def main(argv: list[str] | None = None) -> int:
    """`fcn-panel` 與核對的雙擊入口：開 FCN Workflow 視窗並停在核對頁（#74），參數照舊。"""
    from sp_workflow.app import main as open_window  # 視窗外殼在 sp_workflow；在函式裡 import，載入模組時不互相 import

    open_window(argv, start_on_check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
