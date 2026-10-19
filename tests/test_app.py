"""視窗只做冒煙測試：建得起來、帶入記住的資料夾。沒有顯示器（CI）時略過。"""

from __future__ import annotations

import tkinter as tk

import pytest

from sp_workflow import app


def test_window_builds_with_remembered_folders(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        master = tk.Tk()
    except tk.TclError:
        pytest.skip("沒有顯示器")
    monkeypatch.setattr(app, "load_folders", lambda: {"root": "R:/FCN", "out": "D:/out"})
    try:
        master.withdraw()
        window = app.PrepareWindow(master)
        assert (window.root_var.get(), window.out_var.get()) == ("R:/FCN", "D:/out")
        assert str(window.open_button.cget("state")) == "disabled"
    finally:
        master.destroy()
