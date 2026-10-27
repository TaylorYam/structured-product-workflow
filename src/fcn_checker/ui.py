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
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk
from typing import TypeVar

from fcn_checker import shapes

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


# 整個視窗的配色（#83，改成 macOS 的質感）：淺灰底、白色卡片、近黑的字；只用一個藍當強調色（按鈕、目前所在、選取），
# 紅色只給有問題的、綠色只給完成。對比（WCAG）：白字在 ACCENT 上 5.5:1、TEXT_SECONDARY 在白底 5.1:1、在 BACKGROUND 4.7:1
BACKGROUND = "#F5F5F7"  # 視窗底、頂端列、步驟欄
SURFACE = "#FFFFFF"  # 卡片、表格、輸入框
FIELD_READONLY = "#F7F7F9"
HOVER = "#EDEDF0"
PRESSED = "#E2E2E7"
BORDER = "#E3E3E8"  # 卡片與分隔線的細線
CONTROL_BORDER = "#CFCFD4"  # 按鈕、輸入框的外框
TEXT = "#1D1D1F"
TEXT_SECONDARY = "#6E6E73"
TEXT_DISABLED = "#A1A1A6"
NAVY = "#0B1F3A"  # 提示條的字
ACCENT = "#0A64D8"
ACCENT_ACTIVE = "#0853B5"
ACCENT_PRESSED = "#06448F"
ACCENT_DISABLED = "#AFC9EE"
ACCENT_SOFT = "#E7F0FC"
SCROLL_THUMB = "#C7C7CC"
SCROLL_THUMB_ACTIVE = "#9E9EA3"
DANGER = "#C4262E"
SUCCESS = "#1E7A4C"
SUCCESS_SOFT = "#E6F4EC"
FONT = "Microsoft JhengHei UI"  # 公司 Windows 都有；數字另用 Segoe UI
NUMBER_FONT = "Segoe UI"
# 字級（點數，96 DPI 下 10 點約 13 像素，和 macOS 內文一樣大）：大標 > 區塊標題 > 內文 > 說明小字；層次靠粗細與顏色，不只靠大小
TITLE_FONT = (FONT, 17, "bold")
SECTION_FONT = (FONT, 10, "bold")
BODY_FONT = (FONT, 10)
CAPTION_FONT = (FONT, 9)
VALUE_FONT = (NUMBER_FONT, 17, "bold")  # 重點數字
CODE_FONT = (NUMBER_FONT, 11)  # 貼 TDCC 的欄位：數字好對齊
HEADING_FONT = (FONT, 9, "bold")  # 表頭、步驟欄的分組標題


def flat(color: str) -> dict[str, str]:
    """clam 主題的元件有立體邊（lightcolor／darkcolor）；三個設成同一色就是平的。"""
    return {"background": color, "lightcolor": color, "darkcolor": color}


class _Shapes:
    """步驟欄、完成條的綠勾與勾選框（`fcn_checker.shapes` 畫的 PNG），依螢幕縮放畫一次。

    圖要留著參照，不然 Tk 的圖會被回收；同一個 Tk 只畫一次（測試一個 Tk 開很多視窗）。
    """

    def __init__(self, root: tk.Misc, scale: float) -> None:
        self.root = root
        self.scale = scale
        self.images: list[tk.PhotoImage] = []
        self.derived: set[str] = set()  # 已經建好的「Bg<色碼>.<樣式>」（`on_parent`）
        self.done = self.check(SUCCESS)

    def px(self, value: float) -> int:
        return max(1, round(value * self.scale))

    def image(self, width: int, height: int, layers: Sequence[shapes.Layer]) -> tk.PhotoImage:
        image = tk.PhotoImage(
            master=self.root, data=shapes.png_data(shapes.render(width, height, layers)), format="png"
        )
        self.images.append(image)
        return image

    def check(self, color: str) -> tk.PhotoImage:
        """完成的勾勾（步驟欄、完成條）。"""
        side, u = self.px(14), self.scale
        tick = shapes.polyline([(3 * u, 7.4 * u), (5.9 * u, 10.2 * u), (11 * u, 4 * u)], 1.8 * u)
        return self.image(side, side, [shapes.Layer(tick, color)])

    def checkbox(self, fill: str, border: str | None, mark: str | None, *, focus: bool = False) -> tk.PhotoImage:
        """勾選框（#268）：方框，勾起來是勾勾——clam 的勾選框勾起來畫 X，看起來像「不要」。fill 底色、border 外框色
        （None 是沒有外框）、mark 勾勾的顏色（None 是沒勾）；focus 時外框加粗。右邊留和文字之間的空白。"""
        side, gap, u = self.px(14), self.px(6), self.scale
        box = shapes.rounded_box(0, 0, side, side, 0)
        layers = [shapes.Layer(box, border or fill)]
        if border is not None:
            ring = self.px(2) if focus else self.px(1)
            layers.append(shapes.Layer(shapes.rounded_box(ring, ring, side - ring, side - ring, 0), fill))
        if mark is not None:
            tick = shapes.polyline([(3.2 * u, 7.2 * u), (5.9 * u, 9.9 * u), (10.9 * u, 4.2 * u)], 1.9 * u)
            layers.append(shapes.Layer(tick, mark))
        return self.image(side + gap, side, layers)


def _checkbox_element(style: ttk.Style, shape: _Shapes) -> None:
    """勾選框的圖片元件 `Check.indicator`（#268）：圖小、大小固定、不拉伸，每個勾選框只貼一次，不會拖慢重畫；
    每個 Tk 只建一次。"""
    box = shape.checkbox
    style.element_create(
        "Check.indicator",
        "image",
        box(SURFACE, CONTROL_BORDER, None),
        ("disabled selected", box(ACCENT_DISABLED, None, SURFACE)),
        ("disabled", box(FIELD_READONLY, BORDER, None)),
        ("pressed selected", box(ACCENT_PRESSED, None, SURFACE)),
        ("selected", box(ACCENT, None, SURFACE)),
        ("focus", box(SURFACE, ACCENT, None, focus=True)),
        ("active", box(SURFACE, TEXT_SECONDARY, None)),
        sticky="",
    )


def _layouts(style: ttk.Style) -> None:
    """平面版面（#266）：全部用 clam 自己畫的元件，不用圖片元件——圖片要半透明混色，重畫慢，視窗從工具列打開時
    會先黑一下（#263、#265）。樣式名稱照舊（測試與各分頁都用這些名稱）。"""
    button = [
        (
            "Button.border",
            {
                "sticky": "nswe",
                "children": [
                    ("Button.padding", {"sticky": "nswe", "children": [("Button.label", {"sticky": "nswe"})]})
                ],
            },
        )
    ]
    for name in (
        "TButton",
        "Primary.TButton",
        "Next.TButton",
        "Step.TButton",
        "Toolbar.TButton",
        "CurrentStep.TButton",
    ):
        style.layout(name, button)  # 拿到焦點時外框變藍，不要 clam 的虛線框
    label = [("Checkbutton.label", {"sticky": "nswe"})]
    check = [
        ("Check.indicator", {"side": "left", "sticky": ""}),
        ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": label}),
    ]
    for name in ("TCheckbutton", "Card.TCheckbutton"):
        style.layout(name, [("Checkbutton.padding", {"sticky": "nswe", "children": check})])
    # 捲軸把手照 clam 的 thumb（名稱以 .thumb 結尾，ttk 才認得把手、拖得動），不要兩端的箭頭。
    for orient in ("Vertical", "Horizontal"):
        sticky = "ns" if orient == "Vertical" else "we"
        style.layout(
            f"{orient}.TScrollbar",
            [
                (
                    f"{orient}.Scrollbar.trough",
                    {"sticky": sticky, "children": [(f"{orient}.Scrollbar.thumb", {"sticky": "nswe"})]},
                )
            ],
        )
    style.layout(
        "Treeview", [("Treeview.padding", {"sticky": "nswe", "children": [("Treeview.treearea", {"sticky": "nswe"})]})]
    )
    style.layout(
        "Heading",
        [
            ("Treeheading.cell", {"sticky": "nswe"}),
            (
                "Treeheading.border",
                {
                    "sticky": "nswe",
                    "children": [
                        (
                            "Treeheading.padding",
                            {
                                "sticky": "nswe",
                                "children": [
                                    ("Treeheading.image", {"side": "right", "sticky": ""}),
                                    ("Treeheading.text", {"sticky": "we"}),
                                ],
                            },
                        )
                    ],
                },
            ),
        ],
    )


def apply_style(root: tk.Tk) -> Callable[[float], int]:
    """整個視窗共用的配色、字型、按鈕與表格樣式（#74 起每個分頁都用這一套；#83 蘋果 × 麥肯錫風格；#266 平面）。

    回傳 pixels：把 96 DPI 下的像素依目前螢幕縮放換算成實際像素。視窗外殼（頂端列、步驟欄）的樣式在 sp_workflow.app。
    全部是 clam 的平面元件（直角、細框），拿到焦點時外框變藍；`_Shapes` 只畫完成的綠勾，同一個 Tk 只畫一次。
    """
    scale = root.winfo_fpixels("1i") / 96

    def pixels(value):
        return round(value * scale)

    root.configure(background=BACKGROUND)
    style = ttk.Style(root)
    style.theme_use("clam")
    tk_root = root.nametowidget(".")
    if getattr(tk_root, "_mac_shapes", None) is None:
        tk_root._mac_shapes = _Shapes(tk_root, scale)  # 留著圖的參照
        _checkbox_element(style, tk_root._mac_shapes)
        for name in PARENT_FILLED_CLASSES:  # 綁在整個 Tk 上，綁一次就好
            root.bind_class(name, "<Map>", lambda event: match_parent_background(event.widget), add="+")
    _layouts(style)
    style.configure(
        ".",
        background=BACKGROUND,
        foreground=TEXT,
        font=BODY_FONT,
        focuscolor=ACCENT_SOFT,
        bordercolor=BORDER,
        selectbackground=ACCENT_SOFT,
        selectforeground=TEXT,
        insertcolor=TEXT,
    )
    style.configure(".", lightcolor=BORDER, darkcolor=BORDER)
    style.configure("Card.TFrame", background=SURFACE, bordercolor=BORDER, relief="solid", borderwidth=1)
    style.configure("CardBody.TFrame", background=SURFACE)
    style.configure("Muted.TLabel", foreground=TEXT_SECONDARY)
    style.configure("Step.TLabel", foreground=TEXT_SECONDARY, font=CAPTION_FONT)
    style.configure("Title.TLabel", foreground=TEXT, font=TITLE_FONT)
    style.configure("Card.TLabel", background=SURFACE)
    style.configure("CardSection.TLabel", background=SURFACE, font=SECTION_FONT)
    style.configure("Caption.TLabel", foreground=TEXT_SECONDARY, font=CAPTION_FONT)
    style.configure("Placeholder.TLabel", background=SURFACE, foreground=TEXT_SECONDARY)
    style.configure("Field.TFrame", background=SURFACE, bordercolor=CONTROL_BORDER, relief="solid", borderwidth=1)
    style.map("Field.TFrame", bordercolor=[("focus", ACCENT)])  # 裡面的文字框拿到游標（`FieldBox`）
    style.configure("Card.TRadiobutton", background=SURFACE)
    style.configure("Card.TCheckbutton", background=SURFACE)
    style.configure("TCheckbutton", padding=(0, 2))
    style.map("TCheckbutton", foreground=[("disabled", TEXT_DISABLED)])
    indicator_border = [("disabled", BORDER), ("selected", ACCENT), ("focus", ACCENT), ("active", TEXT_SECONDARY)]
    style.configure(  # 選項鈕用 clam 的平面圓圈；勾選框用 `Check.indicator` 的圖
        "TRadiobutton",
        padding=(0, 2),
        indicatorsize=pixels(14),
        indicatormargin=(0, 0, pixels(6), 0),
        indicatorbackground=SURFACE,
        indicatorforeground=SURFACE,  # 圓點：藍底上的白色
        upperbordercolor=CONTROL_BORDER,
        lowerbordercolor=CONTROL_BORDER,
    )
    style.map(
        "TRadiobutton",
        foreground=[("disabled", TEXT_DISABLED)],
        indicatorbackground=[
            ("disabled selected", ACCENT_DISABLED),
            ("disabled", FIELD_READONLY),
            ("pressed selected", ACCENT_PRESSED),
            ("selected", ACCENT),
        ],
        upperbordercolor=indicator_border,
        lowerbordercolor=indicator_border,
    )
    style.configure("CardMuted.TLabel", background=SURFACE, foreground=TEXT_SECONDARY, font=CAPTION_FONT)
    style.configure("KpiCaption.TLabel", background=SURFACE, foreground=TEXT_SECONDARY, font=CAPTION_FONT)
    style.configure("KpiValue.TLabel", background=SURFACE, foreground=TEXT, font=VALUE_FONT)
    style.configure("KpiBad.TLabel", background=SURFACE, foreground=DANGER, font=VALUE_FONT)

    def button(name: str, fill: str, states: list, border: str, borders: list) -> None:
        """平面按鈕：底色 fill、外框 border，依狀態換色（滑過、按下、停用；拿到焦點時換外框）。"""
        style.configure(name, bordercolor=border, relief="solid", borderwidth=1, **flat(fill))
        style.map(name, background=states, lightcolor=states, darkcolor=states, bordercolor=borders)

    plain = [("pressed", PRESSED), ("active", HOVER)]
    button(
        "TButton",
        SURFACE,
        [("disabled", FIELD_READONLY), *plain],
        CONTROL_BORDER,
        [("disabled", BORDER), ("focus", ACCENT)],
    )
    accent = [("disabled", ACCENT_DISABLED), ("pressed", ACCENT_PRESSED), ("active", ACCENT_ACTIVE)]
    button("Primary.TButton", ACCENT, accent, ACCENT, [accent[0], ("focus", NAVY), *accent[1:]])
    for name in PARENT_FILLED[:3]:  # 沒有外框的按鈕：底色跟著放的地方（`on_parent`），滑過才有底色
        button(name, BACKGROUND, plain, BACKGROUND, [("focus", ACCENT)])
    button("CurrentStep.TButton", ACCENT_SOFT, [], ACCENT_SOFT, [("focus", ACCENT)])
    style.configure("TButton", padding=(pixels(14), pixels(5)), width=-4, anchor="center")
    style.map("TButton", foreground=[("disabled", TEXT_DISABLED)])
    style.configure("Primary.TButton", foreground=SURFACE, font=SECTION_FONT)
    style.map("Primary.TButton", foreground=[("disabled", SURFACE)])
    style.configure("Next.TButton", foreground=ACCENT, font=SECTION_FONT, padding=(pixels(10), pixels(5)))
    style.map("Next.TButton", foreground=[("pressed", ACCENT_PRESSED), ("active", ACCENT_ACTIVE)])
    style.configure("Toolbar.TButton", foreground=ACCENT, padding=(pixels(10), pixels(5)), width=0)
    field = {"bordercolor": CONTROL_BORDER, "lightcolor": SURFACE, "darkcolor": SURFACE}
    focused = {"bordercolor": [("disabled", BORDER), ("focus", ACCENT)], "lightcolor": [("focus", ACCENT)]}
    style.configure("TEntry", padding=(pixels(8), pixels(5)), fieldbackground=SURFACE, **field)
    style.map("TEntry", fieldbackground=[("readonly", FIELD_READONLY), ("disabled", FIELD_READONLY)], **focused)
    style.configure(
        "Path.TEntry",
        padding=(pixels(10), pixels(5)),
        foreground=TEXT_SECONDARY,
        fieldbackground=HOVER,
        bordercolor=HOVER,
        lightcolor=HOVER,
        darkcolor=HOVER,
    )
    style.map("Path.TEntry", fieldbackground=[("readonly", HOVER)])
    style.configure(
        "TCombobox",
        padding=(pixels(8), pixels(4)),
        arrowsize=pixels(12),
        arrowcolor=TEXT_SECONDARY,
        background=SURFACE,  # 右邊箭頭那一格
        **field,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("disabled", FIELD_READONLY), ("readonly", SURFACE)],
        selectbackground=[("readonly", SURFACE)],
        selectforeground=[("readonly", TEXT)],
        foreground=[("disabled", TEXT_DISABLED)],
        arrowcolor=[("disabled", TEXT_DISABLED)],
        background=[("disabled", FIELD_READONLY), ("active", HOVER)],
        **focused,
    )
    style.configure(
        "TLabelframe",
        padding=pixels(10),
        labeloutside=True,
        labelmargins=(0, 0, 0, pixels(6)),
        relief="solid",
        borderwidth=1,
        bordercolor=BORDER,
    )
    style.configure("TLabelframe.Label", font=SECTION_FONT, foreground=TEXT)
    style.configure(
        "Treeview", background=SURFACE, fieldbackground=SURFACE, rowheight=pixels(28), borderwidth=0, font=BODY_FONT
    )
    style.configure(
        "Treeview.Heading",
        foreground=TEXT_SECONDARY,
        font=HEADING_FONT,
        padding=(pixels(8), pixels(5)),
        background=SURFACE,
        bordercolor=SURFACE,
        lightcolor=SURFACE,
        darkcolor=BORDER,  # 平面格子：只有下面與右邊一條細線（欄的分隔）
        relief="raised",
        borderwidth=1,
    )
    style.map("Treeview.Heading", background=[("active", SURFACE), ("pressed", SURFACE)])
    style.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", TEXT)])
    style.configure(
        "TNotebook", borderwidth=0, tabmargins=(0, 0, 0, pixels(8)), bordercolor=BACKGROUND, **flat(BACKGROUND)
    )
    style.configure(
        "TNotebook.Tab",
        foreground=TEXT_SECONDARY,
        padding=(pixels(16), pixels(5)),
        bordercolor=BACKGROUND,
        **flat(BACKGROUND),
    )
    segment = [("selected", PRESSED), ("active", HOVER)]  # 分段按鈕：選中的深一點，滑過淡淡的
    style.map(
        "TNotebook.Tab",
        foreground=[("selected", TEXT), ("active", TEXT)],
        background=segment,
        lightcolor=segment,
        darkcolor=segment,
        bordercolor=segment,
        expand=[],  # 選中的不放大，標籤不跳動
    )
    style.configure(
        "TScrollbar",
        troughcolor=SURFACE,
        background=SCROLL_THUMB,
        bordercolor=SURFACE,  # 把手四周留一圈白，看起來細一點
        lightcolor=SCROLL_THUMB,
        darkcolor=SCROLL_THUMB,
        arrowsize=pixels(11),
        gripcount=0,
    )
    hidden = [("disabled", SURFACE)]  # 不用捲時把手藏起來（`Scrollbar`）
    active = [("pressed", SCROLL_THUMB_ACTIVE), ("active", SCROLL_THUMB_ACTIVE)]
    style.map("TScrollbar", background=hidden + active, lightcolor=hidden + active, darkcolor=hidden + active)
    style.configure("TableCard.TFrame", background=SURFACE, bordercolor=BORDER, relief="solid", borderwidth=1)
    style.configure("Hint.TFrame", background=ACCENT_SOFT)
    style.configure("Hint.TLabel", background=ACCENT_SOFT, foreground=NAVY)
    # 這一步做完時，狀態列變成綠色結果條（#83）
    style.configure(
        "Done.TLabel", foreground=SUCCESS, font=SECTION_FONT, padding=(pixels(12), pixels(7)), background=SUCCESS_SOFT
    )
    for name in ("TCheckbutton", "TRadiobutton", "TEntry"):
        style.map(name, background=[])  # 拿掉 clam 依狀態換的底色：勾選框滑過時旁邊的字不變底色
    # 不是 ttk 的元件（貼 TDCC 的文字框、清單、下拉選單展開的清單）
    for pattern, value in (
        ("*Listbox.font", BODY_FONT),
        ("*Listbox.background", SURFACE),
        ("*Listbox.foreground", TEXT),
        ("*Listbox.selectBackground", ACCENT_SOFT),
        ("*Listbox.selectForeground", TEXT),
        ("*Listbox.relief", "flat"),
        ("*Listbox.borderWidth", 0),
        ("*Listbox.highlightThickness", 1),
        ("*Listbox.highlightBackground", CONTROL_BORDER),
        ("*Listbox.highlightColor", ACCENT),
        ("*TCombobox*Listbox.highlightThickness", 0),
        ("*Text.selectBackground", ACCENT_SOFT),
        ("*Text.selectForeground", TEXT),
        ("*Text.inactiveSelectBackground", HOVER),
    ):
        root.option_add(pattern, value)
    return pixels


PARENT_FILLED = ("Next.TButton", "Step.TButton", "Toolbar.TButton", "TCheckbutton", "TRadiobutton")
# 底色要跟著放的地方的樣式：沒有外框的按鈕、勾選框與選項鈕（旁邊的字的底）
PARENT_FILLED_CLASSES = ("TButton", "TCheckbutton", "TRadiobutton")


def _base_style(name: str) -> str:
    """衍生樣式「Bg<色碼>.<原樣式>」的原樣式。"""
    return name.split(".", 1)[1] if name.startswith("Bg") and "." in name else name


def _parent_background(widget: tk.Misc) -> str | None:
    parent = widget.nametowidget(widget.winfo_parent())
    if isinstance(parent, ttk.Widget):
        style = _base_style(str(parent.cget("style"))) or parent.winfo_class()
        return str(ttk.Style(widget).lookup(style, "background")) or None
    try:
        return str(parent.cget("background"))
    except tk.TclError:
        return None


def on_parent(widget: tk.Misc, name: str) -> str:
    """name 這個樣式（`PARENT_FILLED`）放在 widget 的父元件上要用的樣式名：底色（沒有外框的按鈕連外框）要和父元件
    看起來的顏色一樣。不同時用「Bg<色碼>.<原樣式>」這個衍生樣式（照樣繼承原樣式的一切，包括滑過、焦點的換色，只換底色）。"""
    style = ttk.Style(widget)
    background = _parent_background(widget)
    if not background or not background.startswith("#") or style.lookup(name, "background") == background:
        return name
    derived = f"Bg{background.lstrip('#').upper()}.{name}"
    shape = getattr(widget.nametowidget("."), "_mac_shapes", None)
    made = shape.derived if shape is not None else set()
    if derived not in made:  # 每次設定樣式整個視窗都會重畫：建過的不再設
        style.configure(derived, bordercolor=background, **flat(background))
        made.add(derived)
    return derived


def match_parent_background(widget: tk.Misc) -> None:
    """`PARENT_FILLED` 的元件第一次出現（<Map>）時，底色改成父元件看起來的顏色（`on_parent`）；其他的不動。"""
    if not isinstance(widget, ttk.Widget):  # Tcl 自己建的元件（例如下拉選單展開的清單）
        return
    current = str(widget.cget("style"))
    name = _base_style(current) or widget.winfo_class()
    if name in PARENT_FILLED:
        matched = on_parent(widget, name)
        if matched != current:
            widget.configure(style=matched)


STEP_DONE, STEP_UNDONE = "<<StepDone>>", "<<StepUndone>>"  # 狀態列變成／不再是綠色結果條；視窗的步驟欄聽這兩個


def show_done(label: ttk.Label, done: bool, normal: str) -> None:
    """狀態列：做完時是綠色結果條（#83），否則用 normal 樣式；並通知步驟欄在那一步打勾或拿掉勾。"""
    label.configure(style="Done.TLabel" if done else normal)
    label.event_generate(STEP_DONE if done else STEP_UNDONE)


def done_mark(widget: tk.Misc) -> tk.PhotoImage | str:
    """完成的綠色勾勾（`apply_style` 畫好的）；還沒套樣式時是空字串（不顯示圖）。"""
    shape = getattr(widget.nametowidget("."), "_mac_shapes", None)
    return shape.done if shape is not None else ""


NO_LINE_START = "，。、；：！？」』）】》〉〕｝％…,.;:!?)]}%"  # 不放在行首的標點（跟著前一個字）
NO_LINE_END = "「『（【《〈〔｛([{"  # 不放在行尾的標點（跟著後一個字）


def _wide(char: str) -> bool:
    """中日韓文字與全形標點：每個字之間都可以斷行。"""
    return unicodedata.east_asian_width(char) in ("W", "F")


def _break_units(paragraph: str) -> list[str]:
    """拆成不能再分開的單位：一個中文字（連同後面的句號、前面的左括號）、一個英數字詞或一個空白。"""
    units: list[str] = []
    for char in paragraph:
        last = units[-1] if units and units[-1] != " " else ""
        if char in NO_LINE_START and len(units) > 1 and units[-1] == " ":
            units.pop()
            units[-1] += " " + char  # 空白後面的句號也跟著前一個字
        elif char == " ":
            units.append(" ")
        elif last and (char in NO_LINE_START or last[-1] in NO_LINE_END or not (_wide(char) or _wide(last[-1]))):
            units[-1] += char
        else:
            units.append(char)
    return units


def break_lines(text: str, width: int, measure: Callable[[str], int]) -> str:
    """依寬度（像素）把 text 斷成多行（#255）：中文字之間都可以斷，英數字詞不拆開，句號、逗號、右括號不放在行首，
    左括號不放在行尾；原本的換行照留，一個字詞就比整行寬時才照字拆。width 不是正數時不斷行。

    Tk 的 wraplength 只在空白處斷行：中英夾雜的句子會在「貼上 TDCC…」這種空白處斷出很短的一行。"""
    if width <= 0:
        return text
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for unit in _break_units(paragraph):
            if unit == " ":
                line += " " if line else ""
                continue
            if line.strip() and measure(line + unit) > width:
                lines.append(line.rstrip())
                line = ""
            line += unit
            while len(line) > 1 and measure(line) > width:
                fit = _fit(line, width, measure)
                lines.append(line[:fit])
                line = line[fit:]
        lines.append(line.rstrip())
    return "\n".join(lines)


def _fit(line: str, width: int, measure: Callable[[str], int]) -> int:
    """line 開頭最多幾個字放得進 width（至少 1 個）；line 整個放不進。二分搜尋：很長的字詞也只量幾次。"""
    low, high = 1, len(line) - 1
    while low < high:
        middle = (low + high + 1) // 2
        if measure(line[:middle]) <= width:
            low = middle
        else:
            high = middle - 1
    return low


class _Wrapping:
    """`WrapLabel`／`WrapTkLabel` 共用：wraplength 不交給 Tk，改用 `break_lines` 斷好行再顯示（#255）。

    text、textvariable（要給 `tk.Variable`）、wraplength（像素或 Tk 的長度寫法）照原本的標籤用，之後 configure
    也可以改；cget("text") 回傳沒斷行的原文。字型（例如換樣式變粗體）、文字或寬度改變時重新斷行。
    """

    OWN = ("text", "textvariable", "wraplength")  # 由這裡管、不交給 Tk 的選項

    def __init__(self, master: tk.Misc | None = None, **kw: object) -> None:
        self._source = str(kw.pop("text", ""))
        self._width: object = kw.pop("wraplength", 0)
        variable = kw.pop("textvariable", None)
        self._variable: tk.Variable | None = None
        self._trace: str | None = None
        self._shown: tuple[str, int, str] | None = None
        super().__init__(master, **kw)
        self._follow(variable)
        self.bind("<Destroy>", lambda _event: self._follow(None), add="+")  # 視窗被關掉時也停止跟著變數
        self._wrap()

    def configure(self, cnf: dict | str | None = None, **kw: object) -> object:
        if isinstance(cnf, str):  # 查一個選項：照 Tk 回傳 (名稱, 資料庫名稱, 類別, 預設值, 目前的值)
            if cnf in self.OWN:
                return (cnf, *super().configure(cnf)[1:4], self.cget(cnf))
            return super().configure(cnf)
        kw = {**(cnf or {}), **kw}
        if not kw:
            return super().configure()
        if "text" in kw:
            self._source = str(kw.pop("text"))
        if "textvariable" in kw:
            self._follow(kw.pop("textvariable") or None)
        if "wraplength" in kw:
            self._width = kw.pop("wraplength")
        result = super().configure(**kw) if kw else None
        self._wrap()
        return result

    config = configure

    def cget(self, key: str) -> object:
        if key == "text":
            return self._text()
        if key == "textvariable":
            return str(self._variable) if self._variable else ""
        if key == "wraplength":
            return self._width
        return super().cget(key)

    def __getitem__(self, key: str) -> object:
        return self.cget(key)

    def _follow(self, variable: tk.Variable | None) -> None:
        """改跟著這個變數（None：不跟任何變數）。"""
        if self._variable is not None and self._trace is not None:
            self._variable.trace_remove("write", self._trace)
        self._variable = variable
        self._trace = variable.trace_add("write", lambda *_: self._wrap()) if variable is not None else None

    def _text(self) -> str:
        return str(self._variable.get()) if self._variable is not None else self._source

    def _font(self) -> str:
        font = str(super().cget("font"))
        if not font and isinstance(self, ttk.Widget):
            font = str(ttk.Style(self).lookup(str(super().cget("style")) or self.winfo_class(), "font"))
        return font or "TkDefaultFont"

    def _wrap(self) -> None:
        text, width, font = self._text(), round(self.winfo_fpixels(self._width or 0)), self._font()
        if self._shown == (text, width, font):
            return  # 沒變就不重設：重設會讓版面重算、又觸發 <Configure>
        self._shown = (text, width, font)

        def measure(part: str) -> int:
            return int(self.tk.call("font", "measure", font, "-displayof", self, part))

        super().configure(text=break_lines(text, width, measure), wraplength=0)


class WrapLabel(_Wrapping, ttk.Label):
    """中英夾雜也照寬度斷行的 `ttk.Label`（#255）；參數同 `ttk.Label`。"""


class WrapTkLabel(_Wrapping, tk.Label):
    """中英夾雜也照寬度斷行的 `tk.Label`（#255）；參數同 `tk.Label`。"""


class PageHeader(ttk.Frame):
    """分頁最上方：「步驟 N／總數」、標題（只寫功能，#85）與一行說明（#83）。

    步驟編號由視窗依分頁順序填入 step；畫面需要騰出高度時可用 show_description 收起說明（#108）。
    """

    def __init__(self, parent: tk.Misc, title: str, description: str):
        super().__init__(parent)
        self.step = tk.StringVar()
        ttk.Label(self, textvariable=self.step, style="Step.TLabel").pack(anchor="w")
        ttk.Label(self, text=title, style="Title.TLabel").pack(anchor="w", pady=(2, 4))
        self.description = text = WrapLabel(self, text=description, style="Muted.TLabel", wraplength=900)
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
        for index, (caption, value, bad) in enumerate(self.values):
            if index:  # 數字之間一條細的直線，不再每個數字各框一格
                tk.Frame(self, width=1, background=BORDER).grid(row=0, column=2 * index - 1, sticky="ns", padx=20)
            tile = ttk.Frame(self, style="CardBody.TFrame")
            tile.grid(row=0, column=2 * index, sticky="w")
            ttk.Label(tile, text=str(value), style="KpiBad.TLabel" if bad else "KpiValue.TLabel").pack(anchor="w")
            ttk.Label(tile, text=caption, style="KpiCaption.TLabel").pack(anchor="w")
        self.grid()

    def hide(self) -> None:
        self.values = ()
        self.grid_remove()


class FieldBox(ttk.Frame):
    """輸入框外框，給 ttk 沒有的多行文字框（貼 TDCC）用：裡面的文字框拿到游標時外框變成藍色。"""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, style="Field.TFrame", padding=4)

    def watch(self, widget: tk.Misc) -> None:
        widget.bind("<FocusIn>", lambda _event: self.state(["focus"]), add="+")
        widget.bind("<FocusOut>", lambda _event: self.state(["!focus"]), add="+")


class HintBar(ttk.Frame):
    """分頁上方的藍色提示條：寫出從上一步帶入了什麼，旁邊「改選…」可以換掉（#76）；沒有帶入時不顯示。

    建立後由呼叫端 grid 到分頁最上方，再呼叫 hide()。
    """

    def __init__(self, parent: tk.Misc, change: Callable[[], None]):
        super().__init__(parent, style="Hint.TFrame", padding=(14, 8))
        self.text = tk.StringVar()
        label = WrapLabel(self, textvariable=self.text, style="Hint.TLabel", wraplength=900)
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
    最後一欄照舊跟著視窗變寬，但不縮到比內容窄（#179）。表上沒有列時，表身中間淡淡寫「沒有資料」。
    """

    PAD = 24
    EMPTY = "沒有資料"
    MAX_WIDTH = 420
    SAMPLES = 8  # 每欄只量估起來最長的幾格，表很長時不用每格都量
    SIDEWAYS = 60  # Shift＋滾輪轉一格左右捲幾像素；Treeview 自己只捲 1 像素

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._fit_job: str | None = None
        self._fonts: tuple[tkfont.Font, tkfont.Font] | None = None
        self.placeholder = ttk.Label(self, text=self.EMPTY, style="Placeholder.TLabel")
        self.bind("<Shift-MouseWheel>", self._scroll_sideways)
        self._schedule_fit()  # 表頭設好、還沒有列時也照表頭寬度

    def heading(self, column: str, option: str | None = None, **kw: object):
        """表頭的字預設靠左，和欄裡的字對齊（macOS 的表格）。"""
        if "text" in kw:
            kw.setdefault("anchor", "w")
        return super().heading(column, option, **kw)

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

    def _scroll_sideways(self, event: tk.Event) -> str:
        """Shift＋滾輪左右捲；方向和 Windows 一樣，往下轉是往右捲。"""
        self.xview_scroll((-1 if event.delta > 0 else 1) * self.SIDEWAYS, "units")
        return "break"

    def destroy(self) -> None:
        if self._fit_job is not None:
            self.after_cancel(self._fit_job)
            self._fit_job = None
        super().destroy()

    def fit_columns(self) -> None:
        """依現在表上的列重算每一欄的寬度；沒有列時顯示「沒有資料」。"""
        self._fit_job = None
        if self.get_children():
            self.placeholder.place_forget()
        else:
            self.placeholder.place(relx=0.5, rely=0.5, anchor="center")
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


class Scrollbar(ttk.Scrollbar):
    """捲軸：內容全部看得到、不用捲時把手藏起來（像 macOS），位置照留著，版面不跳動。"""

    def set(self, first: float | str, last: float | str) -> None:
        self.state(["disabled"] if float(first) <= 0 and float(last) >= 1 else ["!disabled"])
        super().set(first, last)


class CopyButton(ttk.Button):
    """結果表右上方的複製按鈕（#121）：預設是「全部複製」，一鍵把整張表（含表頭）放進剪貼簿；按完短暫顯示複製了幾列。

    其他複製（例：進度頁的「複製待確認 TDCC」，#289）給自己的字：`copied` 用複製了幾筆填，`empty` 是什麼都沒複製時。
    """

    TEXT = "全部複製"

    def __init__(
        self,
        parent: tk.Misc,
        copy: Callable[[], int],
        text: str = TEXT,
        *,
        copied: str = "已複製 {} 列",
        empty: str = "表上沒有資料",
        width: int = 12,
    ):
        super().__init__(parent, text=text, command=self.copy, width=width, style="Toolbar.TButton")
        self.action = copy
        self.text = text
        self.copied = copied
        self.empty = empty
        self._reset_job: str | None = None  # 換回原本的字的 after 排程

    def copy(self) -> None:
        count = self.action()
        self.configure(text=self.copied.format(count) if count else self.empty)
        if self._reset_job is not None:
            self.after_cancel(self._reset_job)
        self._reset_job = self.after(1500, self._reset)

    def _reset(self) -> None:
        self._reset_job = None
        self.configure(text=self.text)


class CopyArea(ttk.Frame):
    """結果表那一塊（#121）：第 0 列左邊寫怎麼複製、右邊「全部複製」；第 1 列是白色平面卡片 `card`，
    表與捲軸由呼叫端建在 `card` 裡面（表在第 0 列第 0 欄，直捲軸在它右邊、橫捲軸在它下面）。"""

    def __init__(self, parent: tk.Misc, hint: str, copy_all: Callable[[], int]):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.bar = ttk.Frame(self)
        self.bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(self.bar, text=hint, style="Caption.TLabel").pack(side="left")
        self.button = CopyButton(self.bar, copy_all)
        self.button.pack(side="right")
        self.card = ttk.Frame(self, style="TableCard.TFrame", padding=1)  # 平面、直角的白卡片（#264）
        self.card.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.card.columnconfigure(0, weight=1)
        self.card.rowconfigure(0, weight=1)

    def add_button(self, text: str, copy: Callable[[], int], *, copied: str, empty: str, width: int = 12) -> CopyButton:
        """在「全部複製」左邊多放一個複製按鈕（#289）；參數同 `CopyButton`。"""
        button = CopyButton(self.bar, copy, text, copied=copied, empty=empty, width=width)
        button.pack(side="right", padx=(0, 8))
        return button


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
