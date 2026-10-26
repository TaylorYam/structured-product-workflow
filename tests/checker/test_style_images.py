"""視窗樣式的圖片元件（#253）：九宮格要拉伸的方向，中間那段都先平鋪放大過，不然大的元件一次要貼上萬塊、畫得很慢（#262）。"""

from __future__ import annotations

import tkinter as tk

from fcn_checker.ui import _create_elements, _Shapes


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

    assert "Mac.heading" in recorder.elements
    for name, (images, (left, top, right, bottom)) in recorder.elements.items():
        for image in images:
            if left or right:  # 左右會拉伸
                assert image.width() - left - right >= _Shapes.STRETCH, name
            if top or bottom:  # 上下會拉伸
                assert image.height() - top - bottom >= _Shapes.STRETCH, name
