"""核對讀資料庫（#68、ADR 0013）：資料庫產生的參考條件表與 Excel 核對結果相同、查不到或條件未完成的 TDCC、
回填寫回並鎖住、人工放行改單位面額、VWAP 價格、寫回失敗可以重寫。

資料庫測試對真的 PostgreSQL（`pg`，沒有設定 SPW_TEST_DSN 時略過）；PANEL 工作階段的寫回失敗用假的資料庫來源，不需要資料庫。
只用合成資料（tests/checker/barc_synth.py）。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from barc_synth import SYNTH_ISIN, Spec, build_pdf
from fcn_checker.batch import Category, check_batch, preview_batch
from fcn_checker.orders.reference import load_reference_sheet
from fcn_checker.panel_workflow import PanelSession
from fcn_checker.saving import run_batch, save_batch
from harness import CONFIG, cli_root, with_iis
from reference_synth import build_reference_sheet, issuer_value, price, reference_row
from sp_workflow.check_source import WorkflowDatabase, export_reference, write_back

NOW = dt.datetime(2030, 2, 3, 4, 5, 6)
FMT = CONFIG.reference_format
OTHER = "029199990002"


def seed(pg, spec: Spec, *, pricing="收盤價", denomination=None, status="完成", reason=None) -> None:
    """像起單資料匯入＋算出核對條件（#86、#88）那樣寫一檔商品，條件和合成說明書一致。"""
    (import_id,) = pg.execute(
        "INSERT INTO workflow.order_import (imported_by, summary) VALUES ('tester', '{}') RETURNING import_id"
    ).fetchone()
    tickers = [u.ticker for u in spec.underlyings] + [None] * (5 - len(spec.underlyings))
    product = {
        "tdcc": spec.product_code,
        "product": "FCN",
        "issuer": issuer_value(spec.issuer),
        "currency": spec.ccy,
        "trade_date": spec.trade_date,
        "strike_date": spec.trade_date,
        "final_valuation_date": spec.final_date,
        "maturity_date": spec.maturity_date,
        "initial_pricing": pricing,
        "non_call_months": spec.first_callable,
        **{f"underlying_{i}": t for i, t in enumerate(tickers, 1)},
        "strike_pct": spec.strike,
        "ko_freq": spec.ko_obs,
        "ko_memory": "Y" if spec.memory else "N",
        "ko_type": "Daily Memory",
        "ko_pct": spec.ko,
        "coupon_pct": spec.annual,
        "upfront_pct": Decimal("1.59"),
        "tenor_months": spec.tenor,
        "ki_freq": "-",
        "barrier_type": "NONE",
        "ki_pct": None,
        "denomination": denomination or spec.denom,
        "status_event": "在庫",
        "source": "Order",
        "order_import_id": import_id,
    }
    pg.execute(
        f"INSERT INTO workflow.product ({', '.join(product)}) VALUES ({', '.join(['%s'] * len(product))})",
        tuple(product.values()),
    )
    pg.execute(
        "INSERT INTO workflow.check_terms (tdcc, status, reason) VALUES (%s, %s, %s)",
        (spec.product_code, status, reason),
    )
    vwap = pricing == "VWAP"
    for i, u in enumerate(spec.underlyings, 1):
        prices = (None,) * 4 if vwap else (u.initial, price(u.initial, spec.strike), None, price(u.initial, spec.ko))
        pg.execute(
            "INSERT INTO workflow.underlying_price (tdcc, seq, code, initial_price, strike_price, ki_price, ko_price)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (spec.product_code, i, u.ticker, *prices),
        )
    pg.execute(
        "INSERT INTO workflow.branch_allocation (tdcc, seq, branch, amount) VALUES (%s, 1, '台北', 1000000)",
        (spec.product_code,),
    )


def pdfs_of(tmp_path: Path, *specs: Spec) -> list[Path]:
    return with_iis([build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in specs])


def export(pg, tmp_path: Path, pdfs, name="資料庫.xlsx") -> tuple[Path, dict[str, str]]:
    from fcn_checker.identification import file_code

    path = tmp_path / name
    return path, export_reference(pg, [file_code(p) for p in pdfs], FMT, path)


def run_from_database(pg, tmp_path: Path, pdfs, *, name="資料庫.xlsx"):
    sheet, absent = export(pg, tmp_path, pdfs, name)
    return run_batch(CONFIG, sheet, pdfs, tmp_path / "reports", root=tmp_path, now=NOW, absent=absent)


def by_name(outcome) -> dict[str, object]:
    return {i.term_sheet.name: i for i in outcome.items}


def verdicts(outcome) -> list[tuple[str, Category, tuple[str, ...]]]:
    return sorted((i.term_sheet.name, i.category, i.problem_messages) for i in outcome.items)


# ---------- 讀到的條件與 Excel 相同 ----------


@pytest.mark.db
def test_database_rows_read_the_same_as_the_excel_reference_sheet(pg, tmp_path) -> None:
    spec = Spec()
    seed(pg, spec)
    pdfs = pdfs_of(tmp_path, spec)
    db_sheet, absent = export(pg, tmp_path, pdfs)
    excel_sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])

    (db_row,) = load_reference_sheet(db_sheet, FMT).rows
    (xl_row,) = load_reference_sheet(excel_sheet, FMT).rows

    assert absent == {}
    keys = sorted(set(FMT.columns.values()))
    assert {k: db_row.get(k).value for k in keys} == {k: xl_row.get(k).value for k in keys}
    assert db_row.issuer.value == xl_row.issuer.value

    from_db = check_batch(preview_batch(CONFIG, db_sheet, pdfs, absent=absent))
    from_excel = check_batch(preview_batch(CONFIG, excel_sheet, pdfs))
    assert verdicts(from_db) == verdicts(from_excel)
    assert {c for _, c, _ in verdicts(from_db)} == {Category.PASSED}


# ---------- 查不到、條件未完成 ----------


@pytest.mark.db
def test_missing_or_unfinished_products_need_review_and_cannot_be_released(pg, tmp_path) -> None:
    ready, waiting, missing = Spec(), Spec(product_code=OTHER), Spec(product_code="029199990003")
    seed(pg, ready)
    seed(pg, waiting, status="等股價", reason="還沒有取價日比 Strike date 2030-01-07 晚的股價檔")

    outcome, _ = run_from_database(pg, tmp_path, pdfs_of(tmp_path, ready, waiting, missing))

    items = by_name(outcome)
    assert items[f"{ready.product_code}_TS.pdf"].category == Category.PASSED, "同批其他照常核對"
    for code, reason in (
        (OTHER, "資料庫的核對條件還沒完成：等股價（還沒有取價日比 Strike date 2030-01-07 晚的股價檔）"),
        ("029199990003", "資料庫沒有這檔商品（還沒匯入起單資料），不核對"),
    ):
        for kind in ("TS", "IIS"):
            item = items[f"{code}_{kind}.pdf"]
            assert item.category == Category.REVIEW
            assert any(reason in m for m in item.problem_messages), item.problem_messages
            assert item.release_problem, "不能人工放行"


# ---------- 寫回 ----------


@pytest.mark.db
def test_passing_products_are_backfilled_and_locked(pg, tmp_path) -> None:
    spec = Spec()
    seed(pg, spec)
    pdfs = pdfs_of(tmp_path, spec)
    outcome, receipt = run_from_database(pg, tmp_path, pdfs)

    result = write_back(pg, outcome, receipt, FMT, user="operator1")

    assert (result.locked, result.results) == ((spec.product_code,), 2)
    isin, issue_date, locked_at = pg.execute(
        "SELECT isin, issue_date, locked_at FROM workflow.product WHERE tdcc = %s", (spec.product_code,)
    ).fetchone()
    assert (isin, issue_date) == (SYNTH_ISIN, spec.issue_date) and locked_at is not None
    dates = dict(pg.execute("SELECT slot, value FROM workflow.autocall_date WHERE tdcc = %s", (spec.product_code,)))
    assert len(dates) == 12 and max(d for d in dates.values() if d) == spec.final_date
    kinds = pg.execute("SELECT kind, category, filled, checked_by FROM workflow.check_result ORDER BY kind").fetchall()
    assert kinds == [("投資人須知", "通過", False, "operator1"), ("說明書", "通過", True, "operator1")]
    logged = {item for (item,) in pg.execute("SELECT item FROM workflow.backfill_log")}
    assert {"ISIN Code", "發行日"} <= logged

    # 鎖住後再核對：從資料庫讀到回填過的值，沒有要回填的格子，也不再寫回商品資料
    again, again_receipt = run_from_database(pg, tmp_path, pdfs, name="資料庫2.xlsx")
    (ts,) = [i for i in again.items if i.term_sheet.name.endswith("_TS.pdf")]
    assert ts.category == Category.PASSED
    assert not [d for d in ts.report.backfill if d.action.value in ("fill", "overwrite")]
    second = write_back(pg, again, again_receipt, FMT, user="operator1")
    assert (second.locked, second.already_locked) == ((), (spec.product_code,))


@pytest.mark.db
def test_released_denomination_takes_the_term_sheet_value_and_keeps_the_old_one(pg, tmp_path) -> None:
    spec = Spec()
    seed(pg, spec, denomination=spec.denom * 2)
    sheet, absent = export(pg, tmp_path, pdfs_of(tmp_path, spec))
    outcome = check_batch(preview_batch(CONFIG, sheet, pdfs_of(tmp_path, spec), absent=absent))
    assert {i.category for i in outcome.items} != {Category.PASSED}
    outcome.release(*[i for i in outcome.items if i.category != Category.PASSED])
    receipt = save_batch(outcome, tmp_path / "reports", root=tmp_path, now=NOW)

    write_back(pg, outcome, receipt, FMT, user="operator1")

    (denomination,) = pg.execute(
        "SELECT denomination FROM workflow.product WHERE tdcc = %s", (spec.product_code,)
    ).fetchone()
    assert denomination == spec.denom
    log = pg.execute("SELECT old_value, new_value FROM workflow.backfill_log WHERE item = '單位面額'").fetchall()
    assert [(Decimal(old), Decimal(new)) for old, new in log] == [(Decimal(spec.denom * 2), Decimal(spec.denom))]
    (category,) = pg.execute("SELECT category FROM workflow.check_result WHERE kind = '說明書'").fetchone()
    assert category == "人工放行"


@pytest.mark.db
def test_vwap_prices_from_the_term_sheet_are_written_back(pg, tmp_path) -> None:
    spec = Spec()
    seed(pg, spec, pricing="VWAP")
    outcome, receipt = run_from_database(pg, tmp_path, pdfs_of(tmp_path, spec))
    assert {i.category for i in outcome.items} == {Category.PASSED}

    write_back(pg, outcome, receipt, FMT, user="operator1")

    stored = pg.execute(
        "SELECT seq, initial_price, strike_price, ki_price, ko_price FROM workflow.underlying_price"
        " WHERE tdcc = %s ORDER BY seq",
        (spec.product_code,),
    ).fetchall()
    assert stored == [
        (i, u.initial, price(u.initial, spec.strike), None, price(u.initial, spec.ko))
        for i, u in enumerate(spec.underlyings, 1)
    ]


# ---------- 寫回失敗（不需要資料庫） ----------


class FakeDatabase:
    """假的資料庫來源：產生合成參考條件表；寫回第一次失敗、之後成功。"""

    def __init__(self, sheet: Path) -> None:
        self.sheet = sheet
        self.attempts = 0

    def ready(self) -> bool:
        return True

    def export(self, term_sheets, fmt):
        return self.sheet, {}

    def write_back(self, batch, receipt) -> str:
        self.attempts += 1
        if self.attempts == 1:
            raise ConnectionError("server closed the connection unexpectedly")
        return "已寫回資料庫"


def test_a_failed_write_back_keeps_the_result_file_and_can_be_retried(tmp_path) -> None:
    spec = Spec()
    pdfs = pdfs_of(tmp_path, spec)
    database = FakeDatabase(build_reference_sheet(tmp_path / "資料庫.xlsx", [reference_row(spec)]))
    session = PanelSession(install_root=tmp_path, database=database)
    session.select(None, pdfs)
    session.select_source(True)
    session.load_preview()
    session.start_check()

    receipt = session.save(tmp_path / "out", now=NOW)

    assert receipt.output is not None and receipt.output.exists()
    assert session.write_back_error == "server closed the connection unexpectedly"
    assert "寫回資料庫失敗" in session.message and "重新寫回資料庫" in session.message
    assert session.retry_write_back() == "已寫回資料庫"
    assert session.write_back_error == "" and database.attempts == 2


def test_excel_source_never_touches_the_database(tmp_path) -> None:
    spec = Spec()
    database = FakeDatabase(tmp_path / "不該用.xlsx")
    session = PanelSession(install_root=tmp_path, database=database)
    session.select(build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)]), pdfs_of(tmp_path, spec))
    session.load_preview()
    session.start_check()

    session.save(tmp_path / "out", now=NOW)

    assert database.attempts == 0 and session.write_back_error == ""


def test_new_export_files_never_overwrite_earlier_ones(tmp_path) -> None:
    source = WorkflowDatabase(lambda: None, tmp_path)
    first = source._new_path()
    first.write_text("x", encoding="utf-8")

    assert source._new_path() != first


# ---------- fcn-batch --database ----------


def test_cli_database_source_checks_pdfs_and_writes_back(tmp_path, monkeypatch, capsys) -> None:
    from fcn_checker import cli

    spec = Spec()
    pdfs = pdfs_of(tmp_path, spec)
    database = FakeDatabase(build_reference_sheet(tmp_path / "資料庫.xlsx", [reference_row(spec)]))
    database.attempts = 1  # 寫回直接成功
    monkeypatch.setattr(cli, "_database_source", lambda args: database)
    cli_root(tmp_path, monkeypatch)  # 預設設定檔相對於執行目錄

    code = cli.main(["--database", *map(str, pdfs), "--out", str(tmp_path / "out")])

    assert code == 0
    assert "已寫回資料庫" in capsys.readouterr().out


def test_cli_without_database_still_needs_a_reference_sheet(capsys) -> None:
    from fcn_checker import cli

    with pytest.raises(SystemExit):
        cli.main(["029199990001_TS.pdf"])
    assert "參考條件表" in capsys.readouterr().err
