"""商品條件寫入（#124）：起單資料匯入、已鎖住更正、人工維護共用的規則與併發，和按一次「匯入資料庫」的容錯（ADR 0012）。

共用規則（面額、上手、有沒有改、紀錄）各用 `Mode` 參數化測一次；各模式自己的規則在 test_order_import、
test_term_correction、test_maintenance、test_trade_rows。資料庫測試對真的 PostgreSQL；資料全部虛構。
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from db_synth import price, trade, write_daily
from order_synth import CONFIG, A, order_row
from sp_workflow.check_terms import CheckTermsError
from sp_workflow.daily_import import import_daily
from sp_workflow.database import DatabaseError
from sp_workflow.order_import import OrderDraft, drafts_from, import_drafts, import_orders, open_maintenance
from sp_workflow.product_write import LOCKED, NEW, OVERWRITTEN, SAME, SKIPPED, Mode, save_terms

EDITING = (Mode.CORRECT, Mode.MAINTAIN)  # 資料庫已有這檔、打開來改


def opened(pg, tmp_path, mode: Mode) -> OrderDraft:
    """依模式準備資料庫裡的 A，回傳作業人員打開（讀取）的這檔。"""
    if mode is Mode.CREATE:
        import_daily(write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "台北", 100000)], [price("AAA UW")]), pg)
        return open_maintenance(pg, A, CONFIG)
    import_drafts(drafts_from([order_row()], pg), pg, CONFIG, user="tester")
    if mode is Mode.CORRECT:
        pg.execute("UPDATE workflow.product SET locked_at = now() WHERE tdcc = %s", (A,))
    if mode is Mode.IMPORT:
        (draft,) = drafts_from([order_row()], pg)
        return draft
    return open_maintenance(pg, A, CONFIG)


def write(pg, draft: OrderDraft, mode: Mode):
    if mode is Mode.IMPORT:
        return import_drafts([draft], pg, CONFIG, user="tester").lines[0]
    return save_terms(pg, draft, CONFIG, mode, user="tester")


def column(pg, name: str):
    return pg.execute(f"SELECT {name} FROM workflow.product WHERE tdcc = %s", (A,)).fetchone()[0]


def term_records(pg) -> list[tuple]:
    return pg.execute(
        "SELECT changes, previous, locked FROM workflow.term_correction ORDER BY correction_id"
    ).fetchall()


# ---------- 共用規則 ----------


@pytest.mark.db
@pytest.mark.parametrize(
    ("mode", "issuer", "denomination"),
    [
        (Mode.IMPORT, "Barclays", Decimal(10000)),  # 起單資料為準：照設定換算
        (Mode.CORRECT, "Barclays Bank PLC", Decimal(5000)),  # 上手不改、幣別沒改面額不動
        (Mode.MAINTAIN, "Barclays Bank PLC", Decimal(5000)),
    ],
)
def test_issuer_and_denomination_stay_unless_importing(pg, tmp_path, mode, issuer, denomination) -> None:
    opened(pg, tmp_path, mode)
    pg.execute("UPDATE workflow.product SET issuer = 'Barclays Bank PLC', denomination = 5000 WHERE tdcc = %s", (A,))
    draft = opened_again(pg, mode)
    draft.values["Coupon p.a. (%)"] = Decimal(11)

    write(pg, draft, mode)

    assert (column(pg, "coupon_pct"), column(pg, "issuer"), column(pg, "denomination")) == (
        Decimal(11),
        issuer,
        denomination,
    )


def opened_again(pg, mode: Mode) -> OrderDraft:
    if mode is Mode.IMPORT:
        (draft,) = drafts_from([order_row()], pg)
        return draft
    return open_maintenance(pg, A, CONFIG)


@pytest.mark.db
@pytest.mark.parametrize("mode", [Mode.IMPORT, *EDITING])
def test_a_new_currency_takes_its_default_denomination(pg, tmp_path, mode) -> None:
    opened(pg, tmp_path, mode)
    pg.execute("UPDATE workflow.product SET denomination = 5000 WHERE tdcc = %s", (A,))
    draft = opened_again(pg, mode)
    draft.values["Currency"] = "JPY"

    write(pg, draft, mode)

    assert (column(pg, "currency"), column(pg, "denomination")) == ("JPY", Decimal(1000000))


@pytest.mark.db
@pytest.mark.parametrize("mode", [Mode.IMPORT, *EDITING])
def test_nothing_changed_writes_nothing(pg, tmp_path, mode) -> None:
    draft = opened(pg, tmp_path, mode)
    import_id = column(pg, "order_import_id")

    result = write(pg, draft, mode)

    if mode is Mode.IMPORT:
        assert result.result == SAME
    else:
        assert not result.saved and result.correction_id is None
    assert column(pg, "order_import_id") == import_id and term_records(pg) == []


@pytest.mark.db
@pytest.mark.parametrize("mode", list(Mode))
def test_every_write_leaves_a_record_with_the_previous_columns(pg, tmp_path, mode) -> None:
    draft = opened(pg, tmp_path, mode)
    if mode is Mode.CREATE:
        draft.values.update({"Currency": "USD", "Coupon p.a. (%)": Decimal(11)})
    else:
        draft.values["Coupon p.a. (%)"] = Decimal(11)

    result = write(pg, draft, mode)

    if mode is Mode.IMPORT:
        assert result.result == OVERWRITTEN
        (previous,) = pg.execute(
            "SELECT previous FROM workflow.order_import_line WHERE result = %s", (OVERWRITTEN,)
        ).fetchone()
        assert term_records(pg) == []
    else:
        assert result.saved and result.locked is (mode is Mode.CORRECT) and result.created is (mode is Mode.CREATE)
        ((changes, previous, locked),) = term_records(pg)
        assert ["Coupon p.a. (%)", None if mode is Mode.CREATE else "12.5", "11"] in changes
        assert locked is (mode is Mode.CORRECT)
    if mode is Mode.CREATE:
        assert previous is None and column(pg, "source") == "人工維護"
    else:
        assert previous["coupon_pct"] == "12.5"
    assert column(pg, "coupon_pct") == Decimal(11)
    assert draft.values["Coupon p.a. (%)"] == Decimal(11)  # 畫面換成（或補上）資料庫存的值


# ---------- 併發 ----------


def _wait_until_blocked(pg, pid: int) -> None:
    """等另一個連線卡在等鎖（`pg_stat_activity.wait_event_type` 是 Lock）。"""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        (waiting,) = pg.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", (pid,)).fetchone()
        if waiting == "Lock":
            return
        time.sleep(0.02)
    pytest.fail("匯入沒有在等鎖")


@pytest.mark.db
def test_import_waits_for_a_concurrent_lock_and_reports_locked(pg) -> None:
    """核對寫回鎖住這檔的同時匯入：匯入等鎖放開後看到已鎖住，不蓋掉剛鎖住的條件。"""
    import psycopg

    import_drafts(drafts_from([order_row()], pg), pg, CONFIG, user="tester")
    (draft,) = drafts_from([order_row(changes={"Coupon p.a. (%)": Decimal(10)})], pg)
    dsn = os.environ["SPW_TEST_DSN"]
    with psycopg.connect(dsn) as checker, psycopg.connect(dsn) as importer, ThreadPoolExecutor(1) as pool:
        try:
            checker.execute("SELECT 1 FROM workflow.product WHERE tdcc = %s FOR UPDATE", (A,))  # 同核對寫回
            checker.execute("UPDATE workflow.product SET locked_at = now() WHERE tdcc = %s", (A,))
            running = pool.submit(import_drafts, [draft], importer, CONFIG, user="tester")
            _wait_until_blocked(pg, importer.info.backend_pid)
            checker.commit()
        finally:
            checker.rollback()  # 沒等到時也放開鎖，匯入才不會一直卡住
        receipt = running.result(timeout=10)

    assert receipt.lines[0].result == LOCKED
    assert column(pg, "coupon_pct") == Decimal("12.5")


# ---------- 按一次「匯入資料庫」（ADR 0012） ----------


@pytest.mark.db
def test_importing_recalculates_and_reloads(pg) -> None:
    (draft,) = drafts_from([order_row()], pg)

    result = import_orders([draft], pg, CONFIG, user="tester")

    assert result.receipt.lines[0].result == NEW
    assert result.recalc is not None and result.recalc.line_of(A) is not None
    assert (result.recalc_error, result.reload_error) == ("", "")
    assert draft.stored is not None and draft.stored["Coupon p.a. (%)"] == Decimal("12.5")


@pytest.mark.db
def test_a_failed_recalculation_keeps_the_import(pg, monkeypatch) -> None:
    (draft,) = drafts_from([order_row()], pg)

    def broken(*args, **kwargs):
        raise CheckTermsError("找不到交易所假日表設定檔\n第二行")

    monkeypatch.setattr("sp_workflow.order_import.recalculate", broken)

    result = import_orders([draft], pg, CONFIG, user="tester")

    assert result.receipt.lines[0].result == NEW and column(pg, "coupon_pct") == Decimal("12.5")
    assert (result.recalc, result.recalc_error) == (None, "找不到交易所假日表設定檔")
    assert draft.stored is not None  # 重讀照樣做


@pytest.mark.db
def test_a_failed_reload_keeps_the_import(pg, monkeypatch) -> None:
    (draft,) = drafts_from([order_row()], pg)

    def broken(*args, **kwargs):
        raise DatabaseError("connection lost")

    monkeypatch.setattr("sp_workflow.order_import.read_stored", broken)

    result = import_orders([draft], pg, CONFIG, user="tester")

    assert result.receipt.lines[0].result == NEW and column(pg, "coupon_pct") == Decimal("12.5")
    assert result.recalc is not None and result.reload_error == "connection lost"


@pytest.mark.db
def test_a_product_created_while_importing_is_skipped_not_a_database_error(pg, monkeypatch) -> None:
    """還不存在的列 FOR UPDATE 鎖不到：讀取之後別人新增了同一檔，這檔不匯入、其他照常，不是整批出錯。"""
    (draft,) = drafts_from([order_row(changes={"Coupon p.a. (%)": Decimal(10)})], pg)
    import_drafts(drafts_from([order_row()], pg), pg, CONFIG, user="other")
    monkeypatch.setattr("sp_workflow.product_write.lock_terms", lambda conn, tdcc: None)  # 匯入時還看不到

    receipt = import_drafts([draft], pg, CONFIG, user="tester")

    assert (receipt.lines[0].result, receipt.lines[0].detail) == (
        SKIPPED,
        "讀取之後資料庫已經有這檔的商品條件，請重新讀取後再確認",
    )
    assert column(pg, "coupon_pct") == Decimal("12.5")
