"""視窗測試共用的組視窗方式與虛構結果（#138 從 test_app.py 搬出）：和 main 一樣組視窗，資料全部虛構。"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from tkinter import messagebox, ttk

from fcn_checker.panel_workflow import PanelSession
from fcn_checker.ui import Background
from order_synth import CONFIG
from sp_workflow import app
from sp_workflow.app import PanelWindow
from sp_workflow.check_terms import TO_CONFIRM, CalcLine, Difference
from sp_workflow.documents import DocKind
from sp_workflow.local_settings import FolderMemory
from sp_workflow.order_terms import OrderRow
from sp_workflow.preparation import PREPARED, SKIPPED, PreparedDocument, PrepareReceipt, ProductOutcome
from sp_workflow.tabs import recalc
from sp_workflow.tabs.base import DatabaseLogin
from sp_workflow.window_actions import PrepareResult, ReadResult, _drafts


def texts(widget: tk.Misc, kind: type[tk.Misc]) -> list[str]:
    """畫面上某一種元件（按鈕、標籤…）的文字，包含所有子元件。"""
    found = [widget.cget("text")] if isinstance(widget, kind) else []
    for child in widget.winfo_children():
        found += texts(child, kind)
    return found


def new_window(
    master,
    tmp_path: Path,
    session: PanelSession | None = None,
    *,
    start_on_check: bool = False,
    background=Background,
    logged_in: bool = False,
) -> PanelWindow:
    """和 main 一樣組視窗（#129），根目錄是 tmp_path；給了 session 時改用它（沒有資料庫來源）。

    `background`：分頁的背景執行器，測試傳 `immediate` fixture（#206）。`logged_in`：寫一份虛構帳號、有密碼的
    `.env`，登入不問密碼；分頁的動作都換成假的，不會真的連線。
    """
    if logged_in:
        (tmp_path / ".env").write_text("PGDATABASE=spw_fake\nPGUSER=operator1\nPGPASSWORD=fake\n", encoding="utf-8")
    args = app.parse_args(["--install-root", str(tmp_path)])
    if session is None:
        return app.build_window(master, args, start_on_check=start_on_check, background=background)
    db = DatabaseLogin(master, tmp_path / ".env")
    memory = FolderMemory.under(tmp_path)
    return app.PanelWindow(master, memory, db, session, start_on_check=start_on_check, background=background)


class HeldBackground:
    """測試用、介面和 `Background` 相同（#206）：動作先留著不做，好看處理中的畫面。

    `held` 依開始的順序列出 `(action, deliver)`：`deliver(outcome)` 把那一件當成做完、交回 outcome（例外就當成它丟出）；
    要真的做就 `deliver(action())`。
    """

    def __init__(self, widget: tk.Misc) -> None:
        self.widget = widget
        self.closed = False
        self.held: list[tuple[Callable[[], object], Callable[[object], None]]] = []

    def run(self, action: Callable[[], object], done: Callable[[Future], None] | None = None) -> Future:
        def deliver(outcome: object) -> None:
            future: Future = Future()
            if isinstance(outcome, BaseException):
                future.set_exception(outcome)
            else:
                future.set_result(outcome)
            if done is not None:
                done(future)

        self.held.append((action, deliver))
        return Future()

    def finish(self, outcome: object) -> None:
        """最早留著的那一件當成做完、交回 outcome。"""
        _, deliver = self.held.pop(0)
        deliver(outcome)

    def run_all(self) -> None:
        """依序真的做完留著的每一件（做完時又開始的也算）。"""
        while self.held:
            action, deliver = self.held.pop(0)
            deliver(action())

    def close(self) -> None:
        self.closed = True


def started(tab) -> list[str]:
    """分頁開始了、還沒做完的背景動作（`HeldBackground`；一次只跑一件）：它處理中的說明。"""
    return [tab.status_var.get()] if tab.background.held else []


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
    return ReadResult(rows, _drafts(rows, CONFIG), CONFIG)


def type_into(editor, name: str, text: str) -> None:
    """打字後離開那一欄。"""
    editor.rows[name].var.set(text)
    editor._commit([name])


UF_DIFF = Difference("UF", "1.99", "0.0215")


NEEDS_RELEASE = CalcLine(
    "029199990001", TO_CONFIRM, "UF不一致：起單資料 1.99、交易明細 0.0215（換算 2.15）", True, (UF_DIFF,)
)


def release_without_database(monkeypatch, result) -> list[str]:
    """放行時不連資料庫：問的話回答「是」，放行直接回 result（視窗要用 `immediate`、`logged_in` 建）。回傳問過的內容。"""
    asked = []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or True)
    monkeypatch.setattr(recalc, "release_line", lambda *a: result)
    return asked
