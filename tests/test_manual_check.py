"""人工核對完成（#527、ADR 0032）：輸入解讀不需要資料庫；檢查、寫入、鎖住與之後的比價對真的 PostgreSQL。資料全部虛構。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from db_synth import trade, write_daily
from order_synth import CONFIG, A, order_row
from sp_workflow.check_terms import DONE, recalculate
from sp_workflow.daily_import import import_daily
from sp_workflow.database import OperatorError
from sp_workflow.manual_check import (
    LOG_ITEM,
    RESULT_FILE,
    ManualCheck,
    ManualCheckError,
    confirm,
    parse_entry,
)
from sp_workflow.observation import observe_all
from sp_workflow.product_store import read
from sp_workflow.product_terms import drafts_from, write_batch
from sp_workflow.progress import load_progress
from sp_workflow.status import Pricing, Progress
from sp_workflow.window_actions import manual_check

ISIN = "XS1234567890"
FINAL = dt.date(2027, 4, 15)  # order_synth 的 Final valuation date
FIRST = dt.date(2027, 1, 15)


# ---------- 輸入解讀 ----------


def test_dates_split_on_lines_commas_and_spaces_in_any_supported_spelling() -> None:
    entry = parse_entry(A, " xs1234567890 ", "2027-01-15\n2027/2/15, 15-Mar-27、 2027-04-15\n\n", {}, " 已核對 ")

    assert entry == ManualCheck(A, ISIN, (FIRST, dt.date(2027, 2, 15), dt.date(2027, 3, 15), FINAL), {}, "已核對")


def test_unreadable_dates_and_prices_are_all_named() -> None:
    with pytest.raises(OperatorError) as raised:
        parse_entry(A, ISIN, "2027-01-15\n明年", {1: "1,234.5", 2: "abc", 3: " "})

    assert str(raised.value) == "第 2 個比價日「明年」看不懂（寫成 2026-10-21）；UL_2 進場價「abc」不是數字"


def test_prices_drop_thousands_separators_and_skip_blanks() -> None:
    entry = parse_entry(A, ISIN, "2027-04-15", {1: "1,234.5", 2: ""})

    assert entry.initial == {1: Decimal("1234.5")}


# ---------- 資料庫：檢查、寫入、鎖住 ----------

OCT_16 = dt.date(2026, 10, 16)  # 比 Strike date 10/15 晚的第一份股價檔


def ready(pg, tmp_path: Path, tdcc: str = A, **changes) -> None:
    """起單資料與股價、交易明細都齊了，核對條件算到完成（還沒鎖住）。"""
    values = {"Upfront / NotePrice (%)": Decimal("97.85"), **changes}  # UF 2.15，和交易明細一致
    receipt = write_batch(pg, drafts_from([order_row(tdcc, values)], CONFIG, pg), CONFIG, user="tester")
    assert receipt.lines[0].imported, receipt.lines[0].detail
    prices = [
        ("AAA UW", "虛構公司", 100.5, 99.25, 98.0, 30.0, 12345.6),
        ("BBB UN", "虛構公司", 50.0, 49.0, 48.0, 30.0, 1.0),
    ]
    path = tmp_path / "daily.xlsx"
    import_daily(
        write_daily(path, [trade(tdcc, "Amy", "台北", 10000)], prices, price_date=dt.datetime(2026, 10, 16)), pg
    )
    (line,) = recalculate(pg, CONFIG, tdccs=[tdcc]).lines
    assert line.status == DONE or "VWAP" in line.reason, line.reason


def entry(**changes) -> ManualCheck:
    values = {"tdcc": A, "isin": ISIN, "autocall": (FIRST, FINAL), "initial": {}, "reason": "BNP 還不支援 DACN"}
    return ManualCheck(**{**values, **changes})


def confirmed(pg, found: ManualCheck):
    with pg.transaction():
        return confirm(pg, found, user="operator1")


def autocall_rows(pg) -> list[tuple[int, dt.date | None]]:
    return pg.execute("SELECT slot, value FROM workflow.autocall_date WHERE tdcc = %s ORDER BY slot", (A,)).fetchall()


@pytest.mark.db
def test_manual_check_writes_isin_and_autocall_dates_then_locks_and_prices(pg, tmp_path) -> None:
    ready(pg, tmp_path)

    receipt = confirmed(pg, entry())

    stored = read(pg, A)
    assert stored is not None and stored.locked_at is not None and stored.isin == ISIN
    assert autocall_rows(pg) == [(1, FIRST), (2, FINAL), *((n, None) for n in range(3, 13))], "沒用到的期別寫空白"
    assert receipt.changes == 3, "ISIN 與兩個比價日（空白的期別不算改值）"
    logs = pg.execute(
        "SELECT item, new_value, result_file, written_by FROM workflow.backfill_log WHERE tdcc = %s ORDER BY log_id",
        (A,),
    ).fetchall()
    assert [row[0] for row in logs] == ["ISIN Code", "比價日_1", "比價日_2", LOG_ITEM]
    assert logs[-1] == (LOG_ITEM, "BNP 還不支援 DACN", RESULT_FILE, "operator1")
    (result,) = [r for r in observe_all(pg, CONFIG.calendar).results if r.tdcc == A]
    assert result.status != Pricing.TO_CONFIRM, result.reason


@pytest.mark.db
def test_every_problem_is_listed_and_nothing_is_written(pg, tmp_path) -> None:
    write_batch(pg, drafts_from([order_row(A, {"Issue date": None})], CONFIG, pg), CONFIG, user="tester")
    recalculate(pg, CONFIG)  # 沒有交易明細、沒有股價：不是完成

    with pytest.raises(ManualCheckError) as raised:
        confirmed(pg, entry(isin="XS123", autocall=(FINAL, FIRST)))

    problems = raised.value.problems
    assert problems[0].startswith("核對條件不是完成（")
    assert problems[1:] == (
        "沒有 Issue date（鎖住後不能改，先在人工維護補上存檔）",
        "ISIN「XS123」格式不對（2 碼英文＋9 碼英數＋1 碼數字）",
        "比價日要由小到大、不重複",
        f"最後一個比價日 {FIRST} 和 Final valuation date {FINAL} 不同",
    )
    stored = read(pg, A)
    assert stored is not None and stored.locked_at is None and stored.isin is None
    assert autocall_rows(pg) == []


@pytest.mark.db
def test_more_than_twelve_autocall_dates_or_none_are_rejected(pg, tmp_path) -> None:
    ready(pg, tmp_path)
    many = tuple(FINAL - dt.timedelta(days=n) for n in range(13, -1, -1))

    with pytest.raises(ManualCheckError) as too_many:
        confirmed(pg, entry(autocall=many))
    with pytest.raises(ManualCheckError) as none:
        confirmed(pg, entry(autocall=()))

    assert too_many.value.problems == ("比價日有 14 期，最多 12 期",)
    assert none.value.problems == ("沒有比價日",)


@pytest.mark.db
def test_a_locked_product_cannot_be_checked_by_hand_again(pg, tmp_path) -> None:
    ready(pg, tmp_path)
    confirmed(pg, entry())

    with pytest.raises(ManualCheckError) as raised:
        confirmed(pg, entry(isin="XS0000000000"))

    assert raised.value.problems == ("已經鎖住（核對過了），不能再人工核對",)
    assert str(raised.value) == f"{A} 沒有鎖住：已經鎖住（核對過了），不能再人工核對"


@pytest.mark.db
def test_vwap_products_need_initial_prices_and_derive_the_others(pg, tmp_path) -> None:
    ready(pg, tmp_path, 期初價格="VWAP")

    with pytest.raises(ManualCheckError) as raised:
        confirmed(pg, entry(initial={1: Decimal(50)}))
    confirmed(pg, entry(initial={1: Decimal(50), 2: Decimal("20.1")}))

    assert raised.value.problems == ("UL_2 BBB UN 期初定價是 VWAP，要輸入大於 0 的進場價",)
    stored = read(pg, A)
    assert stored is not None
    # 執行價 80%、KI 65%（EKI）、KO 100%
    assert [(p.seq, p.initial, p.strike, p.ki, p.ko) for p in stored.prices] == [
        (1, Decimal(50), Decimal(40), Decimal("32.5"), Decimal(50)),
        (2, Decimal("20.1"), Decimal("16.08"), Decimal("13.065"), Decimal("20.1")),
    ]


@pytest.mark.db
def test_the_window_action_locks_then_prices_everything(pg, pg_settings, tmp_path) -> None:
    ready(pg, tmp_path)

    result = manual_check(pg_settings, entry(), CONFIG)

    assert result.receipt.tdcc == A and result.observation.receipt is not None
    assert [r.tdcc for r in result.observation.receipt.results] == [A]
    stored = read(pg, A)
    assert stored is not None and stored.locked_at is not None


@pytest.mark.db
def test_the_window_action_rejects_without_writing(pg, pg_settings, tmp_path) -> None:
    ready(pg, tmp_path)

    with pytest.raises(ManualCheckError, match="ISIN「XS1」格式不對"):
        manual_check(pg_settings, entry(isin="XS1"), CONFIG)

    stored = read(pg, A)
    assert stored is not None and stored.locked_at is None


def test_entered_initial_prices_are_rounded_like_calculated_ones() -> None:
    assert parse_entry(A, ISIN, "2027-04-15", {1: "12.345678"}).initial == {1: Decimal("12.3457")}


@pytest.mark.db
def test_terms_changed_after_opening_are_not_locked(pg, tmp_path) -> None:
    """打開之後重新匯入起單資料（或別人存檔）改了條件：不寫、不鎖住，請重新載入。"""
    ready(pg, tmp_path)
    opened = read(pg, A)
    assert opened is not None
    pg.execute("UPDATE workflow.product SET coupon_pct = 11 WHERE tdcc = %s", (A,))

    with pytest.raises(ManualCheckError) as raised:
        confirmed(pg, entry(seen=dict(opened.fields)))
    unchanged = confirmed(pg, entry(seen=dict(read(pg, A).fields)))

    assert raised.value.problems == ("資料庫裡這檔的條件在打開之後改過了，請重新載入",)
    assert unchanged.tdcc == A


@pytest.mark.db
def test_a_manually_checked_product_waits_for_mail_on_the_progress_page(pg, tmp_path) -> None:
    """沒跑過「4 核對」、沒有說明書的核對結果：人工核對那一筆算核對結果，鎖住後是待寄信。"""
    ready(pg, tmp_path)
    confirmed(pg, entry())

    (row,) = [r for r in load_progress(pg, CONFIG, dt.date(2026, 11, 9)) if r.tdcc == A]

    assert (row.progress, row.reason) == (Progress.TO_MAIL, "")
