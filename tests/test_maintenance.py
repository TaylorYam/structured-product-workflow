"""人工維護（#115、ADR 0017）：檢查缺資料的商品，手動補齊、修改商品條件。資料庫測試對真的 PostgreSQL；資料全部虛構。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from db_synth import price, trade, write_daily
from fcn_checker.check_config import ConfigPaths
from order_synth import CONFIG, A, B, maintenance_draft, open_terms, order_row
from sp_workflow.check_terms import DONE, TO_CONFIRM, recalculate
from sp_workflow.daily_import import import_daily
from sp_workflow.order_terms import TDCC_FIELD
from sp_workflow.product_record import FIELDS, REQUIRED
from sp_workflow.product_terms import _MISSING_NOTE, TermsError, missing_products, problems, write, write_batch
from sp_workflow.status import ImportResult, OrderReview, ProductSource
from sp_workflow.window_actions import _drafts, open_product

UF_215 = {"Upfront / NotePrice (%)": Decimal("97.85")}  # UF 2.15，和 db_synth 的交易列一樣
C = "029199990003"  # 只有交易明細（BARC）
PATHS = ConfigPaths.with_fallback(Path(__file__).resolve().parents[1] / "config" / "review_standard.toml")


# ---------- 檢查（不連資料庫） ----------


def test_blank_required_fields_are_marked_missing_but_do_not_block() -> None:
    draft = maintenance_draft(**{"Maturity date": None, "Strike (%)": None})

    found = problems(draft, CONFIG)

    assert found == {"Strike (%)": _MISSING_NOTE, "Maturity date": _MISSING_NOTE}


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"Product": None}, {"Product": "必填"}),  # 商品種類在資料庫不能空白
        ({"Strike date": dt.date(2026, 10, 14)}, {"Strike date": "不能早於 Trade date"}),
        ({"BBG Code 2": None, "BBG Code 3": "CCC UN"}, {"BBG Code 2": "標的要從 BBG Code 1 依序填，中間不能空"}),
        ({"Barrier Type": "NONE"}, {"KI Barrier (%)": "Barrier Type 是 NONE 時要空白"}),
        ({"Tenor (m)": 0}, {"Tenor (m)": "至少 1"}),
    ],
)
def test_format_problems_still_block(changes, expected) -> None:
    assert problems(maintenance_draft(**changes), CONFIG) == expected


def test_cross_field_checks_skip_blank_fields() -> None:
    draft = maintenance_draft(
        **{"Trade date": None, "Barrier Type": None, "Currency": None, "Tenor (m)": None, "Strike date": None}
    )

    assert problems(draft, CONFIG) == {
        name: _MISSING_NOTE for name in ("Currency", "Tenor (m)", "Barrier Type", "Trade date", "Strike date")
    }


def test_a_blank_ki_with_a_barrier_still_blocks() -> None:
    """KI(%) 不在必填欄：有 KI 卻空著存進去，「檢查」找不到、寄信寫成「-」，所以照匯入的規則擋。"""
    draft = maintenance_draft(**{"KI Barrier (%)": None})

    assert problems(draft, CONFIG) == {"KI Barrier (%)": "有 KI 時必填；還不確定就把 Barrier Type 也先空著"}
    both = maintenance_draft(**{"KI Barrier (%)": None, "Barrier Type": None})
    assert problems(both, CONFIG) == {"Barrier Type": _MISSING_NOTE}


def test_mistyped_fields_block() -> None:
    draft = maintenance_draft()
    draft.input_errors["Tenor (m)"] = "要填整數"

    assert problems(draft, CONFIG) == {"Tenor (m)": "要填整數"}


def test_locked_products_follow_the_correction_rules() -> None:
    draft = maintenance_draft(OrderReview.LOCKED, **{"Maturity date": None})

    assert problems(draft, CONFIG) == {"Maturity date": "必填"}


BEN = {
    "Product": "BEN",
    "Guaranteed Periods (m)": 6,
    "KO Type": "Period End",
    "Barrier Type": "NONE",
    "KI Barrier (%)": None,
}


@pytest.mark.parametrize("status", [OrderReview.UNLOCKED, OrderReview.LOCKED])
def test_a_ben_cannot_be_saved_with_a_blank_participation(status) -> None:
    """BEN 的參與率（#373、ADR 0029）人工維護、更正都不能清成空白；其他商品要空白。"""
    draft = maintenance_draft(status, **BEN, **{"Participation (%)": None})

    assert problems(draft, CONFIG) == {"Participation (%)": "BEN 必填（一般 100、Digital 0）"}
    assert problems(maintenance_draft(status, **BEN, **{"Participation (%)": Decimal(0)}), CONFIG) == {}
    fcn = maintenance_draft(status, **{"Participation (%)": Decimal(100)})
    assert problems(fcn, CONFIG) == {"Participation (%)": "FCN 沒有參與率，要空白"}


def test_a_new_product_needs_a_known_issuer() -> None:
    draft = maintenance_draft(OrderReview.TRADES_ONLY, tdcc="999199990001", issuer="")

    assert problems(draft, CONFIG) == {TDCC_FIELD: "TDCC 前三碼 999 不在上手編號對照"}


# ---------- 資料庫 ----------


def import_order(pg, tdcc: str = A, **changes) -> None:
    receipt = write_batch(pg, _drafts([order_row(tdcc, {**UF_215, **changes})], CONFIG, pg), CONFIG, user="tester")
    assert receipt.lines[0].imported, receipt.lines[0].detail


def import_trades(pg, tmp_path, *tdccs: str) -> None:
    trades = [trade(tdcc, "Amy", "台北", 100000) for tdcc in tdccs]
    import_daily(write_daily(tmp_path / "d.xlsx", trades, [price("AAA UW"), price("BBB UN")]), pg)


def blank(pg, tdcc: str, *columns: str) -> None:
    pg.execute(f"UPDATE workflow.product SET {', '.join(f'{c} = NULL' for c in columns)} WHERE tdcc = %s", (tdcc,))


def lock(pg, tdcc: str = A) -> None:
    pg.execute("UPDATE workflow.product SET locked_at = now() WHERE tdcc = %s", (tdcc,))


def column(pg, name: str, tdcc: str = A):
    return pg.execute(f"SELECT {name} FROM workflow.product WHERE tdcc = %s", (tdcc,)).fetchone()[0]


def records(pg):
    return pg.execute(
        "SELECT tdcc, corrected_by, reason, changes, previous, locked FROM workflow.term_correction"
        " ORDER BY correction_id"
    ).fetchall()


@pytest.mark.db
def test_check_lists_unlocked_products_missing_data_and_trade_only_tdccs(pg, tmp_path) -> None:
    import_trades(pg, tmp_path, A, C)
    import_order(pg, A)  # 齊全：不列
    import_order(pg, B)
    blank(pg, B, "maturity_date", "strike_pct")
    import_order(pg, "029199990004")
    blank(pg, "029199990004", "coupon_pct")
    lock(pg, "029199990004")  # 已鎖住：不列

    found = missing_products(pg, CONFIG)

    assert [(p.tdcc, p.issuer, p.status, p.missing) for p in found] == [
        (C, "BARC", OrderReview.TRADES_ONLY, REQUIRED),
        (B, "SG", OrderReview.UNLOCKED, ("Strike (%)", "Maturity date")),
    ]
    assert found[1].values["Coupon p.a. (%)"] == Decimal("12.5") and found[1].values["Maturity date"] is None
    assert set(found[0].values) == set(FIELDS) and not any(found[0].values.values())


@pytest.mark.db
def test_a_trade_only_tdcc_opens_blank_with_its_issuer(pg, tmp_path) -> None:
    import_trades(pg, tmp_path, C)

    draft = open_terms(pg, C, CONFIG)

    assert (draft.tdcc, draft.issuer, draft.row.status) == (C, "BARC", OrderReview.TRADES_ONLY)
    assert draft.values == {**dict.fromkeys(FIELDS), "Product": "FCN"}
    assert draft.edits() == [] and draft.differences() == {} and not draft.locked_reason


@pytest.mark.db
def test_a_tdcc_in_neither_table_cannot_be_opened(pg, pg_settings) -> None:
    with pytest.raises(TermsError, match="交易明細和商品條件都沒有這檔"):
        open_product(pg_settings, C, PATHS)


@pytest.mark.db
def test_any_product_opens_including_complete_and_locked_ones(pg) -> None:
    import_order(pg, A)
    assert open_terms(pg, A, CONFIG).row.status == OrderReview.UNLOCKED

    lock(pg, A)
    draft = open_terms(pg, A, CONFIG)

    assert draft.row.status == OrderReview.LOCKED and not draft.locked_reason, "已鎖住的也可以改（照更正規則）"
    assert draft.values["Coupon p.a. (%)"] == Decimal("12.5") and draft.values == draft.stored


@pytest.mark.db
def test_a_trade_only_tdcc_gets_partial_terms(pg, tmp_path) -> None:
    import_trades(pg, tmp_path, C)
    draft = open_terms(pg, C, CONFIG)
    draft.values.update({"Currency": "USD", "Coupon p.a. (%)": Decimal(11), "Trade date": dt.date(2026, 10, 15)})

    receipt = write(pg, draft, CONFIG, reason="起單建檔漏了", user="operator1")

    assert receipt.correction_id is not None and not receipt.locked and receipt.created
    assert (column(pg, "source", C), column(pg, "issuer", C), column(pg, "denomination", C)) == (
        ProductSource.MANUAL,
        "Barclays",
        Decimal(10000),
    )
    assert column(pg, "coupon_pct", C) == Decimal(11) and column(pg, "maturity_date", C) is None
    assert column(pg, "locked_at", C) is None
    filled = {"Product", "Currency", "Trade date", "Coupon p.a. (%)"}
    assert receipt.missing == tuple(name for name in REQUIRED if name not in filled)
    assert receipt.calc is not None and receipt.calc.status == TO_CONFIRM
    assert receipt.calc.reason.startswith("缺 Guaranteed Periods (m)、BBG Code 1")
    ((tdcc, by, reason, changes, previous, locked),) = records(pg)
    assert (tdcc, by, reason, previous, locked) == (C, "operator1", "起單建檔漏了", None, False)
    assert ["Coupon p.a. (%)", None, "11"] in changes and ["上手", None, "Barclays"] in changes
    assert draft.row.status == OrderReview.UNLOCKED and draft.stored == draft.values, "存檔後再存就是改這檔"
    (listed,) = missing_products(pg, CONFIG)
    assert (listed.tdcc, listed.status, listed.missing) == (C, OrderReview.UNLOCKED, receipt.missing)


@pytest.mark.db
def test_a_trade_only_tdcc_takes_its_product_from_the_trade_rows(pg, pg_settings, tmp_path) -> None:
    """只有交易明細的 TDCC：商品種類是交易明細的 Product（#302），不固定 FCN。"""
    daily = write_daily(
        tmp_path / "d.xlsx", [trade(C, "Amy", "台北", 100000, product="FCN_Express")], [price("AAA UW")]
    )
    import_daily(daily, pg)

    draft = open_product(pg_settings, C, PATHS).draft
    assert open_terms(pg, C, CONFIG).values["Product"] == draft.values["Product"] == "FCN_Express"
    assert problems(draft, CONFIG)["Guaranteed Periods (m)"] == _MISSING_NOTE
    draft.values["Guaranteed Periods (m)"] = 1
    assert problems(draft, CONFIG)["Guaranteed Periods (m)"] == "FCN_Express 沒有 Non-Call，要填 0"
    draft.values["Guaranteed Periods (m)"] = 0

    write(pg, draft, CONFIG, reason="起單建檔漏了", user="operator1")

    assert (column(pg, "product", C), column(pg, "non_call_months", C)) == ("FCN_Express", 0)


@pytest.mark.db
def test_filling_every_required_field_takes_the_product_off_the_check(pg, tmp_path) -> None:
    import_trades(pg, tmp_path, A)
    import_order(pg, A)
    blank(pg, A, "maturity_date")
    recalculate(pg, CONFIG)
    assert [p.tdcc for p in missing_products(pg, CONFIG)] == [A]
    draft = open_terms(pg, A, CONFIG)
    draft.values["Maturity date"] = dt.date(2027, 4, 22)

    receipt = write(pg, draft, CONFIG, user="operator1")

    assert receipt.missing == () and receipt.calc is not None and receipt.calc.status == DONE
    assert missing_products(pg, CONFIG) == ()
    ((_, _, _, changes, previous, locked),) = records(pg)
    assert changes == [["Maturity date", None, "2027-04-22"]] and previous["maturity_date"] is None and not locked


@pytest.mark.db
def test_an_unlocked_product_can_be_left_with_blanks(pg, tmp_path) -> None:
    import_trades(pg, tmp_path, A)
    import_order(pg, A)
    draft = open_terms(pg, A, CONFIG)
    draft.values["Maturity date"] = None
    draft.values["Coupon p.a. (%)"] = Decimal(11)

    receipt = write(pg, draft, CONFIG, user="operator1")

    assert receipt.missing == ("Maturity date",)
    assert receipt.calc is not None and (receipt.calc.status, receipt.calc.reason) == (TO_CONFIRM, "缺 Maturity date")
    assert column(pg, "maturity_date") is None and column(pg, "coupon_pct") == Decimal(11)
    assert column(pg, "source") == "Order", "起單資料匯入的商品，來源不變"


@pytest.mark.db
def test_nothing_changed_writes_nothing(pg, tmp_path) -> None:
    import_trades(pg, tmp_path, C)
    import_order(pg, A)

    for tdcc in (A, C):
        receipt = write(pg, open_terms(pg, tdcc, CONFIG), CONFIG, user="operator1")
        assert receipt.correction_id is None and receipt.changes == ()

    assert records(pg) == []
    assert pg.execute("SELECT count(*) FROM workflow.product WHERE tdcc = %s", (C,)).fetchone()[0] == 0


@pytest.mark.db
def test_format_problems_block_the_save(pg) -> None:
    import_order(pg, A)
    draft = open_terms(pg, A, CONFIG)
    draft.values["Strike date"] = dt.date(2026, 10, 14)

    with pytest.raises(TermsError, match="沒有存檔：Strike date：不能早於 Trade date"):
        write(pg, draft, CONFIG, user="operator1")

    assert records(pg) == []


@pytest.mark.db
def test_a_new_product_with_an_unknown_issuer_is_not_saved(pg, tmp_path) -> None:
    import_trades(pg, tmp_path, "999199990001")
    draft = open_terms(pg, "999199990001", CONFIG)
    draft.values["Coupon p.a. (%)"] = Decimal(11)

    with pytest.raises(TermsError, match="不在上手編號對照"):
        write(pg, draft, CONFIG, user="operator1")


@pytest.mark.db
def test_a_locked_product_is_saved_like_a_correction(pg) -> None:
    import_order(pg, A)
    lock(pg, A)
    locked_at = column(pg, "locked_at")
    draft = open_terms(pg, A, CONFIG)
    draft.values["Coupon p.a. (%)"] = Decimal(11)

    receipt = write(pg, draft, CONFIG, reason="Order 抄錯", user="operator1")

    assert receipt.locked and receipt.correction_id is not None
    assert column(pg, "locked_at") == locked_at and column(pg, "coupon_pct") == Decimal(11)
    ((_, by, reason, changes, _, locked),) = records(pg)
    assert (by, reason, changes, locked) == ("operator1", "Order 抄錯", [["Coupon p.a. (%)", "12.5", "11"]], True)

    blanked = open_terms(pg, A, CONFIG)
    blanked.values["Maturity date"] = None
    with pytest.raises(TermsError, match="Maturity date：必填"):
        write(pg, blanked, CONFIG, user="operator1")


@pytest.mark.db
def test_correcting_an_exited_product_points_out_the_event_stays(pg, pg_settings) -> None:
    """已出場的商品更正條件（#169）：不重新比價，存檔結果寫出事件不會跟著改。"""
    from sp_workflow.window_actions import save_product

    import_order(pg, A)
    lock(pg, A)
    pg.execute(
        "INSERT INTO workflow.product_event (tdcc, event, event_date, source, recorded_by)"
        " VALUES (%s, 'KO', '2026-11-18', '比價', 'tester')",
        (A,),
    )
    draft = open_terms(pg, A, CONFIG)
    draft.values["Coupon p.a. (%)"] = Decimal(11)

    receipt = save_product(pg_settings, draft, CONFIG, trades=None, reason="Order 抄錯")

    assert receipt.correction_id is not None and receipt.exited == "KO 2026-11-18"
    unchanged = save_product(pg_settings, open_terms(pg, A, CONFIG), CONFIG, trades=None, reason="")
    assert unchanged.exited == "", "沒有改商品條件時不提醒"


def changed_terms(pg) -> None:
    pg.execute("UPDATE workflow.product SET tenor_months = 12 WHERE tdcc = %s", (A,))


def created_by_import(pg) -> None:
    import_order(pg, C)


@pytest.mark.db
@pytest.mark.parametrize(
    ("tdcc", "meanwhile", "message"),
    [
        (A, changed_terms, "打開之後改過了"),
        (A, lock, "打開之後已經鎖住了"),
        (C, created_by_import, "打開之後資料庫已經有這檔的商品條件"),
        (C, lambda pg: pg.execute("DELETE FROM workflow.trade_row"), "交易明細已經沒有這檔"),
    ],
)
def test_changes_after_opening_block_the_save(pg, tmp_path, tdcc, meanwhile, message) -> None:
    import_trades(pg, tmp_path, C)
    import_order(pg, A)
    draft = open_terms(pg, tdcc, CONFIG)
    draft.values["Coupon p.a. (%)"] = Decimal(11)
    meanwhile(pg)

    with pytest.raises(TermsError, match=message):
        write(pg, draft, CONFIG, user="operator1")

    assert records(pg) == []


@pytest.mark.db
def test_a_product_created_by_someone_else_at_the_same_time_asks_for_a_reload(pg, tmp_path, monkeypatch) -> None:
    """還不存在的列 FOR UPDATE 鎖不到：別人在確認之後、寫入之前新增了同一檔，請重新載入，不是資料庫錯誤。"""
    import_trades(pg, tmp_path, C)
    draft = open_terms(pg, C, CONFIG)
    draft.values["Coupon p.a. (%)"] = Decimal(11)
    import_order(pg, C)
    monkeypatch.setattr("sp_workflow.product_terms.read", lambda conn, tdcc, *, lock=False: None)  # 確認時還看不到

    with pytest.raises(TermsError, match="打開之後資料庫已經有這檔的商品條件"):
        write(pg, draft, CONFIG, user="operator1")

    assert records(pg) == [] and column(pg, "coupon_pct", C) == Decimal("12.5")


@pytest.mark.db
@pytest.mark.parametrize("locked", [False, True])
def test_saving_again_after_rounding_is_not_taken_for_a_change_by_someone_else(pg, locked) -> None:
    """百分比存成小數 6 位：存檔後畫面換成資料庫的值，同一檔接著改、再存照常寫入。"""
    import_order(pg, A)
    if locked:
        lock(pg, A)
    draft = open_terms(pg, A, CONFIG)
    draft.values["Coupon p.a. (%)"] = Decimal("11.1234567")
    write(pg, draft, CONFIG, user="operator1")
    assert draft.values["Coupon p.a. (%)"] == Decimal("11.123457") == column(pg, "coupon_pct")

    draft.values["Tenor (m)"] = 12
    receipt = write(pg, draft, CONFIG, user="operator1")

    assert receipt.correction_id is not None and column(pg, "tenor_months") == 12
    assert [changes for _, _, _, changes, _, _ in records(pg)][1] == [["Tenor (m)", 6, 12]]


@pytest.mark.db
def test_the_order_sheet_import_takes_over_a_manual_product(pg, tmp_path) -> None:
    import_trades(pg, tmp_path, C)
    draft = open_terms(pg, C, CONFIG)
    draft.values["Coupon p.a. (%)"] = Decimal(11)
    write(pg, draft, CONFIG, user="operator1")

    receipt = write_batch(pg, _drafts([order_row(C, UF_215)], CONFIG, pg), CONFIG, user="tester")

    assert receipt.lines[0].result == ImportResult.OVERWRITTEN
    assert receipt.lines[0].detail.startswith("人工維護建立的商品改由起單資料匯入接手")
    assert (column(pg, "source", C), column(pg, "coupon_pct", C)) == ("Order", Decimal("12.5"))
