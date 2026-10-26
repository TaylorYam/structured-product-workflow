"""視窗樣式的圖片元件（#253）：九宮格要拉伸的方向，中間那段都先平鋪放大過，不然大的元件一次要貼上萬塊、畫得很慢（#262）；
表格那一塊不用圖片元件（#264）。"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from fcn_checker.ui import _create_elements, _Shapes
from window_synth import new_window


class _Recorder:
    """只記 `element_create` 的假 ttk.Style。"""

    def __init__(self) -> None:
        self.elements: dict[str, tuple[list[tk.PhotoImage], tuple[int, int, int, int]]] = {}

    def element_create(self, name, kind, default, *states, border, **options) -> None:
        left, top, right, bottom = (border,) * 4 if isinstance(border, int) else border
        self.elements[name] = ([default, *(image for _, image in states)], (left, top, right, bottom))


def test_every_stretched_image_has_a_wide_middle(master) -> None:
    recorder = _Recorder()
    _create_elements(recorder, _Shapes(master, 1.0))

    assert "Mac.card" in recorder.elements
    for name, (images, (left, top, right, bottom)) in recorder.elements.items():
        for image in images:
            if left or right:  # 左右會拉伸
                assert image.width() - left - right >= _Shapes.STRETCH, name
            if top or bottom:  # 上下會拉伸
                assert image.height() - top - bottom >= _Shapes.STRETCH, name


def _element_names(layout) -> list[str]:
    names = []
    for name, options in layout:
        names += [str(name), *_element_names(options.get("children", []))]
    return names


def test_the_table_is_drawn_flat(master, tmp_path: Path) -> None:
    """表格那一塊（表頭、外框、捲軸）每捲一次都重畫，用平面樣式、不用圖片元件，捲動才順（#264）。"""
    new_window(master, tmp_path)  # 套上視窗的樣式
    style = ttk.Style(master)

    for name in ("Heading", "TableCard.TFrame", "Vertical.TScrollbar", "Horizontal.TScrollbar"):
        assert not [e for e in _element_names(style.layout(name)) if e.startswith("Mac.")], name
