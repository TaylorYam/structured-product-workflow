"""「2 起單資料」分頁（`sp_workflow.tabs.orders`）：選起單建檔、在視窗裡確認與匯入（#86、#97）、匯入後的核對條件（#88）、結果表複製（#121）。"""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import pytest

from order_synth import missing_row, order_row
from sp_workflow.app import PanelWindow
from sp_workflow.check_terms import DONE, TO_CONFIRM, WAIT_PRICES, CalcLine, CalcReceipt, Difference
from sp_workflow.local_settings import FolderMemory
from sp_workflow.order_terms import OrderRow
from sp_workflow.product_terms import BLOCKED, READY, TO_CHECK, OrderImportLine, OrderImportReceipt, OrderImportResult
from sp_workflow.product_terms import NEW as ORDER_NEW
from sp_workflow.product_terms import SKIPPED as ORDER_SKIPPED
from sp_workflow.tabs.editor import PROBLEM_BG
from window_synth import NEEDS_RELEASE, UF_DIFF, new_window, read_result, release_without_database, texts, type_into


def test_order_terms_tab_remembers_its_sheet(master, tmp_path: Path) -> None:
    memory = FolderMemory.under(tmp_path)
    memory.remember(root="R:/FCN", order_sheet="D:/ELN/起單建檔_20261015.xlsm")

    window = new_window(master, tmp_path)

    assert window.orders.sheet_var.get() == "D:/ELN/起單建檔_20261015.xlsm"
    assert not hasattr(window.orders, "root_folder"), "起單資料頁不用根目錄（#97）"
    assert texts(window.orders.buttons, ttk.Button) == ["讀取", "匯入資料庫", "放行差異…"], "更正搬到人工維護（#115）"
    assert str(window.orders.import_button.cget("state")) == "disabled"


def test_choosing_a_sheet_opens_the_last_folder_or_the_daily_sheet_folder(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    opened = []
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **kwargs: opened.append(kwargs["initialdir"]) or "")
    window.orders.memory.defaults["order_sheet"] = str(tmp_path)  # 起單建檔的預設資料夾（#209）

    window.orders._ask_sheet()
    (tmp_path / "202609").mkdir()
    window.orders.sheet_var.set(str(tmp_path / "202609" / "起單建檔_20260930.xlsm"))
    window.orders._ask_sheet()

    assert opened == [str(tmp_path), str(tmp_path / "202609")]
    assert "order_sheet" not in window.orders.memory.load(), "按取消不記"

    chosen = str(tmp_path / "202609" / "起單建檔_20260929.xlsm")
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **kwargs: chosen)
    window.orders._ask_sheet()

    assert window.orders.memory.load()["order_sheet"] == chosen, "選了就記，不用等按讀取"


def test_reading_without_a_sheet_asks_to_choose_it_first(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    window.orders.tdcc_text.insert("1.0", "029199990001")
    errors = []
    monkeypatch.setattr(messagebox, "showerror", lambda title, message, **kwargs: errors.append(message))
    monkeypatch.setattr(window.orders, "run_in_background", lambda *args, **kwargs: pytest.fail("不該開始執行"))

    window.orders.start()

    assert errors == ["請先選擇起單建檔。"]


def test_order_page_shows_key_figures_when_done(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    window.orders._done(read_result(order_row("029199990001"), missing_row("147000000002")))

    assert window.orders.kpis.values == (("可匯入（檔）", 1, False), ("要處理（檔）", 1, True))
    assert window.orders.status_label.cget("style") == "Done.TLabel"


# ---------------------------------------------------------------- 起單資料在視窗裡確認（#86、#97、ADR 0009、0010）


def order_window(master, tmp_path: Path, *rows: OrderRow) -> PanelWindow:
    window = new_window(master, tmp_path)
    window.orders._done(read_result(*rows))
    return window


def table_rows(tab) -> list[tuple[str, str]]:
    return [tuple(tab.table.item(iid, "values")[:2]) for iid in tab.table.get_children()]


def test_products_needing_work_come_first_and_the_first_one_opens(master, tmp_path: Path) -> None:
    ready, missing = order_row("029199990001"), order_row("029199990002", {"Maturity date": None})
    window = order_window(master, tmp_path, ready, missing)

    assert table_rows(window.orders) == [("029199990002", BLOCKED), ("029199990001", READY)]
    editor = window.orders.editor
    assert editor.draft.tdcc == "029199990002"
    assert editor.rows["Maturity date"].note.cget("text").startswith("必填")
    assert editor.rows["Maturity date"].label.cget("background") == PROBLEM_BG
    assert str(window.orders.import_button.cget("state")) == "normal"


def test_filling_a_red_field_makes_the_product_importable(master, tmp_path: Path) -> None:
    window = order_window(master, tmp_path, order_row("029199990002", {"Maturity date": None}))

    type_into(window.orders.editor, "Maturity date", "2027-04-22")

    assert table_rows(window.orders) == [("029199990002", READY)]
    assert window.orders.editor.draft.values["Maturity date"].isoformat() == "2027-04-22"
    assert window.orders.unsaved


def test_a_mistyped_field_stays_red_and_keeps_only_that_product_out(master, tmp_path: Path, monkeypatch) -> None:
    window = order_window(master, tmp_path, order_row("029199990001"), order_row("029199990002"))
    started = []
    monkeypatch.setattr(window.orders.db, "settings", lambda **k: object())
    monkeypatch.setattr(window.orders, "run_in_background", lambda *a, **k: started.append(a[1]))

    type_into(window.orders.editor, "Trade date", "10/15")
    window.orders.import_now()

    draft = window.orders.editor.draft
    assert window.orders.editor.rows["Trade date"].note.cget("text") == "日期要寫成 2026-10-21"
    assert window.orders.editor.rows["Trade date"].var.get() == "10/15", "保留打的字"
    assert draft.input_errors == {"Trade date": "日期要寫成 2026-10-21"}, "匯入時這一檔會判不匯入"
    assert dict(table_rows(window.orders))["029199990001"] == BLOCKED
    assert dict(table_rows(window.orders))["029199990002"] == READY
    assert started == ["匯入資料庫中…"], "其他檔照常匯入"
    assert str(window.orders.editor.rows["Coupon p.a. (%)"].input.cget("state")) == "disabled", "存檔中不能改"


def test_just_browsing_does_not_count_as_unsaved(master, tmp_path: Path) -> None:
    window = order_window(master, tmp_path, order_row("029199990001"), order_row("029199990002"))

    for iid in ("1", "0", "1"):
        window.orders.table.selection_set(iid)
        window.orders._selected()

    assert not window.orders.has_unsaved()


def test_a_field_with_a_reading_problem_stays_red_even_when_it_differs_from_the_database(master, tmp_path) -> None:
    notes = {"Coupon p.a. (%)": "讀到「abc」，不是數字"}
    window = order_window(master, tmp_path, order_row("029199990001", notes=notes))
    draft = window.orders.editor.draft
    draft.stored = {**draft.values, "Coupon p.a. (%)": 12}

    window.orders.editor.refresh()

    row = window.orders.editor.rows["Coupon p.a. (%)"]
    assert row.label.cget("background") == PROBLEM_BG
    assert row.note.cget("text") == "起單建檔：讀到「abc」，不是數字；資料庫是 12"
    assert row.database.winfo_manager() == "grid"


def test_conflicting_duplicate_fields_are_red_until_filled(master, tmp_path: Path) -> None:
    row = order_row(
        "029199990001",
        {"Coupon p.a. (%)": None},
        notes={"Coupon p.a. (%)": "這檔有 2 列，這欄不同：第 3 列「12.5」、第 4 列「13」；請填正確的值"},
        conflicts=("Coupon p.a. (%)",),
    )
    window = order_window(master, tmp_path, row)
    field = window.orders.editor.rows["Coupon p.a. (%)"]

    assert table_rows(window.orders) == [("029199990001", BLOCKED)]
    assert field.label.cget("background") == PROBLEM_BG and "好幾列" in field.note.cget("text")

    type_into(window.orders.editor, "Coupon p.a. (%)", "13")

    assert table_rows(window.orders) == [("029199990001", TO_CHECK)]


def test_leaving_another_field_keeps_a_cleared_strike_date(master, tmp_path: Path) -> None:
    window = order_window(master, tmp_path, order_row("029199990001"))
    editor = window.orders.editor

    type_into(editor, "Strike date", "")
    type_into(editor, "Coupon p.a. (%)", "13")

    assert editor.rows["Strike date"].var.get() == "" and editor.draft.values["Strike date"] is None


def test_editing_the_trade_date_moves_an_equal_strike_date(master, tmp_path: Path) -> None:
    window = order_window(master, tmp_path, order_row("029199990001"))
    editor = window.orders.editor

    type_into(editor, "Trade date", "2026-10-16")

    assert editor.rows["Strike date"].var.get() == "2026-10-16"
    assert editor.draft.values["Strike date"].isoformat() == "2026-10-16"


def test_choices_use_drop_downs(master, tmp_path: Path) -> None:
    window = order_window(master, tmp_path, order_row("029199990001"))

    editor = window.orders.editor
    assert isinstance(editor.rows["KO Type"].input, ttk.Combobox)
    assert tuple(editor.rows["Barrier Type"].input.cget("values")) == ("NONE", "EKI", "AKI")
    assert not isinstance(editor.rows["Coupon p.a. (%)"].input, ttk.Combobox)


def test_products_not_in_the_sheet_show_their_reason_only(master, tmp_path: Path) -> None:
    window = order_window(master, tmp_path, missing_row("147000000002"))

    editor = window.orders.editor
    assert editor.draft is None
    assert editor.note_var.get() == "沒有可以改的條件：TDCC：起單建檔_20261015.xlsm 裡沒有這檔"
    assert str(editor.rows["Trade date"].input.cget("state")) == "disabled"
    assert str(window.orders.import_button.cget("state")) == "disabled"


def test_import_results_replace_the_status_column(master, tmp_path: Path) -> None:
    window = order_window(
        master, tmp_path, order_row("029199990001"), order_row("029199990002", {"Maturity date": None})
    )
    type_into(window.orders.editor, "Maturity date", "2027-04-22")
    lines = (
        OrderImportLine("029199990002", ORDER_NEW),
        OrderImportLine("029199990001", ORDER_SKIPPED, "Currency：問題"),
    )

    window.orders._saved(OrderImportResult(OrderImportReceipt(7, lines)))

    assert table_rows(window.orders) == [("029199990002", ORDER_NEW), ("029199990001", ORDER_SKIPPED)]
    assert window.orders.kpis.values == (("已匯入（檔）", 1, False), ("沒匯入（檔）", 1, True))
    assert window.orders.status_var.get().startswith("完成（第 7 次起單資料匯入）：新增 1、覆蓋 0、沒有變 0、不匯入 1")
    assert not window.orders.unsaved
    for iid in ("1", "0"):  # 匯入後點來點去，結果不會被洗掉
        window.orders.table.selection_set(iid)
        window.orders._selected()
    assert table_rows(window.orders) == [("029199990002", ORDER_NEW), ("029199990001", ORDER_SKIPPED)]
    assert not window.orders.has_unsaved()


def test_reload_failure_after_import_says_the_database_was_written(master, tmp_path: Path) -> None:
    window = order_window(master, tmp_path, order_row("029199990001"))
    receipt = OrderImportReceipt(8, (OrderImportLine("029199990001", ORDER_NEW),))

    window.orders._saved(OrderImportResult(receipt, reload_error="connection lost"))

    assert "已寫進資料庫，但重新讀取資料庫的值失敗（connection lost）" in window.orders.status_var.get()


def test_closing_with_unsaved_order_edits_asks_and_stays_on_the_order_tab(master, tmp_path: Path, monkeypatch) -> None:
    window = order_window(master, tmp_path, order_row("029199990002", {"Maturity date": None}))
    type_into(window.orders.editor, "Maturity date", "2027-04-22")
    asked = []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or False)

    window.close()

    assert len(asked) == 1 and "還沒匯入資料庫" in asked[0]
    assert master.winfo_exists() and window.notebook.select() == str(window.orders.frame)


def test_reading_again_asks_before_dropping_unsaved_edits(master, tmp_path: Path, monkeypatch) -> None:
    window = order_window(master, tmp_path, order_row("029199990002", {"Maturity date": None}))
    window.orders.sheet_var.set(str(tmp_path / "起單建檔_20261015.xlsm"))
    type_into(window.orders.editor, "Maturity date", "2027-04-22")
    asked = []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or False)
    monkeypatch.setattr(window.orders, "run_in_background", lambda *a, **k: pytest.fail("沒同意就不該重讀"))

    window.orders.start()

    assert len(asked) == 1 and "重新讀取後會不見" in asked[0]
    assert window.orders.editor.draft.values["Maturity date"].isoformat() == "2027-04-22"


def test_reading_without_a_database_setup_does_not_ask_for_a_password(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    window.orders.sheet_var.set(str(tmp_path / "起單建檔_20261015.xlsm"))
    window.orders.tdcc_text.insert("1.0", "029199990001")
    monkeypatch.setattr(simpledialog, "askstring", lambda *a, **k: pytest.fail("沒有連線設定就不問密碼"))
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: pytest.fail("不該跳錯誤"))
    started = []
    monkeypatch.setattr(window.orders, "run_in_background", lambda *a, **k: started.append(a[1]))

    window.orders.start()

    assert started and started[0].startswith("讀取中")


# ---------------------------------------------------------------- 匯入後重算核對條件、放行差異（#88）


def test_order_import_shows_the_check_terms_of_the_batch(master, tmp_path: Path, monkeypatch) -> None:
    window = order_window(master, tmp_path, order_row("029199990001"), order_row("029199990002"))
    lines = (OrderImportLine("029199990001", ORDER_NEW), OrderImportLine("029199990002", ORDER_NEW))
    calcs = (NEEDS_RELEASE, CalcLine("029199990002", DONE, "", True), CalcLine("029199990009", WAIT_PRICES, "", True))
    window.orders._saved(OrderImportResult(OrderImportReceipt(7, lines), CalcReceipt(calcs)))

    values = {
        window.orders.table.set(iid, "tdcc"): window.orders.table.item(iid, "values") for iid in window.orders.items
    }
    assert values["029199990001"][1:] == (ORDER_NEW, "BARC", TO_CONFIRM, "核對條件：" + NEEDS_RELEASE.reason)
    assert values["029199990002"][1:] == (ORDER_NEW, "BARC", DONE, "")
    assert window.orders.kpis.values[-1] == ("核對條件待確認（檔）", 1, True)
    assert "核對條件（這批匯入的 2 檔）：完成 1、等股價 0、等交易明細 0、待確認 1。" in window.orders.status_var.get()

    iid = next(i for i, (row, _) in window.orders.items.items() if row.tdcc == "029199990001")
    window.orders.table.selection_set(iid)
    master.update()
    released = CalcLine("029199990001", DONE, "已放行的差異：UF（…）", True, (Difference(*UF_DIFF.key, released=True),))
    release_without_database(window.orders, monkeypatch, released)
    window.orders.releaser.button.invoke()

    assert window.orders.table.item(iid, "values")[3:] == (DONE, "核對條件：已放行的差異：UF（…）")


# ---------------------------------------------------------------- 結果表複製（#121）


def click_row(tab, item: str) -> None:
    """模擬點一列：游標所在的列（focus）和選取一起換。"""
    tab.table.focus(item)
    tab.table.selection_set(item)
    tab.master.update()


def test_copy_all_keeps_the_product_being_edited(master, tmp_path: Path) -> None:
    window = order_window(master, tmp_path, order_row("029199990001"), order_row("029199990002"))
    tab = window.orders
    second = tab.table.get_children()[1]
    click_row(tab, second)
    editing = tab.editor.draft

    tab.table_area.button.invoke()

    assert tab.table.selection() == (second,) and tab.editor.draft is editing
    assert len(master.clipboard_get().split("\n")) == 3


def test_select_all_keeps_the_product_being_edited(master, tmp_path: Path) -> None:
    window = order_window(master, tmp_path, order_row("029199990001"), order_row("029199990002"))
    tab = window.orders
    second = tab.table.get_children()[1]
    click_row(tab, second)
    editing = tab.editor.draft

    tab.copier.select_all()
    tab.master.update()

    assert len(tab.table.selection()) == 2, "Ctrl+A 全部反白"
    assert tab.editor.draft is editing, "右邊不跳到第一檔"
    assert tab._selected_tdcc() == editing.tdcc, "放行差異照樣對點的那一檔"
