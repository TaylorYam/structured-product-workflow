"""視窗樣式是平面的（#266）：不用圖片元件——圖片要半透明混色，重畫慢，捲表格會卡、從工具列打開視窗時會先黑一下
（#262、#264）。只有勾選框用一張大小固定的小圖：clam 的勾選框勾起來畫 X（#268）。"""

from __future__ import annotations

from pathlib import Path
from tkinter import ttk

from fcn_checker.ui import PARENT_FILLED
from window_synth import new_window

STYLES = (
    "TButton",
    "Primary.TButton",
    "CurrentStep.TButton",
    *PARENT_FILLED,
    "TEntry",
    "Path.TEntry",
    "TCombobox",
    "Card.TCheckbutton",
    "Card.TRadiobutton",
    "Card.TFrame",
    "Field.TFrame",
    "Hint.TFrame",
    "TableCard.TFrame",
    "TLabelframe",
    "Done.TLabel",
    "Heading",
    "Vertical.TScrollbar",
    "Horizontal.TScrollbar",
)


def _element_names(layout) -> list[str]:
    names = []
    for name, options in layout:
        names += [str(name), *_element_names(options.get("children", []))]
    return names


def test_the_window_is_drawn_flat(master, tmp_path: Path) -> None:
    new_window(master, tmp_path)  # 套上視窗的樣式
    style = ttk.Style(master)

    for name in STYLES:
        assert not [e for e in _element_names(style.layout(name)) if e.startswith("Mac.")], name
    assert not [e for e in style.element_names() if e.startswith("Mac.")], "不建圖片元件"


def test_focus_turns_the_frame_blue(master, tmp_path: Path) -> None:
    """平面的元件沒有圓角圖的藍色外圈：用鍵盤移到按鈕、輸入框、下拉選單時外框變藍，看得出來在哪裡。"""
    new_window(master, tmp_path)
    style = ttk.Style(master)

    for name in ("TButton", "TEntry", "TCombobox", "Field.TFrame"):
        assert style.lookup(name, "bordercolor", ["focus"]) != style.lookup(name, "bordercolor"), name


def test_a_ticked_checkbox_shows_a_check_mark_not_an_x(master, tmp_path: Path) -> None:
    """clam 的勾選框勾起來畫 X，看起來像「不要」；改用畫勾勾的小圖（#268），勾與沒勾用不同的圖。"""
    new_window(master, tmp_path)
    style = ttk.Style(master)

    for name in ("TCheckbutton", "Card.TCheckbutton"):
        names = _element_names(style.layout(name))
        assert "Check.indicator" in names and "Checkbutton.indicator" not in names, name
