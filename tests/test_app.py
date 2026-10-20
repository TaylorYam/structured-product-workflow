"""視窗只做冒煙測試：建得起來、帶入與記住資料夾。沒有顯示器（CI）時略過。"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path

import pytest

from sp_workflow import app
from sp_workflow.local_settings import FolderMemory


@pytest.fixture
def master():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("沒有顯示器")
    root.withdraw()
    yield root
    root.destroy()


def test_window_builds_with_remembered_folders(master, tmp_path: Path) -> None:
    memory = FolderMemory.under(tmp_path)
    memory.remember(root="R:/FCN", out="D:/out", batch="D:/out/待核對_1")

    window = app.PanelWindow(master, memory)

    assert (window.prepare.root_var.get(), window.prepare.out_var.get()) == ("R:/FCN", "D:/out")
    assert str(window.prepare.open_button.cget("state")) == "disabled"
    assert window.returning.batch_var.get() == "D:/out/待核對_1"


def test_prepared_batch_is_filled_into_the_return_tab(master, tmp_path: Path) -> None:
    window = app.PanelWindow(master, FolderMemory.under(tmp_path))

    window.prepare.on_prepared(Path("D:/out/待核對_20261019-103000"))

    assert window.returning.batch_var.get() == str(Path("D:/out/待核對_20261019-103000"))


def test_install_root_decides_where_folders_are_remembered(tmp_path: Path) -> None:
    assert app.folder_memory(["--install-root", str(tmp_path)]).path == tmp_path / ".local" / "prepare_folders.json"
    assert app.folder_memory([]).path.name == "prepare_folders.json"


def test_daily_import_tab_remembers_files_and_env_path(master, tmp_path: Path) -> None:
    memory = FolderMemory.under(tmp_path)
    memory.remember(daily="D:/每日更新.xlsx")

    window = app.PanelWindow(master, memory, tmp_path / ".env")

    assert window.daily.daily_var.get() == "D:/每日更新.xlsx"
    assert window.daily.db.env_path == tmp_path / ".env" and window.mail.db is window.daily.db


def test_mail_draft_tab_reuses_the_root_and_remembers_its_output(master, tmp_path: Path) -> None:
    memory = FolderMemory.under(tmp_path)
    memory.remember(root="R:/FCN", mail_out="D:/寄信")

    window = app.PanelWindow(master, memory, tmp_path / ".env")

    assert (window.mail.root_var.get(), window.mail.out_var.get()) == ("R:/FCN", "D:/寄信")


def test_order_terms_tab_reuses_the_root_and_remembers_its_output(master, tmp_path: Path) -> None:
    memory = FolderMemory.under(tmp_path)
    memory.remember(root="R:/FCN", order_out="D:/Order")

    window = app.PanelWindow(master, memory, tmp_path / ".env")

    assert (window.orders.root_var.get(), window.orders.out_var.get()) == ("R:/FCN", "D:/Order")
    assert str(window.orders.open_button.cget("state")) == "disabled"
    assert str(window.orders.export_button.cget("state")) == "disabled"  # 讀取完、有問題才可按
