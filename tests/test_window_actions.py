"""視窗按鈕背後的動作（#125）：自己開連線、決定 transaction 與容錯。對真的 PostgreSQL 跑，資料全部虛構。

重點是 ADR 0012：匯入成功、之後的重算（或重讀）失敗時，匯入照樣寫進資料庫，原因放在結果裡；
另一條連線（`pg`）看得到匯入，才算真的 commit 了。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from db_synth import csv_price, price, seed_mailbox, trade, write_daily, write_price_csv
from fcn_checker.check_config import ConfigPaths
from order_synth import CONFIG, SHEET, A, drafts_of, order_row, sheet_line, write_sheet
from sp_workflow import window_actions
from sp_workflow.check_terms import CheckTermsError
from sp_workflow.database import DatabaseError, SchemaOutdatedError
from sp_workflow.product_terms import write_batch
from sp_workflow.status import ImportResult
from sp_workflow.window_actions import (
    import_close_history,
    import_order_sheet,
    observe_products,
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
def test_a_failed_observation_keeps_the_price_csv(pg, pg_settings, tmp_path: Path, monkeypatch) -> None:
    source = write_price_csv(tmp_path / "MarketData_20261015.csv", [csv_price("AAA UW")])

    def broken(conn, calendar):
        raise RuntimeError("壞掉了")

    monkeypatch.setattr(window_actions, "observe_all", broken)

    result = run_price_csv(pg_settings, source, PATHS)

    assert result.observation.receipt is None and "比價程式出錯（RuntimeError：壞掉了）" in result.observation.error
    assert count(pg, "workflow.daily_price") == 1 and count(pg, "workflow.close_price") == 1, "匯入照樣保留"


@pytest.mark.db
def test_close_history_is_observed_after_the_import(pg, pg_settings, tmp_path: Path) -> None:
    from test_close_prices import write_history

    source = write_history(tmp_path / "HP.xlsx", [["AAA", "AAA UW Equity", "虛構", 10, 11, 12, 13, 14]])

    result = import_close_history(pg_settings, source, PATHS)

    assert result.receipt.summary.stored > 0
    assert result.observation.receipt is not None and result.observation.receipt.through == dt.date(2026, 10, 16)


@pytest.mark.db
def test_price_csv_is_recalculated(pg, pg_settings, tmp_path: Path) -> None:
    source = write_price_csv(tmp_path / "MarketData_20261015.csv", [csv_price("AAA UW")])

    result = run_price_csv(pg_settings, source, PATHS)

    assert result.recalc.receipt is not None and not result.recalc.error
    assert result.receipt.closes is not None and result.receipt.closes.stored == 1, "讀得到假日表時同時存收盤價"
    assert result.observation.receipt is not None and result.observation.receipt.through == dt.date(2026, 10, 15)


def test_a_price_csv_without_holidays_stores_no_closes(pg, pg_settings, tmp_path: Path) -> None:
    source = write_price_csv(tmp_path / "MarketData_20261015.csv", [csv_price("AAA UW")])

    result = run_price_csv(pg_settings, source, no_holidays(tmp_path))

    assert result.receipt.closes is None and count(pg, "workflow.close_price") == 0
    assert result.observation.receipt is None and "讀不到交易所假日表" in result.observation.error


@pytest.mark.db
def test_reobserve_needs_the_holiday_calendar(pg, pg_settings, tmp_path: Path) -> None:
    with pytest.raises(CheckTermsError):
        observe_products(pg_settings, no_holidays(tmp_path))

    receipt = observe_products(pg_settings, PATHS)

    assert receipt.through is None and receipt.results == ()


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
def test_reading_with_an_outdated_schema_still_reads_the_sheet_and_keeps_the_login(pg, pg_settings, tmp_path) -> None:
    sheet = write_sheet(tmp_path / SHEET, [sheet_line()])
    pg.execute("DELETE FROM public.spw_schema_version")

    result = read_order_sheet(pg_settings, A, sheet, PATHS)

    assert len(result.drafts) == 1 and result.drafts[0].stored is None
    assert "spw-db-migrate" in result.database_note and result.database_error is None, "版本不對不是登入問題（#200）"


@pytest.mark.db
@pytest.mark.parametrize("action", ["load_progress_rows", "observe_products", "load_exit_list", "check_missing"])
def test_actions_refuse_an_outdated_schema_with_the_same_message(pg, pg_settings, action) -> None:
    """版本只在 `connect` 檢查一次（#200）：每個要連資料庫的動作都丟同一句話。"""
    pg.execute("DELETE FROM public.spw_schema_version")
    run = getattr(window_actions, action)
    args = {"load_exit_list": (pg_settings, PATHS, None)}.get(action, (pg_settings, PATHS))

    with pytest.raises(SchemaOutdatedError) as info:
        run(*args)
    assert str(info.value) == "資料庫的資料表還沒建立或不是最新版本，請先執行 spw-db-migrate。"


@pytest.mark.db
def test_reading_brings_the_stored_values(pg, pg_settings, tmp_path: Path) -> None:
    write_batch(pg, drafts_of([order_row()], pg), CONFIG, user="tester")
    sheet = write_sheet(tmp_path / SHEET, [sheet_line(changes={"Coupon p.a. (%)": Decimal(11)})])

    result = read_order_sheet(pg_settings, A, sheet, PATHS)

    assert (result.database_note, result.database_error) == ("", None)
    assert result.drafts[0].stored is not None and result.drafts[0].stored["Coupon p.a. (%)"] == Decimal("12.5")


# ---------- 匯入起單資料：匯入 → 重算 → 重讀，三段各自的容錯 ----------


@pytest.mark.db
def test_importing_the_sheet_commits_then_recalculates_and_reloads(pg, pg_settings) -> None:
    (draft,) = drafts_of([order_row()], pg)

    result = import_order_sheet(pg_settings, [draft], CONFIG)

    assert result.receipt.lines[0].result == ImportResult.NEW and coupon(pg) == Decimal("12.5")
    assert result.recalc is not None and result.recalc.line_of(A) is not None, "重算看得到剛匯入的那一檔"
    assert draft.stored is not None and draft.stored["Coupon p.a. (%)"] == Decimal("12.5"), "重讀是匯入後的值"
    assert (result.recalc_error, result.reload_error) == ("", "")


@pytest.mark.db
def test_a_database_error_while_recalculating_keeps_the_sheet_import(pg, pg_settings, monkeypatch) -> None:
    (draft,) = drafts_of([order_row()], pg)
    broken_recalculation(monkeypatch)

    result = import_order_sheet(pg_settings, [draft], CONFIG)

    assert result.recalc is None and "no_such_table" in result.recalc_error
    assert draft.stored is not None and result.reload_error == "", "重算失敗之後照樣重讀"
    assert coupon(pg) == Decimal("12.5")


@pytest.mark.db
def test_a_recalculation_error_shows_its_first_line_and_keeps_the_sheet_import(pg, pg_settings, monkeypatch) -> None:
    (draft,) = drafts_of([order_row()], pg)

    def broken(*args, **kwargs):
        raise CheckTermsError("找不到交易所假日表設定檔\n第二行")

    monkeypatch.setattr("sp_workflow.window_actions.recalculate", broken)

    result = import_order_sheet(pg_settings, [draft], CONFIG)

    assert (result.recalc, result.recalc_error) == (None, "找不到交易所假日表設定檔")
    assert result.receipt.lines[0].result == ImportResult.NEW and coupon(pg) == Decimal("12.5")
    assert draft.stored is not None, "重算失敗之後照樣重讀"


@pytest.mark.db
def test_a_failed_reload_keeps_the_sheet_import_and_the_recalculation(pg, pg_settings, monkeypatch) -> None:
    (draft,) = drafts_of([order_row()], pg)

    def broken(conn, tdcc):
        conn.execute("SELECT * FROM workflow.no_such_table")

    monkeypatch.setattr("sp_workflow.window_actions.read", broken)

    result = import_order_sheet(pg_settings, [draft], CONFIG)

    assert result.recalc is not None and "no_such_table" in result.reload_error
    assert coupon(pg) == Decimal("12.5")
    assert pg.execute("SELECT count(*) FROM workflow.check_terms WHERE tdcc = %s", (A,)).fetchone()[0] == 1
