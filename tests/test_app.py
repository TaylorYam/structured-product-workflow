"""視窗只做冒煙測試：建得起來、帶入記住的資料夾。沒有顯示器（CI）時略過。"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path

import pytest

from sp_workflow import app


@pytest.fixture
def master():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("沒有顯示器")
    root.withdraw()
    yield root
    root.destroy()


def test_window_builds_with_remembered_folders(master, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "load_folders", lambda: {"root": "R:/FCN", "out": "D:/out", "batch": "D:/out/待核對_1"})

    window = app.PanelWindow(master)

    assert (window.prepare.root_var.get(), window.prepare.out_var.get()) == ("R:/FCN", "D:/out")
    assert str(window.prepare.open_button.cget("state")) == "disabled"
    assert window.returning.batch_var.get() == "D:/out/待核對_1"


def test_prepared_batch_is_filled_into_the_return_tab(master, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "load_folders", lambda: {})
    window = app.PanelWindow(master)

    window.prepare.on_prepared(Path("D:/out/待核對_20261019-103000"))

    assert window.returning.batch_var.get() == str(Path("D:/out/待核對_20261019-103000"))
