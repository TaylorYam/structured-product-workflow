"""視窗按鈕背後的動作（#125）：自己開連線、決定 transaction 與容錯。對真的 PostgreSQL 跑，資料全部虛構。

重點是 ADR 0012：匯入成功、之後的重算（或重讀）失敗時，匯入照樣寫進資料庫，原因放在結果裡；
另一條連線（`pg`）看得到匯入，才算真的 commit 了。
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from db_synth import csv_price, price, seed_mailbox, trade, write_daily, write_price_csv
from fcn_checker.check_config import ConfigPaths
from order_synth import CONFIG, SHEET, A, order_row, sheet_line, write_sheet
from sp_workflow.check_terms import CheckTermsError
from sp_workflow.database import DatabaseError
from sp_workflow.order_import import drafts_from, import_drafts
from sp_workflow.product_write import NEW
from sp_workflow.window_actions import (
    import_close_history,
    import_order_sheet,
    read_order_sheet,
    run_daily_import,
    run_price_csv,
)

PATHS = ConfigPaths.with_fallback(Path(__file__).resolve().parents[1] / "config" / "review_standard.toml")


def count(pg, table: str) -> int:
    return pg.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def coupon(pg):
    return pg.execute("SELECT coupon_pct FROM workflow.product WHERE tdcc = %s", (A,)).fetchone()[0]


def no_holidays(tmp_path: Path) -> ConfigPaths:
    """交易所假日表不存在：匯入照常，重算時找不到假日表。"""
    return replace(PATHS, exchange_holidays=tmp_path / "沒有這個檔.toml")


def broken_recalculation(monkeypatch) -> None:
    """重算時資料庫出錯（查不存在的表），錯誤發生在重算自己的 transaction 裡。"""

    def broken(conn, where, params):
        conn.execute("SELECT * FROM workflow.no_such_table")

    monkeypatch.setattr("sp_workflow.check_terms._products", broken)


# ---------- 每日匯入、回補股價：匯入 → 重算 ----------


@pytest.mark.db
def test_daily_import_is_committed_then_recalculated(pg, pg_settings, tmp_path: Path) -> None:
    seed_mailbox(pg)
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 100000)], [price("AAA UW")])

    result = run_daily_import(pg_settings, daily, PATHS, prices=True)

    assert result.receipt.fcn_tdccs == (A,)
    assert result.recalc.receipt is not None and result.recalc.calendar is not None and not result.recalc.error
    assert count(pg, "workflow.trade_row") == 1


@pytest.mark.db
def test_trades_only_daily_import_leaves_prices_alone(pg, pg_settings, tmp_path: Path) -> None:
    seed_mailbox(pg)
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 100000)], [price("AAA UW")])

    result = run_daily_import(pg_settings, daily, PATHS, prices=False)

    assert result.receipt.prices is None and count(pg, "workflow.daily_price") == 0


@pytest.mark.db
def test_a_missing_holiday_file_keeps_the_daily_import(pg, pg_settings, tmp_path: Path) -> None:
    seed_mailbox(pg)
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 100000)], [price("AAA UW")])

    result = run_daily_import(pg_settings, daily, no_holidays(tmp_path), prices=True)

    assert result.recalc.receipt is None and result.recalc.calendar is None
    assert "沒有這個檔.toml" in result.recalc.error and "\n" not in result.recalc.error
    assert count(pg, "workflow.trade_row") == 1


@pytest.mark.db
def test_a_database_error_while_recalculating_keeps_the_daily_import(
    pg, pg_settings, tmp_path: Path, monkeypatch
) -> None:
    seed_mailbox(pg)
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 100000)], [price("AAA UW")])
    broken_recalculation(monkeypatch)

    result = run_daily_import(pg_settings, daily, PATHS, prices=True)

    assert result.recalc.receipt is None and "no_such_table" in result.recalc.error
    assert count(pg, "workflow.trade_row") == 1


@pytest.mark.db
def test_a_failed_recalculation_keeps_the_price_csv(pg, pg_settings, tmp_path: Path) -> None:
    source = write_price_csv(tmp_path / "MarketData_20261015.csv", [csv_price("AAA UW")])

    result = run_price_csv(pg_settings, source, no_holidays(tmp_path))

    assert result.receipt.prices.count == 1 and result.recalc.receipt is None and result.recalc.error
    assert count(pg, "workflow.daily_price") == 1


@pytest.mark.db
def test_price_csv_is_recalculated(pg, pg_settings, tmp_path: Path) -> None:
    source = write_price_csv(tmp_path / "MarketData_20261015.csv", [csv_price("AAA UW")])

    result = run_price_csv(pg_settings, source, PATHS)

    assert result.recalc.receipt is not None and not result.recalc.error
    assert result.receipt.closes is not None and result.receipt.closes.stored == 1, "讀得到假日表時同時存收盤價"


def test_a_price_csv_without_holidays_stores_no_closes(pg, pg_settings, tmp_path: Path) -> None:
    source = write_price_csv(tmp_path / "MarketData_20261015.csv", [csv_price("AAA UW")])

    result = run_price_csv(pg_settings, source, no_holidays(tmp_path))

    assert result.receipt.closes is None and count(pg, "workflow.close_price") == 0


def test_close_history_needs_the_holiday_calendar(pg, pg_settings, tmp_path: Path) -> None:
    with pytest.raises(CheckTermsError):
        import_close_history(pg_settings, tmp_path / "HP.xlsx", no_holidays(tmp_path))

    assert count(pg, "workflow.close_price_import") == 0


# ---------- 讀起單建檔：能連就帶入資料庫的值，連不上就退回 ----------


def test_reading_without_a_login_does_not_bring_database_values(tmp_path: Path) -> None:
    sheet = write_sheet(tmp_path / SHEET, [sheet_line()])

    result = read_order_sheet(None, A, sheet, PATHS)

    assert [row.tdcc for row in result.rows] == [A] and len(result.drafts) == 1
    assert result.drafts[0].stored is None
    assert (result.database_note, result.database_error) == ("沒有登入資料庫，沒有帶入資料庫的值", None)


@pytest.mark.db
def test_reading_when_the_database_is_unreachable_still_reads_the_sheet(pg, pg_settings, tmp_path: Path) -> None:
    sheet = write_sheet(tmp_path / SHEET, [sheet_line()])
    unreachable = replace(pg_settings, port="1")

    result = read_order_sheet(unreachable, A, sheet, PATHS)

    assert len(result.drafts) == 1 and result.drafts[0].stored is None
    assert isinstance(result.database_error, DatabaseError)
    assert result.database_note == f"資料庫沒有連上，沒有帶入資料庫的值（{result.database_error}）"


@pytest.mark.db
def test_reading_brings_the_stored_values(pg, pg_settings, tmp_path: Path) -> None:
    import_drafts(drafts_from([order_row()], pg), pg, CONFIG, user="tester")
    sheet = write_sheet(tmp_path / SHEET, [sheet_line(changes={"Coupon p.a. (%)": Decimal(11)})])

    result = read_order_sheet(pg_settings, A, sheet, PATHS)

    assert (result.database_note, result.database_error) == ("", None)
    assert result.drafts[0].stored is not None and result.drafts[0].stored["Coupon p.a. (%)"] == Decimal("12.5")


# ---------- 匯入起單資料：匯入 → 重算 → 重讀，三段各自的容錯 ----------


@pytest.mark.db
def test_importing_the_sheet_commits_then_recalculates_and_reloads(pg, pg_settings) -> None:
    (draft,) = drafts_from([order_row()], pg)

    result = import_order_sheet(pg_settings, [draft], CONFIG)

    assert result.receipt.lines[0].result == NEW and coupon(pg) == Decimal("12.5")
    assert result.recalc is not None and result.recalc.line_of(A) is not None, "重算看得到剛匯入的那一檔"
    assert draft.stored is not None and draft.stored["Coupon p.a. (%)"] == Decimal("12.5"), "重讀是匯入後的值"
    assert (result.recalc_error, result.reload_error) == ("", "")


@pytest.mark.db
def test_a_database_error_while_recalculating_keeps_the_sheet_import(pg, pg_settings, monkeypatch) -> None:
    (draft,) = drafts_from([order_row()], pg)
    broken_recalculation(monkeypatch)

    result = import_order_sheet(pg_settings, [draft], CONFIG)

    assert result.recalc is None and "no_such_table" in result.recalc_error
    assert draft.stored is not None and result.reload_error == "", "重算失敗之後照樣重讀"
    assert coupon(pg) == Decimal("12.5")


@pytest.mark.db
def test_a_failed_reload_keeps_the_sheet_import_and_the_recalculation(pg, pg_settings, monkeypatch) -> None:
    (draft,) = drafts_from([order_row()], pg)

    def broken(conn, tdcc):
        conn.execute("SELECT * FROM workflow.no_such_table")

    monkeypatch.setattr("sp_workflow.order_import.read_stored", broken)

    result = import_order_sheet(pg_settings, [draft], CONFIG)

    assert result.recalc is not None and "no_such_table" in result.reload_error
    assert coupon(pg) == Decimal("12.5")
    assert pg.execute("SELECT count(*) FROM workflow.check_terms WHERE tdcc = %s", (A,)).fetchone()[0] == 1
