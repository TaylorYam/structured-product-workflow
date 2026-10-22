"""更正商品條件（#55）：核對通過、條件鎖住之後改條件。對真的 PostgreSQL；資料全部虛構。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from db_synth import price, trade, write_daily
from order_synth import CONFIG, A, B, order_row
from sp_workflow.check_terms import DONE, TO_CONFIRM, recalculate
from sp_workflow.daily_import import import_daily
from sp_workflow.order_import import (
    apply_correction,
    check_draft,
    drafts_from,
    import_drafts,
    load_correction,
)
from sp_workflow.product_write import (
    LOCKED,
    CorrectionError,
)

pytestmark = pytest.mark.db

UF_215 = {"Upfront / NotePrice (%)": Decimal("97.85")}  # UF 2.15，和 db_synth 的交易列一樣


def import_locked(pg, tdcc=A, **changes):
    receipt = import_drafts(drafts_from([order_row(tdcc, {**UF_215, **changes})], pg), pg, CONFIG, user="tester")
    assert receipt.lines[0].imported, receipt.lines[0].detail
    pg.execute("UPDATE workflow.product SET locked_at = now() WHERE tdcc = %s", (tdcc,))


def import_with_prices(pg, tmp_path, **changes):
    """有交易明細與股價、算過核對條件之後才鎖住。"""
    path = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "台北", 100000)], [price("AAA UW"), price("BBB UN")])
    import_daily(path, pg)
    receipt = import_drafts(drafts_from([order_row(A, {**UF_215, **changes})], pg), pg, CONFIG, user="tester")
    assert receipt.lines[0].imported, receipt.lines[0].detail
    assert recalculate(pg, CONFIG.calendar).line_of(A).status == DONE
    pg.execute("UPDATE workflow.product SET locked_at = now() WHERE tdcc = %s", (A,))


def column(pg, name: str, tdcc=A):
    return pg.execute(f"SELECT {name} FROM workflow.product WHERE tdcc = %s", (tdcc,)).fetchone()[0]


def prices(pg, tdcc=A):
    return pg.execute(
        "SELECT seq, code, initial_price, strike_price, ki_price, ko_price FROM workflow.underlying_price"
        " WHERE tdcc = %s ORDER BY seq",
        (tdcc,),
    ).fetchall()


def corrections(pg):
    return pg.execute(
        "SELECT tdcc, corrected_by, reason, changes, previous FROM workflow.term_correction ORDER BY correction_id"
    ).fetchall()


# ---------- 帶出條件 ----------


def test_a_locked_product_opens_with_the_database_values(pg) -> None:
    import_locked(pg)

    draft = load_correction(pg, A, CONFIG)

    assert draft.tdcc == A and draft.issuer == "BARC"
    assert draft.values["Coupon p.a. (%)"] == Decimal("12.5")
    assert draft.values["Upfront / NotePrice (%)"] == Decimal("97.85")
    assert draft.values == draft.stored and draft.edits() == []
    assert not draft.locked, "更正畫面可以改"
    assert check_draft(draft, CONFIG) == {}


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda pg: None, "資料庫沒有這檔商品"),
        (lambda pg: import_drafts(drafts_from([order_row()], pg), pg, CONFIG, user="t"), "還沒鎖住"),
    ],
)
def test_only_locked_products_can_be_corrected(pg, setup, message) -> None:
    setup(pg)

    with pytest.raises(CorrectionError, match=message):
        load_correction(pg, A, CONFIG)


# ---------- 寫入 ----------


def test_a_correction_updates_the_terms_keeps_the_lock_and_records_who_and_why(pg) -> None:
    import_locked(pg)
    locked_at = column(pg, "locked_at")
    pg.execute(
        "INSERT INTO workflow.check_result (tdcc, kind, file_name, category, status, released, filled, result_file,"
        " checked_by) VALUES (%s, '說明書', 'a.pdf', '通過', 'PASS', false, true, 'r.xlsx', 'checker')",
        (A,),
    )
    draft = load_correction(pg, A, CONFIG)
    draft.values["Coupon p.a. (%)"] = Decimal(11)
    draft.values["KO Type"] = "Period End"

    receipt = apply_correction(pg, draft, CONFIG, reason="Order 抄錯", user="operator1")

    assert receipt.correction_id is not None
    assert receipt.changes == (("KO Type", "Daily Memory", "Period End"), ("Coupon p.a. (%)", Decimal("12.5"), 11))
    assert receipt.calc is None, "沒有核對條件（沒有交易明細與股價時不會有）就不重算"
    assert (column(pg, "coupon_pct"), column(pg, "ko_freq"), column(pg, "ko_memory")) == (Decimal(11), "P", "N")
    assert column(pg, "locked_at") == locked_at, "維持鎖住"
    assert pg.execute("SELECT count(*), max(category) FROM workflow.check_result").fetchone() == (1, "通過")
    ((tdcc, by, reason, changes, previous),) = corrections(pg)
    assert (tdcc, by, reason) == (A, "operator1", "Order 抄錯")
    assert changes == [["KO Type", "Daily Memory", "Period End"], ["Coupon p.a. (%)", "12.5", "11"]]
    assert previous["coupon_pct"] == "12.5" and previous["ko_freq"] == "D"
    assert draft.stored == draft.values, "寫入後畫面和資料庫一致"

    again = import_drafts(drafts_from([order_row(changes=UF_215)], pg), pg, CONFIG, user="tester")
    assert again.lines[0].result == LOCKED, "更正後重新匯入照樣不覆蓋"
    assert column(pg, "coupon_pct") == Decimal(11)


def test_the_reason_is_optional(pg) -> None:
    import_locked(pg)
    draft = load_correction(pg, A, CONFIG)
    draft.values["Tenor (m)"] = 12

    apply_correction(pg, draft, CONFIG, user="operator1")

    assert corrections(pg)[0][2] is None


def test_nothing_changed_writes_nothing(pg) -> None:
    import_locked(pg)

    receipt = apply_correction(pg, load_correction(pg, A, CONFIG), CONFIG, user="operator1")

    assert receipt.correction_id is None and receipt.changes == ()
    assert corrections(pg) == []


def test_problems_block_the_correction(pg) -> None:
    import_locked(pg)
    draft = load_correction(pg, A, CONFIG)
    draft.values["Maturity date"] = None
    draft.values["Coupon p.a. (%)"] = Decimal(11)

    with pytest.raises(CorrectionError, match="Maturity date：必填"):
        apply_correction(pg, draft, CONFIG, user="operator1")

    assert column(pg, "coupon_pct") == Decimal("12.5") and corrections(pg) == []


def test_mistyped_fields_block_the_correction(pg) -> None:
    import_locked(pg)
    draft = load_correction(pg, A, CONFIG)
    draft.input_errors["Tenor (m)"] = "要填整數"

    with pytest.raises(CorrectionError, match="Tenor \\(m\\)：要填整數"):
        apply_correction(pg, draft, CONFIG, user="operator1")


def test_the_issue_date_comes_from_the_ts_and_cannot_be_corrected(pg) -> None:
    import_locked(pg)
    draft = load_correction(pg, A, CONFIG)
    draft.values["Issue date"] = dt.date(2026, 10, 23)

    with pytest.raises(CorrectionError, match="Issue date：TS 回填"):
        apply_correction(pg, draft, CONFIG, user="operator1")


def test_a_product_changed_after_opening_must_be_reloaded(pg) -> None:
    import_locked(pg)
    draft = load_correction(pg, A, CONFIG)
    draft.values["Coupon p.a. (%)"] = Decimal(11)
    pg.execute("UPDATE workflow.product SET tenor_months = 12 WHERE tdcc = %s", (A,))  # 別人在這之間改過

    with pytest.raises(CorrectionError, match="重新載入"):
        apply_correction(pg, draft, CONFIG, user="operator1")

    assert column(pg, "coupon_pct") == Decimal("12.5")


def test_a_product_unlocked_after_opening_is_not_corrected(pg) -> None:
    import_locked(pg)
    draft = load_correction(pg, A, CONFIG)
    draft.values["Coupon p.a. (%)"] = Decimal(11)
    pg.execute("UPDATE workflow.product SET locked_at = NULL WHERE tdcc = %s", (A,))

    with pytest.raises(CorrectionError, match="還沒鎖住"):
        apply_correction(pg, draft, CONFIG, user="operator1")


def test_a_new_currency_takes_its_default_denomination(pg) -> None:
    import_locked(pg)
    pg.execute("UPDATE workflow.product SET denomination = 2000 WHERE tdcc = %s", (A,))  # 放行時改成 TS 的值
    same = load_correction(pg, A, CONFIG)
    same.values["Coupon p.a. (%)"] = Decimal(11)
    apply_correction(pg, same, CONFIG, user="operator1")
    assert column(pg, "denomination") == Decimal(2000), "幣別沒改時面額不動"

    draft = load_correction(pg, A, CONFIG)
    draft.values["Currency"] = "JPY"
    receipt = apply_correction(pg, draft, CONFIG, user="operator1")

    assert column(pg, "denomination") == Decimal(1000000)
    assert receipt.changes == (("Currency", "USD", "JPY"), ("單位面額", Decimal("2000.0000"), Decimal("1000000.0000")))


def insert_old_product(pg, tdcc: str, *, issuer: str = "Barclays", ko_freq: str = "D") -> None:
    """以前清單更新匯入、已鎖住的商品（沒有核對條件）。"""
    run_id = pg.execute(
        "INSERT INTO workflow.import_run (daily_file_name, daily_sha256, summary) VALUES ('d.xlsx', 'x', '{}')"
        " RETURNING run_id"
    ).fetchone()[0]
    pg.execute(
        "INSERT INTO workflow.product (tdcc, product, issuer, isin, currency, trade_date, strike_date, issue_date,"
        " final_valuation_date, maturity_date, initial_pricing, non_call_months, underlying_1, strike_pct, ko_freq,"
        " ko_memory, ko_type, ko_pct, coupon_pct, upfront_pct, tenor_months, ki_freq, barrier_type, ki_pct,"
        " denomination, status_event, first_run_id, locked_at)"
        " VALUES (%s, 'FCN', 'Barclays', 'XS0000000001', 'USD', '2026-01-15', '2026-01-15', '2026-01-22',"
        " '2026-07-15', '2026-07-22', '收盤價', 1, 'AAA UW', 80, 'D', 'Y', 'Daily Memory', 100, 12.5, 1.5, 6, 'AM',"
        " 'EKI', 65, 10000, '在庫', %s, now())",
        (tdcc, run_id),
    )
    pg.execute("UPDATE workflow.product SET issuer = %s, ko_freq = %s WHERE tdcc = %s", (issuer, ko_freq, tdcc))


def test_an_old_inventory_product_is_corrected_without_recalculation(pg) -> None:
    insert_old_product(pg, B)
    draft = load_correction(pg, B, CONFIG)
    draft.values["Strike (%)"] = Decimal(75)

    receipt = apply_correction(pg, draft, CONFIG, user="operator1")

    assert receipt.calc is None and column(pg, "strike_pct", B) == Decimal(75)
    assert column(pg, "source", B) == "清單更新" and column(pg, "isin", B) == "XS0000000001"
    assert pg.execute("SELECT count(*) FROM workflow.check_terms").fetchone()[0] == 0


# ---------- 重算價格 ----------


def test_prices_are_recalculated_and_recorded(pg, tmp_path) -> None:
    import_with_prices(pg, tmp_path)
    before = prices(pg)
    draft = load_correction(pg, A, CONFIG)
    draft.values["Strike (%)"] = Decimal(75)

    receipt = apply_correction(pg, draft, CONFIG, user="operator1")

    assert receipt.calc is not None and receipt.calc.status == DONE
    assert [p[3] for p in prices(pg)] == [Decimal("75.3750"), Decimal("75.3750")]
    assert [p[2] for p in prices(pg)] == [p[2] for p in before], "進場價不變"
    assert receipt.changes[1:] == (
        ("UL_1_執行價", Decimal("80.4000"), Decimal("75.3750")),
        ("UL_2_執行價", Decimal("80.4000"), Decimal("75.3750")),
    )
    assert corrections(pg)[0][3][1] == ["UL_1_執行價", "80.4", "75.375"]


def test_a_new_cross_check_difference_shows_on_the_correction(pg, tmp_path) -> None:
    import_with_prices(pg, tmp_path)
    draft = load_correction(pg, A, CONFIG)
    draft.values["Upfront / NotePrice (%)"] = Decimal(98)

    receipt = apply_correction(pg, draft, CONFIG, user="operator1")

    assert receipt.calc is not None and receipt.calc.status == TO_CONFIRM
    assert "UF" in receipt.calc.reason
    assert column(pg, "locked_at") is not None


def vwap_backfilled(pg, tmp_path, **changes):
    """VWAP 商品：核對條件算過（價格空白），核對通過後 TS 回填四個價格。"""
    import_with_prices(pg, tmp_path, **{"期初價格": "VWAP", **changes})
    for seq, values in ((1, (101, 81, 66, 101)), (2, (51, 41, 33, 51))):
        pg.execute(
            "UPDATE workflow.underlying_price SET initial_price = %s, strike_price = %s, ki_price = %s, ko_price = %s"
            " WHERE tdcc = %s AND seq = %s",
            (*values, A, seq),
        )


def test_vwap_prices_from_the_ts_are_kept_and_only_changed_percentages_recalculated(pg, tmp_path) -> None:
    vwap_backfilled(pg, tmp_path)
    draft = load_correction(pg, A, CONFIG)
    draft.values["Strike (%)"] = Decimal(75)

    apply_correction(pg, draft, CONFIG, user="operator1")

    assert prices(pg) == [
        (1, "AAA UW", Decimal(101), Decimal("75.7500"), Decimal(66), Decimal(101)),
        (2, "BBB UN", Decimal(51), Decimal("38.2500"), Decimal(33), Decimal(51)),
    ]


def test_vwap_prices_of_a_changed_underlying_are_blank(pg, tmp_path) -> None:
    vwap_backfilled(pg, tmp_path)
    draft = load_correction(pg, A, CONFIG)
    draft.values["BBG Code 2"] = "CCC UN"
    draft.values["Barrier Type"] = "NONE"
    draft.values["KI Barrier (%)"] = None

    receipt = apply_correction(pg, draft, CONFIG, user="operator1")

    assert prices(pg) == [
        (1, "AAA UW", Decimal(101), Decimal(81), None, Decimal(101)),
        (2, "CCC UN", None, None, None, None),
    ]
    assert receipt.calc is not None and receipt.calc.status == TO_CONFIRM, "價格空白不算完成"
    assert receipt.calc.reason.startswith("更正後 UL_2 CCC UN 沒有 TS 回填的價格")


def test_switching_to_vwap_does_not_keep_close_prices(pg, tmp_path) -> None:
    import_with_prices(pg, tmp_path)
    draft = load_correction(pg, A, CONFIG)
    draft.values["期初價格"] = "VWAP"

    apply_correction(pg, draft, CONFIG, user="operator1")

    assert [p[2:] for p in prices(pg)] == [(None, None, None, None)] * 2


def test_the_issuer_is_kept_even_when_its_mapping_is_gone(pg) -> None:
    insert_old_product(pg, "999199990001")  # 前三碼不在上手編號對照：用發行機構寫法反查
    insert_old_product(pg, "998199990001", issuer="已併購的上手")  # 寫法也查不到：上手不改，照樣可以更正
    for tdcc in ("999199990001", "998199990001"):
        draft = load_correction(pg, tdcc, CONFIG)
        draft.values["Coupon p.a. (%)"] = Decimal(11)

        apply_correction(pg, draft, CONFIG, user="operator1")

    assert load_correction(pg, "999199990001", CONFIG).issuer == "BARC"
    assert column(pg, "issuer", "998199990001") == "已併購的上手"
    assert column(pg, "coupon_pct", "998199990001") == Decimal(11)


def test_derived_codes_rewritten_without_an_edit_are_recorded(pg) -> None:
    insert_old_product(pg, B, ko_freq="P")  # 舊資料的代碼和 KO Type（Daily Memory）的換算不同
    draft = load_correction(pg, B, CONFIG)
    draft.values["Coupon p.a. (%)"] = Decimal(11)

    receipt = apply_correction(pg, draft, CONFIG, user="operator1")

    assert receipt.changes == (("Coupon p.a. (%)", Decimal("12.5"), 11), ("ko_freq", "P", "D"))


def test_fields_that_do_not_affect_the_check_terms_do_not_recalculate(pg, tmp_path) -> None:
    import_with_prices(pg, tmp_path)
    before = prices(pg)
    pg.execute("DELETE FROM workflow.daily_price")  # 之後股價檔不見了，也不會把原本的價格算掉
    draft = load_correction(pg, A, CONFIG)
    draft.values["Coupon p.a. (%)"] = Decimal(11)

    receipt = apply_correction(pg, draft, CONFIG, user="operator1")

    assert receipt.calc is None and receipt.changes == (("Coupon p.a. (%)", Decimal("12.5"), 11),)
    assert prices(pg) == before
