"""「7 人工維護」分頁（`sp_workflow.tabs.manual`）：檢查與補齊商品條件（#115、ADR 0017）、交易明細（#116）、檢查結果表複製（#121）、匯出商品總表（#291）。"""

from __future__ import annotations

import datetime as dt
import tkinter as tk
from decimal import Decimal
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import pytest

import sp_workflow.tabs.manual
from fcn_checker.ui import SURFACE
from order_synth import CONFIG, VALUES, maintenance_draft, maintenance_trades
from sp_workflow.app import PanelWindow
from sp_workflow.check_terms import DONE, TO_CONFIRM, CalcLine
from sp_workflow.manual_check import ManualCheck, ManualCheckReceipt
from sp_workflow.product_export import ExportReceipt
from sp_workflow.product_record import FIELDS, LISTED, REQUIRED
from sp_workflow.product_terms import _MISSING_NOTE, Draft, MissingProduct, Receipt, TermsError, draft_from
from sp_workflow.status import OrderReview
from sp_workflow.tabs.editor import DIFFERENT_BG, PROBLEM_BG
from sp_workflow.tabs.manual import ManualCheckForm, correction_text
from sp_workflow.trade_rows import CANCEL_NOTE, UNMATCHED_NOTE, TradeRowsDraft
from sp_workflow.window_actions import Checked, ManualCheckResult, Opened
from window_synth import HeldBackground, new_window, started, type_into

A_TDCC, B_TDCC, NEW_TDCC = "029199990001", "029199990002", "029199990003"


def new_product_draft(tdcc: str = NEW_TDCC) -> Draft:
    """只有交易明細的 TDCC：從空白開始，商品種類是交易明細的 FCN（人工維護打開資料庫沒有的一檔，#302）。"""
    return draft_from(tdcc, None, CONFIG, trade_product="FCN")


def missing(tdcc: str, status: str = OrderReview.UNLOCKED, **changes) -> MissingProduct:
    """「檢查」列出的一檔：只有交易明細的全部空白。"""
    values = {**VALUES, **changes} if status == OrderReview.UNLOCKED else dict.fromkeys(FIELDS)
    return MissingProduct(tdcc, "BARC", status, values, tuple(n for n in REQUIRED if values.get(n) is None))


def maintenance_window(master, tmp_path: Path) -> PanelWindow:
    """背景動作先留著不做（`HeldBackground`）、登入不問密碼的視窗：看處理中的畫面，`finish` 交回結果。"""
    return new_window(master, tmp_path, background=HeldBackground, logged_in=True)


def checked_window(master, tmp_path: Path, *products: MissingProduct) -> PanelWindow:
    window = maintenance_window(master, tmp_path)
    window.manual._checked(Checked(products, CONFIG))
    return window


def opened(window: PanelWindow, draft: Draft):
    """打開了 draft：結果交給 `_opened`，再和 `ActionRunner` 做完時一樣恢復按鈕。"""
    window.manual._opened(Opened(draft, CONFIG))
    window.manual._busy(False)
    return window.manual.editor


def test_check_lists_products_with_red_cells_where_data_is_missing(master, tmp_path: Path) -> None:
    window = checked_window(
        master,
        tmp_path,
        missing(B_TDCC, **{"Strike (%)": None, "Maturity date": None}),
        missing(NEW_TDCC, OrderReview.TRADES_ONLY),
    )
    cells = window.manual.cells

    assert cells.keys == [B_TDCC, NEW_TDCC]
    assert cells.background(B_TDCC, "Maturity date") == PROBLEM_BG
    assert cells.background(B_TDCC, "Coupon p.a. (%)") == SURFACE
    assert (cells.text(B_TDCC, "Coupon p.a. (%)"), cells.text(B_TDCC, "Maturity date")) == ("12.5", "")
    assert cells.text(NEW_TDCC, "status") == OrderReview.TRADES_ONLY and cells.text(NEW_TDCC, "tdcc") == NEW_TDCC
    assert all(cells.background(NEW_TDCC, name) == PROBLEM_BG for name in REQUIRED)
    assert window.manual.kpis.values == (("缺資料（檔）", 2, True), ("只有交易明細（檔）", 1, False))
    assert window.manual.editor.draft is None, "檢查完不自動打開"


def test_nothing_missing_says_so(master, tmp_path: Path) -> None:
    window = checked_window(master, tmp_path)

    assert window.manual.cells.keys == []
    assert window.manual.status_var.get() == "檢查完了：沒有還沒鎖住、缺資料的商品。"


def test_the_tdcc_column_stays_left_while_the_fields_scroll(master, tmp_path: Path) -> None:
    cells = checked_window(master, tmp_path, *(missing(f"0291999900{n:02}") for n in range(1, 40))).manual.cells

    cells._xview("moveto", 0.5)
    cells._yview("moveto", 0.5)

    assert cells.body.xview()[0] > 0 and cells.header.xview() == cells.body.xview(), "表頭跟著左右捲"
    assert cells.side.xview()[0] == 0 and cells.corner.xview()[0] == 0, "TDCC 那一欄不動"
    assert cells.body.yview()[0] > 0 and cells.side.yview() == cells.body.yview(), "上下捲時兩邊一起動"
    assert cells.header.yview()[0] == 0


def test_clicking_a_row_opens_that_product(master, tmp_path: Path, monkeypatch) -> None:
    window = checked_window(
        master, tmp_path, missing(B_TDCC, **{"Maturity date": None}), missing(NEW_TDCC, OrderReview.TRADES_ONLY)
    )
    window.manual.cells._choose(NEW_TDCC)

    assert started(window.manual) == [f"讀取 {NEW_TDCC} 的條件中…"]
    assert window.manual.cells.selected == NEW_TDCC
    assert str(window.manual.check_button.cget("state")) == "disabled", "讀取中不能再按"


def test_any_tdcc_can_be_typed_in_to_open(master, tmp_path: Path, monkeypatch) -> None:
    window = maintenance_window(master, tmp_path)
    errors: list[str] = []
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: errors.append(a[1]))

    window.manual.tdcc_var.set("0291 x")
    window.manual.open_typed()
    window.manual.tdcc_var.set(f" {A_TDCC} ")
    window.manual.open_typed()

    assert errors == ["請輸入一筆 TDCC。"]
    assert started(window.manual) == [f"讀取 {A_TDCC} 的條件中…"]


def test_an_unlocked_product_marks_blank_fields_red_but_can_be_saved(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    editor = opened(window, maintenance_draft(**{"Maturity date": None}))

    maturity = editor.rows["Maturity date"]
    assert maturity.label.cget("background") == PROBLEM_BG and maturity.note.cget("text") == _MISSING_NOTE
    assert window.manual.intro_var.get().startswith("還沒鎖住")
    assert str(window.manual.save_button.cget("state")) == "normal"
    assert str(editor.rows["Issue date"].input.cget("state")) == "normal", "還沒鎖住的 Issue date 同起單資料可以改"
    type_into(editor, "Coupon p.a. (%)", "11")
    coupon = editor.rows["Coupon p.a. (%)"]
    assert coupon.label.cget("background") == DIFFERENT_BG and coupon.note.cget("text") == "資料庫是 12.5"


def test_a_locked_product_follows_the_correction_rules(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    editor = opened(window, maintenance_draft(OrderReview.LOCKED))
    type_into(editor, "BBG Code 3", "CCC UN")
    type_into(editor, "Maturity date", "")

    assert window.manual.intro_var.get().startswith("已核對通過、條件已鎖住")
    assert str(editor.rows["Issue date"].input.cget("state")) == "disabled"
    assert editor.rows["Issue date"].note.cget("text") == "TS 回填的欄位，不能更正"
    assert editor.rows["BBG Code 3"].note.cget("text") == "資料庫是 空白", "補上也標黃"
    assert editor.rows["Maturity date"].note.cget("text") == "必填", "已鎖住的不能留空白"


def test_a_trade_only_product_starts_blank(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    editor = opened(window, new_product_draft())

    assert window.manual.intro_var.get().startswith("資料庫還沒有這檔的商品條件")
    assert editor.rows["Currency"].note.cget("text") == _MISSING_NOTE
    assert editor.rows["Product"].var.get() == "FCN"
    assert editor.title_var.get() == f"{NEW_TDCC}　BARC"


def test_saving_reports_the_changes_and_updates_the_check_table(master, tmp_path: Path, monkeypatch) -> None:
    window = checked_window(
        master,
        tmp_path,
        missing(A_TDCC, **{"Maturity date": None}),
        missing(B_TDCC, **{"Maturity date": None, "Coupon p.a. (%)": None}),
    )
    tab = window.manual
    editor = opened(window, maintenance_draft(**{"Maturity date": None}))
    type_into(editor, "Maturity date", "2027-04-22")
    tab.reason_var.set("起單建檔漏了")

    tab.save()
    assert started(tab) == [f"存檔中…（{A_TDCC}）"]
    assert str(tab.save_button.cget("state")) == "disabled", "存檔中不能改"
    calc = CalcLine(A_TDCC, DONE, "", True)
    tab.background.finish(Receipt(A_TDCC, 7, (("Maturity date", None, dt.date(2027, 4, 22)),), calc, locked=False))

    assert tab.status_var.get() == (
        f"已存檔 {A_TDCC} 的商品條件（第 7 筆紀錄）：Maturity date 空白 → 2027-04-22。\n核對條件重算後：完成。"
    )
    assert tab.cells.keys == [B_TDCC], "補齊的不再列出"
    assert tab.reason_var.get() == "" and str(tab.save_button.cget("state")) == "normal"

    editor = opened(window, maintenance_draft(tdcc=B_TDCC, **{"Maturity date": None, "Coupon p.a. (%)": None}))
    type_into(editor, "Maturity date", "2027-04-22")
    tab._saved(Receipt(B_TDCC, 8, (), calc, locked=False, missing=("Coupon p.a. (%)",)))

    assert tab.cells.keys == [B_TDCC], "還缺的照樣列出"
    assert tab.cells.background(B_TDCC, "Maturity date") != PROBLEM_BG
    assert tab.cells.background(B_TDCC, "Coupon p.a. (%)") == PROBLEM_BG


def test_save_messages_for_new_and_locked_products() -> None:
    created = Receipt(
        NEW_TDCC,
        8,
        (("Coupon p.a. (%)", None, Decimal(11)),),
        CalcLine(NEW_TDCC, TO_CONFIRM, "缺 Currency", True),
        locked=False,
        created=True,
        missing=("Currency",),
    )
    corrected = Receipt(
        A_TDCC,
        3,
        (("Coupon p.a. (%)", Decimal("12.5"), Decimal(11)), ("UL_2_下限價", Decimal("32.5"), None)),
        CalcLine(A_TDCC, TO_CONFIRM, "UF：起單資料 2、交易明細 0.0215", True),
    )

    assert correction_text(created) == (
        f"已新增 {NEW_TDCC} 的商品條件（第 8 筆紀錄）：Coupon p.a. (%) 空白 → 11。\n核對條件重算後：待確認（缺 Currency）。"
    )
    assert correction_text(corrected) == (
        f"已更正 {A_TDCC}（第 3 次更正）：Coupon p.a. (%) 12.5 → 11、UL_2_下限價 32.5 → 空白。"
        "商品維持鎖住，核對結果沒有變。\n核對條件重算後：待確認（UF：起單資料 2、交易明細 0.0215）。"
    )
    assert correction_text(Receipt(A_TDCC, None, locked=False)) == (f"{A_TDCC} 沒有改任何欄位，沒有寫入。")
    exited = Receipt(A_TDCC, 4, (("Coupon p.a. (%)", Decimal("12.5"), Decimal(11)),), exited="KO 2026-11-18")
    assert correction_text(exited).endswith(
        "\n注意：這檔已出場（KO 2026-11-18），不再比價，存續事件不會跟著改；結果和上手通知不同時，到「8 進度」提出異議。"
    )


def test_a_rejected_save_keeps_the_edits(master, tmp_path: Path, monkeypatch) -> None:
    window = maintenance_window(master, tmp_path)
    tab = window.manual
    errors = []
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: errors.append(a[1]))
    editor = opened(window, maintenance_draft())
    type_into(editor, "Coupon p.a. (%)", "11")
    tab.save()

    tab.background.finish(TermsError(f"{A_TDCC}：資料庫裡這檔的條件在打開之後改過了，請再按一次「打開」重讀這一檔"))

    assert errors == [f"{A_TDCC}：資料庫裡這檔的條件在打開之後改過了，請再按一次「打開」重讀這一檔"]

    assert editor.draft.values["Coupon p.a. (%)"] == Decimal(11) and tab.has_unsaved()
    assert str(tab.save_button.cget("state")) == "normal"


def test_switching_products_with_unsaved_edits_asks_first(master, tmp_path: Path, monkeypatch) -> None:
    window = checked_window(
        master, tmp_path, missing(A_TDCC, **{"Maturity date": None}), missing(B_TDCC, **{"Maturity date": None})
    )
    tab = window.manual
    editor = opened(window, maintenance_draft(**{"Maturity date": None}))
    type_into(editor, "Coupon p.a. (%)", "11")
    answers = [False, True]
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: answers.pop(0))

    tab.cells._choose(B_TDCC)
    assert started(tab) == [] and tab.cells.selected == A_TDCC, "不換檔，選取回到原本那一檔"

    tab.cells._choose(B_TDCC)
    assert started(tab) == [f"讀取 {B_TDCC} 的條件中…"]


def test_open_again_rereads_a_product_opened_from_the_check_table(master, tmp_path: Path, monkeypatch) -> None:
    """「重新載入」已拿掉（#558）：從左邊檢查結果點開的，TDCC 欄也填上這一檔；存檔被擋後按「打開」就重讀它，
    有改過、還沒存的照舊先問。"""
    window = checked_window(master, tmp_path, missing(A_TDCC, **{"Maturity date": None}))
    tab = window.manual
    tab.tdcc_var.set(B_TDCC)  # 上次打的是別檔
    tab.cells._choose(A_TDCC)
    tab.background.finish(Opened(maintenance_draft(**{"Maturity date": None}), CONFIG))
    assert tab.tdcc_var.get() == A_TDCC
    type_into(tab.editor, "Coupon p.a. (%)", "11")
    tab.save()
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: None)
    tab.background.finish(TermsError(f"{A_TDCC}：資料庫裡這檔的條件在打開之後改過了，請再按一次「打開」重讀這一檔"))
    asked = []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or True)

    tab.open_typed()

    assert len(asked) == 1 and "人工維護頁有改過、還沒存檔" in asked[0]
    assert started(tab)[-1] == f"讀取 {A_TDCC} 的條件中…"


def test_closing_with_unsaved_maintenance_asks_and_stays_on_the_tab(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    type_into(opened(window, maintenance_draft()), "Tenor (m)", "12")
    asked = []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or False)

    window.close()

    assert len(asked) == 1 and "人工維護頁有改過、還沒存檔" in asked[0]
    assert master.winfo_exists() and window.notebook.select() == str(window.manual.frame)


# ---------------------------------------------------------------- 人工維護：交易明細（#116）


def opened_trades(*rows: dict) -> TradeRowsDraft:
    """打開時的交易明細（畫面還沒改）：每列是對 `order_synth.TRADE` 的修改。"""
    trades = maintenance_trades(*rows)
    for row in trades.rows:
        row.stored = dict(row.values)
    return trades


def opened_with(window: PanelWindow, draft: Draft, trades: TradeRowsDraft):
    window.manual._opened(Opened(draft, CONFIG, trades))
    window.manual._busy(False)
    return window.manual.trade_editor


def type_trade(editor, key: tuple[int, str], text: str) -> None:
    editor.rows[key].var.set(text)
    editor.commit()


def grid_row(widget: tk.Misc) -> int:
    return int(widget.grid_info()["row"])


def test_trade_rows_are_listed_under_the_terms(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    editor = opened_with(window, maintenance_draft(), opened_trades({}, {"編號": None, "FC1": "Ben"}))

    assert sorted({seq for seq, _ in editor.rows}) == [1, 2]
    shown = [editor.rows[(1, name)].var.get() for name in ("交易日期", "金額", "UF")]
    assert shown == ["2026-10-15", "100000", "0.0215"]
    assert editor.rows[(2, "編號")].var.get() == ""
    last_term = window.manual.editor.rows[FIELDS[-1]].label
    assert grid_row(editor.rows[(1, "編號")].label) > grid_row(last_term), "在商品條件底下"
    fc = editor.rows[(2, "FC1")]
    assert fc.label.cget("background") == SURFACE, "沒改的名字照資料庫的，不比對信箱"
    type_trade(editor, (2, "FC1"), "Zed")
    assert fc.label.cget("background") == PROBLEM_BG and fc.note.cget("text").startswith(UNMATCHED_NOTE)


def test_editing_a_trade_cell_marks_it_and_counts_as_unsaved(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    editor = opened_with(window, maintenance_draft(), opened_trades({}))
    assert not window.manual.has_unsaved()

    type_trade(editor, (1, "金額"), "120,000")

    amount = editor.rows[(1, "金額")]
    assert amount.var.get() == "120000" and editor.trades.rows[0].values["金額"] == Decimal(120000)
    assert amount.label.cget("background") == DIFFERENT_BG and amount.note.cget("text") == "資料庫是 100000"
    assert window.manual.has_unsaved() and window.manual.status_var.get() == "029199990001 有改過、還沒存檔。"
    type_trade(editor, (1, "金額"), "100000")  # 自己改回資料庫的值（「用資料庫的值」已拿掉，#558）
    assert not window.manual.has_unsaved() and amount.label.cget("background") == SURFACE


def test_trade_problems_are_marked_red(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    editor = opened_with(window, maintenance_draft(), opened_trades({}))

    type_trade(editor, (1, "分公司"), "")
    type_trade(editor, (1, "UF"), "abc")
    type_trade(editor, (1, "備註"), "Cancel")

    assert editor.rows[(1, "分公司")].note.cget("text") == "必填"
    uf = editor.rows[(1, "UF")]
    assert uf.var.get() == "abc" and uf.note.cget("text") == "「abc」不是數字", "寫錯的保留打的字"
    assert editor.rows[(1, "備註")].note.cget("text") == CANCEL_NOTE
    assert window.manual.has_unsaved()


def test_a_product_without_trade_rows_says_so(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    editor = opened_with(window, maintenance_draft(), opened_trades())

    assert editor.rows == {} and "還沒有這檔的交易明細" in editor.empty.cget("text")


def test_opening_another_product_replaces_the_trade_rows(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    first = opened_with(window, maintenance_draft(), opened_trades({}, {}))
    old = first.rows[(2, "金額")].input

    editor = opened_with(window, maintenance_draft(tdcc=B_TDCC), opened_trades({"FC1": "Amy"}))
    master.update_idletasks()

    assert sorted(editor.rows) == [(1, name) for name in sorted(editor.trades.rows[0].values)]
    assert not old.winfo_exists(), "上一檔的欄拿掉"


def test_trade_rows_cannot_be_edited_while_saving(master, tmp_path: Path) -> None:
    window = maintenance_window(master, tmp_path)
    editor = opened_with(window, maintenance_draft(), opened_trades({}))
    type_trade(editor, (1, "募集"), "Y")

    window.manual.save()

    assert str(editor.rows[(1, "金額")].input.cget("state")) == "disabled"


def test_saving_sends_the_trade_rows_with_the_terms(master, tmp_path: Path, monkeypatch, immediate) -> None:
    window = new_window(master, tmp_path, background=immediate, logged_in=True)
    tab = window.manual
    saved = []

    def save(settings, draft, config, *, trades, reason):
        saved.append(trades)
        trades.reopen({row.seq: row.values for row in trades.rows})
        return Receipt(draft.tdcc, None, locked=False, trade_correction_id=4, trade_changes=((1, "募集", "N", "Y"),))

    monkeypatch.setattr(sp_workflow.tabs.manual, "save_product", save)
    editor = opened_with(window, maintenance_draft(), opened_trades({}))
    editor.rows[(1, "募集")].var.set("Y")  # 還沒離開欄位就按存檔

    tab.save()

    assert saved == [editor.trades] and saved[0].rows[0].values["募集"] == "Y"
    assert tab.status_var.get() == "已改 029199990001 的交易明細（第 4 筆紀錄）：第 1 列 募集 N → Y。"
    assert tab.status_label.cget("style") == "Done.TLabel", "只改交易明細也算存好了"
    assert not tab.has_unsaved() and editor.rows[(1, "募集")].label.cget("background") == SURFACE


def test_save_messages_for_trade_rows() -> None:
    both = Receipt(
        A_TDCC,
        5,
        (("Coupon p.a. (%)", Decimal("12.5"), Decimal(11)),),
        CalcLine(A_TDCC, DONE, "", True),
        locked=False,
        trade_correction_id=6,
        trade_changes=((2, "金額", Decimal("50000.0000"), Decimal(70000)), (2, "分公司", "台中", "台北")),
        unmatched=("Zed",),
    )
    locked = Receipt(
        A_TDCC,
        None,
        (("UL_1_進場價", Decimal("100.5"), Decimal("100.6")),),
        CalcLine(A_TDCC, DONE, "", True),
        trade_correction_id=7,
        trade_changes=((1, "UF", Decimal("0.0215"), None),),
    )

    assert correction_text(both) == (
        f"已存檔 {A_TDCC} 的商品條件（第 5 筆紀錄）：Coupon p.a. (%) 12.5 → 11。"
        f"已改 {A_TDCC} 的交易明細（第 6 筆紀錄）：第 2 列 金額 50000 → 70000、第 2 列 分公司 台中 → 台北。"
        "\n核對條件重算後：完成。"
        "\n注意：FC 名字對不到信箱：Zed（寄信草稿找不到收件人；請確認名字或在信箱表補上）。"
    )
    assert correction_text(locked) == (
        f"已改 {A_TDCC} 的交易明細（第 7 筆紀錄）：第 1 列 UF 0.0215 → 空白。重算後改變的價格：UL_1_進場價 100.5 → 100.6。"
        "商品維持鎖住，核對結果沒有變。\n核對條件重算後：完成。"
    )


# ---------------------------------------------------------------- 檢查結果表複製（#121）


def test_maintenance_check_table_copies_cells_rows_and_everything(master, tmp_path: Path) -> None:
    window = checked_window(
        master, tmp_path, missing(B_TDCC, **{"Maturity date": None}), missing(NEW_TDCC, OrderReview.TRADES_ONLY)
    )
    tab = window.manual
    cells = tab.cells
    cells.select(B_TDCC)

    cells.fill_menu(None, None)
    cells.menu.invoke(tk.END)  # 全部複製（含表頭）

    lines = master.clipboard_get().split("\n")
    assert lines[0].split("\t") == ["TDCC", "上手", "狀態", *LISTED]
    assert lines[1].split("\t")[:3] == [B_TDCC, "BARC", OrderReview.UNLOCKED]
    assert lines[1].split("\t")[3 + REQUIRED.index("Maturity date")] == ""
    assert len(lines) == 3
    assert [type(w) for w in tab.cell_area.bar.winfo_children()] == [ttk.Label], "沒有「全部複製」按鈕（#557）"

    cells.fill_menu(NEW_TDCC, "tdcc")
    assert cells.menu.labels() == [f"複製「{NEW_TDCC}」", "複製這一列", "全部複製（含表頭）"]
    cells.menu.invoke(0)
    assert master.clipboard_get() == NEW_TDCC
    cells.menu.invoke(1)
    assert master.clipboard_get().split("\t")[:3] == [NEW_TDCC, "BARC", OrderReview.TRADES_ONLY]
    cells.fill_menu(B_TDCC, "issuer")
    cells.menu.invoke(0)
    assert master.clipboard_get() == "BARC"
    assert cells.selected == B_TDCC, "右鍵複製不換選取的列"


def test_right_click_on_a_blank_cell_offers_the_row_and_the_table_only(master, tmp_path: Path) -> None:
    cells = checked_window(master, tmp_path, missing(B_TDCC, **{"Maturity date": None})).manual.cells

    cells.fill_menu(B_TDCC, "Maturity date")
    first = cells.menu.labels()
    cells.fill_menu(None, None)
    outside = cells.menu.labels()

    assert first == ["複製這一列", "全部複製（含表頭）"]
    assert outside == ["全部複製（含表頭）"]


class FakeClick:
    """右鍵點在 Canvas 上的位置。"""

    def __init__(self, widget: tk.Canvas, x: int, y: int) -> None:
        self.widget, self.x, self.y, self.x_root, self.y_root = widget, x, y, 0, 0


def test_right_click_finds_the_cell_under_the_pointer(master, tmp_path: Path, monkeypatch) -> None:
    cells = checked_window(
        master, tmp_path, missing(B_TDCC, **{"Maturity date": None}), missing(NEW_TDCC, OrderReview.TRADES_ONLY)
    ).manual.cells
    monkeypatch.setattr(cells.menu, "tk_popup", lambda *a: None)
    issuer_right = cells._edges[0][0]
    second_row = cells.row_height + 2

    cells._popup(FakeClick(cells.body, issuer_right + 2, second_row))
    status = cells.menu.labels()
    cells._popup(FakeClick(cells.side, 3, 2))
    tdcc = cells.menu.labels()
    cells._popup(FakeClick(cells.body, 3, cells.row_height * 5))
    below = cells.menu.labels()

    assert status[0] == f"複製「{OrderReview.TRADES_ONLY}」", "第二列的「狀態」欄"
    assert tdcc[0] == f"複製「{B_TDCC}」", "左邊固定的 TDCC 欄"
    assert below == ["全部複製（含表頭）"], "點在最後一列底下"


# ---------------------------------------------------------------- 匯出商品總表（#291；匯出商品條件 #122 已拿掉，#558）


def test_export_asks_where_to_save_before_connecting(master, tmp_path: Path, monkeypatch, immediate) -> None:
    tab = new_window(master, tmp_path, background=immediate).manual  # 沒有連線設定檔：登入的話會跳錯誤
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: pytest.fail("按取消時不連資料庫"))
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **k: "")

    tab.export_summary()


def test_cancelling_the_login_does_not_remember_the_folder(master, tmp_path: Path, monkeypatch, immediate) -> None:
    (tmp_path / ".env").write_text("PGDATABASE=spw_fake\nPGUSER=operator1\n", encoding="utf-8")  # 沒有密碼：要問
    tab = new_window(master, tmp_path, background=immediate).manual
    monkeypatch.setattr(simpledialog, "askstring", lambda *a, **k: None)  # 取消登入
    monkeypatch.setattr(sp_workflow.tabs.manual, "export_product_summary", lambda *a: pytest.fail("取消登入不匯出"))
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **k: str(tmp_path / "out" / "x.xlsx"))

    tab.export_summary()

    assert "product_export" not in tab.memory.load()


def test_summary_export_remembers_the_folder_and_runs_in_the_background(master, tmp_path: Path, monkeypatch) -> None:
    tab = maintenance_window(master, tmp_path).manual
    asked = []
    target = tmp_path / "out" / "summary.xlsx"
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **k: asked.append(k) or str(target))

    tab.export_summary()
    assert started(tab) == ["匯出商品總表中…"]
    assert str(tab.summary_button.cget("state")) == "disabled", "匯出中不能再按"
    tab.export_summary()
    assert len(asked) == 1
    tab.background.finish(ExportReceipt(target, 3))
    assert tab.status_var.get() == f"已匯出資料庫的商品總表3 檔：{target}"
    tab.export_summary()

    assert asked[0]["initialfile"].startswith("商品總表_") and asked[0]["initialfile"].endswith(".xlsx")
    assert asked[0]["initialdir"] is None and asked[1]["initialdir"] == str(target.parent), "下次從上次的位置開始"


def test_export_result_says_how_many_products_and_where(master, tmp_path: Path) -> None:
    tab = new_window(master, tmp_path).manual
    path = tmp_path / "summary.xlsx"

    tab._exported(ExportReceipt(path, 12), unsaved=True, what="商品總表")

    assert tab.status_var.get() == f"已匯出資料庫的商品總表12 檔：{path}\n注意：畫面上還沒存檔的修改不在匯出檔裡。"
    assert tab.status_label.cget("style") == "Done.TLabel"
    assert str(tab.summary_button.cget("state")) == "normal"


def test_maintenance_buttons_left_after_the_removals(master, tmp_path: Path) -> None:
    """#558：上方只剩「檢查」「匯出商品總表…」，編輯區下方只剩「存檔」「人工核對完成…」。"""
    tab = new_window(master, tmp_path).manual

    def texts(frame) -> list[str]:
        return [str(w.cget("text")) for w in frame.winfo_children() if isinstance(w, ttk.Button)]

    assert texts(tab.buttons) == ["檢查", "匯出商品總表…"]
    assert texts(tab.save_button.master) == ["存檔", "人工核對完成…"]


# ---------- 人工核對完成（#527） ----------


def test_manual_check_is_only_offered_for_unlocked_products(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    button = window.manual.manual_check_button
    assert str(button.cget("state")) == "disabled", "還沒打開任何一檔"

    opened(window, maintenance_draft())
    assert str(button.cget("state")) == "normal"
    assert "人工核對完成…" in window.manual.intro_var.get()
    opened(window, maintenance_draft(OrderReview.LOCKED))
    assert str(button.cget("state")) == "disabled"
    opened(window, new_product_draft())
    assert str(button.cget("state")) == "disabled", "只有交易明細的要先存成商品條件"


def test_manual_check_asks_to_save_unsaved_edits_first(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    errors, forms = [], []
    monkeypatch.setattr(messagebox, "showerror", lambda title, message, **_: errors.append(message))
    monkeypatch.setattr(sp_workflow.tabs.manual, "ask_form", forms.append)
    editor = opened(window, maintenance_draft())
    type_into(editor, "Coupon p.a. (%)", "11")

    window.manual.manual_check()

    assert errors == ["有改過、還沒存檔的內容，請先存檔再人工核對。"] and forms == []


def test_manual_check_writes_locks_and_reopens_the_product(master, tmp_path: Path, monkeypatch, immediate) -> None:
    window = new_window(master, tmp_path, background=immediate, logged_in=True)
    tab = window.manual
    entry = ManualCheck(A_TDCC, "XS1234567890", (dt.date(2027, 1, 15), dt.date(2027, 4, 15)), {}, "不支援的上手")
    asked, sent, questions = [], [], []

    def ask(form):
        asked.append((form.isin_var.get(), form.prices))
        form.destroy()
        return entry

    def check(settings, found, config):
        sent.append(found)
        return ManualCheckResult(ManualCheckReceipt(A_TDCC, 3))

    monkeypatch.setattr(sp_workflow.tabs.manual, "ask_form", ask)
    monkeypatch.setattr(messagebox, "askyesno", lambda title, message, **_: questions.append(message) or True)
    monkeypatch.setattr(sp_workflow.tabs.manual, "manual_check", check)
    monkeypatch.setattr(
        sp_workflow.tabs.manual,
        "open_product",
        lambda settings, tdcc, paths: Opened(maintenance_draft(OrderReview.LOCKED), CONFIG),
    )
    opened(window, maintenance_draft())

    tab.manual_check()

    assert asked == [("", {})], "收盤價的商品不用輸入進場價"
    assert sent == [entry]
    assert "會寫入 ISIN XS1234567890、2 個比價日，然後鎖住（跳過自動核對），接著比價。" in questions[0]
    assert tab.status_var.get() == "已人工核對並鎖住 029199990001（寫入 3 個值）。比價：這次沒有跑。"
    assert tab.intro_var.get().startswith("已核對通過、條件已鎖住"), "重新打開，換成更正的規則"
    assert str(tab.manual_check_button.cget("state")) == "disabled"


def test_saying_no_to_the_confirmation_writes_nothing(master, tmp_path: Path, monkeypatch, immediate) -> None:
    window = new_window(master, tmp_path, background=immediate, logged_in=True)
    sent = []
    entry = ManualCheck(A_TDCC, "XS1234567890", (dt.date(2027, 4, 15),))
    monkeypatch.setattr(sp_workflow.tabs.manual, "ask_form", lambda form: form.destroy() or entry)
    monkeypatch.setattr(messagebox, "askyesno", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(sp_workflow.tabs.manual, "manual_check", lambda *args: sent.append(args))
    opened(window, maintenance_draft())

    window.manual.manual_check()

    assert sent == []


def test_the_manual_check_form_reads_dates_one_per_line_and_vwap_prices(master) -> None:
    form = ManualCheckForm(
        master,
        A_TDCC,
        isin="xs1234567890",
        autocall=[dt.date(2027, 1, 15)],
        final=dt.date(2027, 4, 15),
        vwap=[(1, "AAA UW"), (2, "BBB UN")],
        reason="不支援",
        seen={"Coupon p.a. (%)": Decimal("12.5")},
        lookback=True,
    )
    form.dates.mark_set(tk.INSERT, "end-1c")
    assert form.dates.bind("<Return>"), "比價日欄自己接 Enter"
    assert form._newline(None) == "break", "Enter 換行，不往上傳到視窗的「確定」"
    form.dates.insert(tk.INSERT, "2027-04-15")
    form.prices[1].set("50")
    form.prices[2].set("20.1")

    assert form.winfo_exists() and form.result is None
    assert form.read() == ManualCheck(
        A_TDCC,
        "XS1234567890",
        (dt.date(2027, 1, 15), dt.date(2027, 4, 15)),
        {1: Decimal(50), 2: Decimal("20.1")},
        "不支援",
        {"Coupon p.a. (%)": Decimal("12.5")},
    )
    form.prices[2].set("abc")
    form.confirm()
    assert form.error_var.get() == "UL_2 進場價「abc」不是數字" and form.winfo_exists(), "看不懂時視窗不關"
    form.destroy()
