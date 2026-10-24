"""商品條件寫入（#124、#203）：起單資料匯入、已鎖住更正、人工維護共用的規則與併發。

按一次「匯入資料庫」之後重算、重讀的容錯（ADR 0012）在 test_window_actions。
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
from order_synth import CONFIG, A, drafts_of, open_terms, order_row
from sp_workflow.daily_import import import_daily
from sp_workflow.product_terms import Draft, Mode, write, write_batch
from sp_workflow.status import ImportResult

EDITING = (Mode.CORRECT, Mode.MAINTAIN)  # 資料庫已有這檔、打開來改


def opened(pg, tmp_path, mode: Mode) -> Draft:
    """依模式準備資料庫裡的 A，回傳作業人員打開（讀取）的這檔；草稿的 `mode` 就是要測的模式。"""
    if mode is Mode.CREATE:
        import_daily(write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "台北", 100000)], [price("AAA UW")]), pg)
        draft = open_terms(pg, A)
    else:
        write_batch(pg, drafts_of([order_row()], pg), CONFIG, user="tester")
        if mode is Mode.CORRECT:
            pg.execute("UPDATE workflow.product SET locked_at = now() WHERE tdcc = %s", (A,))
        (draft,) = drafts_of([order_row()], pg) if mode is Mode.IMPORT else (open_terms(pg, A),)
    assert draft.mode is mode
    return draft


def save(pg, draft: Draft):
    """起單資料整批匯入（這一檔的結果），更正與人工維護存一檔。"""
    if draft.mode is Mode.IMPORT:
        return write_batch(pg, [draft], CONFIG, user="tester").lines[0]
    return write(pg, draft, CONFIG, user="tester")


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

    save(pg, draft)

    assert (column(pg, "coupon_pct"), column(pg, "issuer"), column(pg, "denomination")) == (
        Decimal(11),
        issuer,
        denomination,
    )


def opened_again(pg, mode: Mode) -> Draft:
    if mode is Mode.IMPORT:
        (draft,) = drafts_of([order_row()], pg)
        return draft
    return open_terms(pg, A)


@pytest.mark.db
@pytest.mark.parametrize("mode", [Mode.IMPORT, *EDITING])
def test_a_new_currency_takes_its_default_denomination(pg, tmp_path, mode) -> None:
    opened(pg, tmp_path, mode)
    pg.execute("UPDATE workflow.product SET denomination = 5000 WHERE tdcc = %s", (A,))
    draft = opened_again(pg, mode)
    draft.values["Currency"] = "JPY"

    save(pg, draft)

    assert (column(pg, "currency"), column(pg, "denomination")) == ("JPY", Decimal(1000000))


@pytest.mark.db
@pytest.mark.parametrize("mode", [Mode.IMPORT, *EDITING])
def test_nothing_changed_writes_nothing(pg, tmp_path, mode) -> None:
    draft = opened(pg, tmp_path, mode)
    import_id = column(pg, "order_import_id")

    result = save(pg, draft)

    if mode is Mode.IMPORT:
        assert result.result == ImportResult.SAME
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

    result = save(pg, draft)

    if mode is Mode.IMPORT:
        assert result.result == ImportResult.OVERWRITTEN
        (previous,) = pg.execute(
            "SELECT previous FROM workflow.order_import_line WHERE result = %s", (ImportResult.OVERWRITTEN,)
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

    write_batch(pg, drafts_of([order_row()], pg), CONFIG, user="tester")
    (draft,) = drafts_of([order_row(changes={"Coupon p.a. (%)": Decimal(10)})], pg)
    dsn = os.environ["SPW_TEST_DSN"]
    with psycopg.connect(dsn) as checker, psycopg.connect(dsn) as importer, ThreadPoolExecutor(1) as pool:
        try:
            checker.execute("SELECT 1 FROM workflow.product WHERE tdcc = %s FOR UPDATE", (A,))  # 同核對寫回
            checker.execute("UPDATE workflow.product SET locked_at = now() WHERE tdcc = %s", (A,))
            running = pool.submit(write_batch, importer, [draft], CONFIG, user="tester")
            _wait_until_blocked(pg, importer.info.backend_pid)
            checker.commit()
        finally:
            checker.rollback()  # 沒等到時也放開鎖，匯入才不會一直卡住
        receipt = running.result(timeout=10)

    assert receipt.lines[0].result == ImportResult.LOCKED
    assert column(pg, "coupon_pct") == Decimal("12.5")


@pytest.mark.db
def test_a_product_created_while_importing_is_skipped_not_a_database_error(pg, monkeypatch) -> None:
    """還不存在的列 FOR UPDATE 鎖不到：讀取之後別人新增了同一檔，這檔不匯入、其他照常，不是整批出錯。"""
    (draft,) = drafts_of([order_row(changes={"Coupon p.a. (%)": Decimal(10)})], pg)
    write_batch(pg, drafts_of([order_row()], pg), CONFIG, user="other")
    monkeypatch.setattr("sp_workflow.product_terms.read", lambda conn, tdcc, *, lock=False: None)  # 匯入時還看不到

    receipt = write_batch(pg, [draft], CONFIG, user="tester")

    assert (receipt.lines[0].result, receipt.lines[0].detail) == (
        ImportResult.SKIPPED,
        "讀取之後資料庫已經有這檔的商品條件，請重新讀取後再確認",
    )
    assert column(pg, "coupon_pct") == Decimal("12.5")
