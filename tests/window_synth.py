"""視窗測試共用的組視窗方式與虛構結果（#138 從 test_app.py 搬出）：和 main 一樣組視窗，資料全部虛構。"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from fcn_checker.panel_workflow import PanelSession
from order_synth import CONFIG
from sp_workflow import app
from sp_workflow.app import PanelWindow
from sp_workflow.check_terms import TO_CONFIRM, CalcLine, Difference
from sp_workflow.documents import DocKind
from sp_workflow.local_settings import FolderMemory
from sp_workflow.order_terms import OrderRow
from sp_workflow.preparation import PREPARED, SKIPPED, PreparedDocument, PrepareReceipt, ProductOutcome
from sp_workflow.product_terms import drafts_from
from sp_workflow.tabs.base import DatabaseLogin
from sp_workflow.window_actions import PrepareResult, ReadResult


def texts(widget: tk.Misc, kind: type[tk.Misc]) -> list[str]:
    """畫面上某一種元件（按鈕、標籤…）的文字，包含所有子元件。"""
    found = [widget.cget("text")] if isinstance(widget, kind) else []
    for child in widget.winfo_children():
        found += texts(child, kind)
    return found


def new_window(
    master, tmp_path: Path, session: PanelSession | None = None, *, start_on_check: bool = False
) -> PanelWindow:
    """和 main 一樣組視窗（#129），根目錄是 tmp_path；給了 session 時改用它（沒有資料庫來源）。"""
    args = app.parse_args(["--install-root", str(tmp_path)])
    if session is None:
        return app.build_window(master, args, start_on_check=start_on_check)
    db = DatabaseLogin(master, tmp_path / ".env")
    return app.PanelWindow(master, FolderMemory.under(tmp_path), db, session, start_on_check=start_on_check)


def hint_change_button(hint) -> ttk.Button:
    (button,) = [w for w in hint.winfo_children() if isinstance(w, ttk.Button)]
    return button


def prepared_receipt(batch: Path) -> PrepareReceipt:
    """一檔已準備（TS＋IIS）、一檔跳過的準備結果。"""
    ready = ProductOutcome(
        "147000000001",
        PREPARED,
        documents=(
            PreparedDocument(DocKind.TS, output_name="147000000001_TS.pdf"),
            PreparedDocument(DocKind.IIS, output_name="147000000001_IIS.pdf"),
        ),
    )
    skipped = ProductOutcome("147000000002", SKIPPED, "找不到說明書")
    return PrepareReceipt(batch, batch / "準備清單.xlsx", (ready, skipped))


def prepared_result(batch: Path) -> PrepareResult:
    """`prepared_receipt`，步驟紀錄已寫進資料庫。"""
    return PrepareResult(prepared_receipt(batch))


def read_result(*rows: OrderRow) -> ReadResult:
    """讀完起單建檔的結果（沒有連資料庫）。"""
    return ReadResult(rows, drafts_from(rows), CONFIG)


def type_into(editor, name: str, text: str) -> None:
    """打字後離開那一欄。"""
    editor.rows[name].var.set(text)
    editor._commit([name])


UF_DIFF = Difference("UF", "1.99", "0.0215")


NEEDS_RELEASE = CalcLine(
    "029199990001", TO_CONFIRM, "UF不一致：起單資料 1.99、交易明細 0.0215（換算 2.15）", True, (UF_DIFF,)
)


def release_without_database(tab, monkeypatch, result) -> list[str]:
    """放行時不連資料庫：問的話回答「是」，背景工作直接回 result。回傳問過的內容。"""
    asked = []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or True)
    monkeypatch.setattr(tab.db, "settings", lambda **kwargs: object())
    monkeypatch.setattr(tab, "run_in_background", lambda action, busy, on_done, **k: on_done(result))
    return asked
