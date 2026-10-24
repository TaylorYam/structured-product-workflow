"""人工維護修改交易明細（#116、ADR 0018）：只改值、不增刪列，規則同每日匯入。資料庫測試對真的 PostgreSQL；資料全部虛構。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from db_synth import price, seed_mailbox, trade, write_daily
from order_synth import CONFIG, MAILBOX, TRADE, A, maintenance_trades, order_row
from sp_workflow.check_terms import DONE, recalculate
from sp_workflow.daily_import import DIFFERENT, UNCHANGED, import_daily, trade_value
from sp_workflow.mailbox import Mailbox
from sp_workflow.product_terms import (
    LOCKED,
    MAINTAIN_NEW,
    CorrectionError,
    drafts_from,
    import_drafts,
    open_maintenance,
    save_maintenance,
)
from sp_workflow.trade_rows import (
    CANCEL_NOTE,
    UNMATCHED_NOTE,
    TradeRowsDraft,
    load_trade_rows,
    trade_notes,
    trade_problems,
    unmatched_names,
)


def trades(*rows: dict[str, object], mailbox: Mailbox = MAILBOX) -> TradeRowsDraft:
    """打開時的交易明細：每列是畫面上對 `TRADE` 的修改。"""
    return maintenance_trades(*rows, mailbox=mailbox)


# ---------- 欄位的值（每日匯入與人工維護共用） ----------


@pytest.mark.parametrize(
    ("header", "raw", "value"),
    [
        ("編號", "2", 2),
        ("編號", 3.0, 3),
        ("交易日期", "2026/10/16", dt.date(2026, 10, 16)),
        ("交易日期", dt.datetime(2026, 10, 16), dt.date(2026, 10, 16)),
        ("金額", "1,234.56789", Decimal("1234.5679")),
        ("UF", "0.0215", Decimal("0.021500")),
        ("FC/PB", "  Amy ", "Amy"),
        ("募集", "-", None),
        ("備註", "", None),
    ],
)
def test_values_follow_the_daily_import_rules(header, raw, value) -> None:
    """Excel 的格子和畫面打的字用同一套解讀，存進資料庫的值才會和之後匯入的一樣。"""
    assert trade_value(header, raw) == value


@pytest.mark.parametrize(
    ("header", "raw", "message"),
    [
        ("金額", "abc", "「abc」不是數字"),
        ("UF", "2%", "「2%」不是數字"),
        ("編號", "1.5", "「1.5」不是整數"),
        ("交易日期", "15/10/2026", "「15/10/2026」不是日期"),
    ],
)
def test_bad_formats_say_why(header, raw, message) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        trade_value(header, raw)


# ---------- 檢查（不連資料庫） ----------


def test_blank_required_fields_block() -> None:
    draft = trades({}, {"金額": None, "分公司": None, "前台主管/投專": None})

    assert trade_problems(draft) == {(2, "金額"): "必填", (2, "分公司"): "必填"}


def test_mistyped_fields_block() -> None:
    draft = trades({})
    draft.rows[0].input_errors["金額"] = "「abc」不是數字"

    assert trade_problems(draft) == {(1, "金額"): "「abc」不是數字"}


@pytest.mark.parametrize("note", ["Cancel", "此筆取消"])
def test_a_cancel_note_blocks_because_rows_cannot_be_deleted(note) -> None:
    """每日匯入不把取消的列存進資料庫；這裡只改值、不刪列，所以不能把列改成取消。"""
    assert trade_problems(trades({"備註": note})) == {(1, "備註"): CANCEL_NOTE}


def test_changed_fc_names_not_in_the_mailbox_are_noted_but_do_not_block() -> None:
    draft = trades({"FC/PB": "amy"}, {"FC/PB": "Zed"}, {"FC/PB": "pb"})

    assert trade_problems(draft) == {}
    assert trade_notes(draft) == {(2, "FC/PB"): UNMATCHED_NOTE}, "大小寫不同算同一人；PB 不比"
    assert unmatched_names(draft.mailbox, draft.edits()) == ("Zed",)


def test_an_empty_mailbox_cannot_match_names() -> None:
    draft = trades({"FC/PB": "Ben"}, mailbox=Mailbox((), {}))

    assert trade_notes(draft) == {(1, "FC/PB"): "信箱還沒有任何人員，FC 名字無法比對"}


def test_unchanged_cells_keep_the_database_values_without_blocking_or_noting() -> None:
    """以前匯入的列（例如備註寫了取消、FC 不在信箱）不擋這次改別的欄；只看改了的格子。"""
    draft = trades({"募集": "Y"})
    draft.rows[0].stored.update({"備註": "此筆取消", "前台主管/投專": None, "FC/PB": "Zed"})
    draft.rows[0].values.update({"備註": "此筆取消", "FC/PB": "Zed"})

    assert trade_problems(draft) == {} and trade_notes(draft) == {}
    assert unmatched_names(draft.mailbox, draft.edits()) == ()


def test_edits_list_each_changed_cell_by_row() -> None:
    draft = trades({}, {"金額": Decimal(120000), "募集": "Y"})

    assert draft.edits() == [(2, "金額", Decimal("100000.0000"), Decimal(120000)), (2, "募集", "N", "Y")]
    assert trades({"金額": Decimal(100000)}).edits() == [], "100000 和 100000.0000 一樣"


def test_differences_map_each_changed_cell_to_the_database_value() -> None:
    draft = trades({}, {"金額": Decimal(120000), "備註": "改"})

    assert draft.differences() == {(2, "金額"): Decimal("100000.0000"), (2, "備註"): None}


# ---------- 資料庫 ----------

B = "029199990002"  # 只有交易明細（BARC）
UF_215 = {"Upfront / NotePrice (%)": Decimal("97.85")}  # UF 2.15，和 db_synth 的交易列一樣


def daily_rows(tdcc: str = A) -> list[dict]:
    """每日更新裡這檔的兩列：台北 Amy 10 萬、台中 Ben 5 萬。"""
    return [trade(tdcc, "Amy", "台北", 100000), trade(tdcc, "Ben", "台中", 50000, number=None)]


def import_trades(pg, tmp_path, rows: list[dict], name: str = "d.xlsx"):
    return import_daily(write_daily(tmp_path / name, rows, [price("AAA UW"), price("BBB UN")]), pg)


def import_product(pg, tmp_path, *, locked: bool = False) -> None:
    """A 有兩列交易明細、股價與起單資料，核對條件算完；`locked` 時再鎖住（核對通過）。"""
    seed_mailbox(pg)
    import_trades(pg, tmp_path, daily_rows())
    receipt = import_drafts(drafts_from([order_row(A, UF_215)], pg), pg, CONFIG, user="tester")
    assert receipt.lines[0].imported, receipt.lines[0].detail
    assert recalculate(pg, CONFIG.calendar).line_of(A).status == DONE
    if locked:
        pg.execute("UPDATE workflow.product SET locked_at = now() WHERE tdcc = %s", (A,))


def opened(pg, tdcc: str = A):
    return open_maintenance(pg, tdcc, CONFIG), load_trade_rows(pg, tdcc)


def allocation(pg, tdcc: str = A):
    return pg.execute(
        "SELECT branch, amount FROM workflow.branch_allocation WHERE tdcc = %s ORDER BY seq", (tdcc,)
    ).fetchall()


def stored_trades(pg, tdcc: str = A):
    return pg.execute(
        "SELECT row_seq, amount, branch, raised FROM workflow.trade_row WHERE tdcc = %s ORDER BY row_seq", (tdcc,)
    ).fetchall()


def records(pg):
    return pg.execute(
        "SELECT tdcc, corrected_by, reason, changes, previous, price_changes, locked"
        " FROM workflow.trade_row_correction ORDER BY correction_id"
    ).fetchall()


def term_records(pg):
    return pg.execute("SELECT tdcc, changes, locked FROM workflow.term_correction ORDER BY correction_id").fetchall()


def calculated_at(pg, tdcc: str = A):
    return pg.execute("SELECT calculated_at FROM workflow.check_terms WHERE tdcc = %s", (tdcc,)).fetchone()[0]


@pytest.mark.db
def test_trade_rows_open_in_row_order_with_the_mailbox(pg, tmp_path) -> None:
    import_product(pg, tmp_path)

    trades = load_trade_rows(pg, A)

    assert [row.seq for row in trades.rows] == [1, 2]
    assert trades.rows[1].values == {
        **TRADE,
        "編號": None,
        "金額": Decimal("50000.0000"),
        "FC/PB": "Ben",
        "分公司": "台中",
    }
    assert trades.rows[0].values == trades.rows[0].stored and trades.edits() == []
    assert [p.name for p in trades.mailbox.people] == ["Amy", "Ben", "Pat"]
    assert load_trade_rows(pg, "037199990009").rows == [], "還沒有交易明細的商品沒有列"


@pytest.mark.db
def test_saving_trade_rows_updates_them_records_and_recalculates_the_allocation(pg, tmp_path) -> None:
    import_product(pg, tmp_path)
    assert allocation(pg) == [("台北", Decimal(100000)), ("台中", Decimal(50000))]
    draft, trades = opened(pg)
    trades.rows[1].values.update({"金額": Decimal(70000), "分公司": "台北", "募集": "Y"})

    receipt = save_maintenance(pg, draft, CONFIG, trades=trades, reason="Excel 後來改了", user="operator1")

    assert receipt.correction_id is None, "商品條件沒改，不寫商品條件的紀錄"
    assert receipt.trade_correction_id is not None and receipt.saved and not receipt.locked
    assert receipt.trade_changes == (
        (2, "金額", Decimal("50000.0000"), Decimal(70000)),
        (2, "分公司", "台中", "台北"),
        (2, "募集", "N", "Y"),
    )
    assert stored_trades(pg) == [(1, Decimal(100000), "台北", "N"), (2, Decimal(70000), "台北", "Y")]
    assert allocation(pg) == [("台北", Decimal(170000))], "分公司配置反映新的金額與分公司"
    assert receipt.calc is not None and receipt.calc.status == DONE
    ((tdcc, by, reason, changes, previous, price_changes, locked),) = records(pg)
    assert (tdcc, by, reason, price_changes, locked) == (A, "operator1", "Excel 後來改了", None, False)
    assert changes == [[2, "金額", "50000", "70000"], [2, "分公司", "台中", "台北"], [2, "募集", "N", "Y"]]
    assert [row["row_seq"] for row in previous] == [1, 2] and previous[1]["amount"] == "50000"
    assert term_records(pg) == []
    assert trades.edits() == [] and trades.rows[1].stored["金額"] == Decimal(70000), "存檔後畫面換成資料庫存的值"


@pytest.mark.db
def test_terms_and_trade_rows_are_saved_together(pg, tmp_path) -> None:
    import_product(pg, tmp_path)
    draft, trades = opened(pg)
    draft.values["Coupon p.a. (%)"] = Decimal(11)
    trades.rows[0].values["募集"] = "Y"

    receipt = save_maintenance(pg, draft, CONFIG, trades=trades, user="operator1")

    assert receipt.correction_id is not None and receipt.trade_correction_id is not None
    assert [changes for _, changes, _ in term_records(pg)] == [[["Coupon p.a. (%)", "12.5", "11"]]]
    assert [changes for _, _, _, changes, _, _, _ in records(pg)] == [[[1, "募集", "N", "Y"]]]


@pytest.mark.db
@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"金額": None}, "交易明細第 2 列 金額：必填"),
        ({"備註": "Cancel"}, f"交易明細第 2 列 備註：{CANCEL_NOTE}"),
    ],
)
def test_trade_problems_block_the_whole_save(pg, tmp_path, changes, message) -> None:
    import_product(pg, tmp_path)
    draft, trades = opened(pg)
    draft.values["Coupon p.a. (%)"] = Decimal(11)
    trades.rows[1].values.update(changes)

    with pytest.raises(CorrectionError, match=f"沒有存檔：{message}"):
        save_maintenance(pg, draft, CONFIG, trades=trades, user="operator1")

    assert records(pg) == [] and term_records(pg) == []
    assert stored_trades(pg)[1] == (2, Decimal(50000), "台中", "N")
    assert pg.execute("SELECT coupon_pct FROM workflow.product").fetchone()[0] == Decimal("12.5"), "商品條件也不寫"


@pytest.mark.db
def test_a_mistyped_cell_blocks_the_save(pg, tmp_path) -> None:
    import_product(pg, tmp_path)
    draft, trades = opened(pg)
    trades.rows[0].input_errors["金額"] = "「abc」不是數字"

    with pytest.raises(CorrectionError, match="交易明細第 1 列 金額：「abc」不是數字"):
        save_maintenance(pg, draft, CONFIG, trades=trades, user="operator1")


@pytest.mark.db
def test_nothing_changed_writes_nothing(pg, tmp_path) -> None:
    import_product(pg, tmp_path)
    draft, trades = opened(pg)

    receipt = save_maintenance(pg, draft, CONFIG, trades=trades, user="operator1")

    assert not receipt.saved and receipt.trade_changes == ()
    assert records(pg) == [] and term_records(pg) == []


@pytest.mark.db
def test_trade_rows_changed_after_opening_block_the_save(pg, tmp_path) -> None:
    import_product(pg, tmp_path)
    draft, trades = opened(pg)
    trades.rows[0].values["募集"] = "Y"
    pg.execute("UPDATE workflow.trade_row SET checker = 'Pat' WHERE tdcc = %s AND row_seq = 2", (A,))

    with pytest.raises(CorrectionError, match="交易明細在打開之後改過了"):
        save_maintenance(pg, draft, CONFIG, trades=trades, user="operator1")

    assert records(pg) == []


@pytest.mark.db
def test_a_trade_row_added_after_opening_blocks_the_save(pg, tmp_path) -> None:
    """例如打開之後重新匯入、多了一列：畫面上的列和資料庫對不上，不寫入。"""
    import_product(pg, tmp_path)
    draft, trades = opened(pg)
    trades.rows[0].values["募集"] = "Y"
    pg.execute(
        "INSERT INTO workflow.trade_row (tdcc, row_seq, trade_date, product, currency, amount, fc_name, branch,"
        " first_run_id) SELECT tdcc, 3, trade_date, product, currency, amount, fc_name, branch, first_run_id"
        " FROM workflow.trade_row"
        " WHERE tdcc = %s AND row_seq = 1",
        (A,),
    )

    with pytest.raises(CorrectionError, match="交易明細在打開之後改過了"):
        save_maintenance(pg, draft, CONFIG, trades=trades, user="operator1")

    assert records(pg) == [] and stored_trades(pg)[0] == (1, Decimal(100000), "台北", "N")


@pytest.mark.db
def test_a_trade_only_tdcc_can_have_its_rows_fixed_without_creating_terms(pg, tmp_path) -> None:
    import_trades(pg, tmp_path, daily_rows(B))
    draft, trades = opened(pg, B)
    trades.rows[0].values["金額"] = Decimal(90000)

    receipt = save_maintenance(pg, draft, CONFIG, trades=trades, user="operator1")

    assert receipt.trade_correction_id is not None and receipt.correction_id is None and receipt.calc is None
    assert pg.execute("SELECT count(*) FROM workflow.product").fetchone()[0] == 0, "商品條件沒填，不新增"
    assert draft.row.status == MAINTAIN_NEW
    assert [(tdcc, locked) for tdcc, _, _, _, _, _, locked in records(pg)] == [(B, False)]


@pytest.mark.db
def test_a_locked_product_stays_locked_and_recalculates_only_for_check_term_fields(pg, tmp_path) -> None:
    import_product(pg, tmp_path, locked=True)
    pg.execute(
        "INSERT INTO workflow.check_result (tdcc, kind, file_name, category, status, released, filled, result_file,"
        " checked_by) VALUES (%s, '說明書', 'a.pdf', '通過', 'PASS', false, true, 'r.xlsx', 'checker')",
        (A,),
    )
    locked_at = pg.execute("SELECT locked_at FROM workflow.product").fetchone()[0]
    before = calculated_at(pg)
    draft, trades = opened(pg)
    assert draft.row.status == LOCKED
    trades.rows[0].values["募集"] = "Y"

    receipt = save_maintenance(pg, draft, CONFIG, trades=trades, user="operator1")

    assert receipt.locked and receipt.calc is None and calculated_at(pg) == before, "募集不影響核對條件，不重算"

    trades.rows[1].values["金額"] = Decimal(70000)
    receipt = save_maintenance(pg, draft, CONFIG, trades=trades, user="operator1")

    assert receipt.calc is not None and calculated_at(pg) > before
    assert allocation(pg) == [("台北", Decimal(100000)), ("台中", Decimal(70000))]
    assert pg.execute("SELECT locked_at FROM workflow.product").fetchone()[0] == locked_at, "維持鎖住"
    assert pg.execute("SELECT count(*), max(category) FROM workflow.check_result").fetchone() == (1, "通過")
    assert [locked for *_, locked in records(pg)] == [True, True]


@pytest.mark.db
def test_trade_problems_block_a_locked_product_too(pg, tmp_path) -> None:
    import_product(pg, tmp_path, locked=True)
    draft, trades = opened(pg)
    trades.rows[1].values["分公司"] = None

    with pytest.raises(CorrectionError, match="沒有更正：交易明細第 2 列 分公司：必填"):
        save_maintenance(pg, draft, CONFIG, trades=trades, user="operator1")

    assert records(pg) == [] and stored_trades(pg)[1] == (2, Decimal(50000), "台中", "N")


@pytest.mark.db
def test_an_unmatched_fc_name_is_saved_and_reported(pg, tmp_path) -> None:
    import_product(pg, tmp_path)
    draft, trades = opened(pg)
    trades.rows[1].values["FC/PB"] = "Zed"

    receipt = save_maintenance(pg, draft, CONFIG, trades=trades, user="operator1")

    assert receipt.trade_correction_id is not None and receipt.unmatched == ("Zed",)


@pytest.mark.db
def test_the_daily_import_shows_existing_once_the_database_matches_excel(pg, tmp_path) -> None:
    """Excel 後來補了欄：每日匯入照舊不覆蓋（內容不同）；人工改到一樣之後，同一份每日更新顯示「已存在」。"""
    seed_mailbox(pg)
    import_trades(pg, tmp_path, daily_rows())
    later = daily_rows()
    later[1].update({"募集": "Y", "TS/IIS 核對": "V", "核對TS/IIS人員": "Pat", "金額": 50000.5})
    assert [line.kind for line in import_trades(pg, tmp_path, later, "later.xlsx").lines] == [DIFFERENT]
    draft, trades = opened(pg)
    typed = {"募集": "Y", "TS/IIS 核對": "V", "核對TS/IIS人員": "Pat", "金額": "50,000.50"}
    trades.rows[1].values.update({name: trade_value(name, text) for name, text in typed.items()})
    save_maintenance(pg, draft, CONFIG, trades=trades, user="operator1")

    assert [line.kind for line in import_trades(pg, tmp_path, later, "again.xlsx").lines] == [UNCHANGED]
