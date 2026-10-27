"""可連結標的白名單（`sp_workflow.eln_whitelist`，#273）：讀「ELN可連結標的白名單.xlsm」的總表、整份取代存進資料庫，
風險等級的 ETF 清單改從資料庫讀。資料庫的部分對真的 PostgreSQL 跑；代碼全部虛構。"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence
from pathlib import Path

import openpyxl
import pytest

from order_synth import CONFIG, A
from sp_workflow.check_terms import recalculate
from sp_workflow.eln_whitelist import (
    Listed,
    WhitelistError,
    current_import,
    import_whitelist,
    read_whitelist,
    stored_etfs,
)
from sp_workflow.risk_level import risk_level_of
from sp_workflow.window_actions import import_eln_whitelist
from test_risk_level import OCT_15, import_order, import_prices, vol_price
from test_window_actions import PATHS

FETCHED = dt.date(2026, 10, 15)
HEADER = (
    "TICKER",
    "SP",
    "NQ",
    "SOX",
    "R1000",
    "R2000",
    "R3000",
    "NKY",
    "TPX500",
    "HSI",
    "ADR",
    "ETF",
    "所屬指數數",
    "所屬指數",
)
ROWS = (("AAA UW", "SP", "NQ"), ("BBB UN", "SP"), ("TSM UN", "ADR"), ("QQQ US", "ETF"), ("1 HK", "HSI"))


def write_whitelist(
    path: Path,
    rows: Sequence[tuple[str | None, ...]] = ROWS,
    *,
    fetched: object = dt.datetime.combine(FETCHED, dt.time()),
    header: Sequence[str] = HEADER,
    sheets: Sequence[str] = ("總表", "成分股", "ADR", "ETF", "設定", "說明"),
) -> Path:
    """照白名單的格式寫一份：總表第 1 列說明、第 2 列表頭、第 3 列起每檔一列（所屬的欄填欄名）；說明!B3 是抓取日。"""
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name in sheets:
        book.create_sheet(name)
    if "總表" in sheets:
        total = book["總表"]
        total.append((None, None, None, "由公式自動產生。"))
        total.append(tuple(header))
        for code, *groups in rows:
            cells = [code] + [h if h in groups else None for h in header[1:-2]]
            total.append((*cells, len(groups), "、".join(groups)))
    if "說明" in sheets:
        book["說明"]["A1"] = "使用說明"
        book["說明"]["B3"] = fetched
    book.save(path)
    return path


# ---------- 讀檔（不需資料庫） ----------


def test_the_summary_sheet_is_read_with_groups_and_fetch_date(tmp_path) -> None:
    whitelist = read_whitelist(write_whitelist(tmp_path / "白名單.xlsx"))

    assert whitelist.fetched_on == FETCHED
    assert whitelist.rows[:2] == (Listed("AAA UW", ("SP", "NQ")), Listed("BBB UN", ("SP",)))
    assert whitelist.etfs == frozenset({"QQQ US"})
    assert len(whitelist.rows) == 5


def test_the_macro_workbook_is_read_too(tmp_path) -> None:
    """作業人員給的是 .xlsm 原檔。"""
    path = write_whitelist(tmp_path / "x.xlsx").rename(tmp_path / "ELN可連結標的白名單.xlsm")

    assert read_whitelist(path).etfs == frozenset({"QQQ US"})


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"sheets": ("成分股", "說明")}, "沒有「總表」工作表"),
        ({"sheets": ("總表",)}, "沒有「說明」工作表"),
        ({"header": ("代碼", *HEADER[1:])}, "表頭"),
        ({"header": tuple(h for h in HEADER if h != "ETF")}, "ETF"),
        ({"header": (*HEADER[:11], "KOSPI", *HEADER[11:])}, "不認得的分類欄：KOSPI"),
        ({"fetched": None}, "抓取日期"),
        ({"rows": ()}, "沒有任何代碼"),
        ({"rows": ((None, "SP"),)}, "沒有任何代碼"),
        ({"rows": (*ROWS, ("AAA UW", "R1000"))}, "AAA UW 出現兩次"),
        ({"rows": (*ROWS, ("ZZZ UN",))}, "ZZZ UN 沒有所屬"),
        ({"rows": (("AAA UW", "SP"), ("BBB UN", "SP"))}, "沒有任何 ETF"),
    ],
)
def test_a_workbook_in_the_wrong_format_is_rejected(tmp_path, kwargs, message) -> None:
    with pytest.raises(WhitelistError, match=message):
        read_whitelist(write_whitelist(tmp_path / "白名單.xlsx", **kwargs))


def test_a_missing_file_is_rejected(tmp_path) -> None:
    with pytest.raises(WhitelistError, match="打不開"):
        read_whitelist(tmp_path / "沒有.xlsm")


# ---------- 資料庫 ----------


@pytest.mark.db
def test_each_import_replaces_the_whole_list_and_logs_the_changes(pg, tmp_path) -> None:
    first = read_whitelist(write_whitelist(tmp_path / "a.xlsx"))
    nov_14 = dt.date(2026, 11, 14)
    bbb_etf = Listed("BBB UN", ("SP", "ETF"))
    later = dataclasses.replace(first, fetched_on=nov_14, rows=(bbb_etf, *first.rows[2:], Listed("SPY US", ("ETF",))))

    assert stored_etfs(pg) is None and current_import(pg) is None, "還沒匯入過"
    one = import_whitelist(pg, first, tmp_path / "a.xlsx", user="op1")
    again = import_whitelist(pg, first, tmp_path / "a.xlsx", user="op1")
    two = import_whitelist(pg, later, tmp_path / "b.xlsm", user="op2")

    assert (one.code_count, one.etf_count, len(one.added), one.removed, one.previous) == (5, 1, 5, (), None)
    assert (again.added, again.removed, again.regrouped, again.previous) == ((), (), (), FETCHED)
    assert two.added == (Listed("SPY US", ("ETF",)),)
    assert two.removed == (Listed("AAA UW", ("SP", "NQ")),)
    assert two.regrouped == ((Listed("BBB UN", ("SP",)), bbb_etf),), "變成 ETF 也要看得到"
    assert stored_etfs(pg) == frozenset({"BBB UN", "QQQ US", "SPY US"})
    assert pg.execute("SELECT count(*) FROM workflow.eln_whitelist").fetchone() == (5,)
    current = current_import(pg)
    assert current is not None
    assert (current.fetched_on, current.source_file, current.code_count, current.etf_count, current.imported_by) == (
        nov_14,
        "b.xlsm",
        5,
        3,
        "op2",
    )

    back = import_whitelist(pg, first, tmp_path / "a.xlsx", user="op1")

    assert back.previous == nov_14, "上一份是上一次匯入的那份，不是抓取日最晚的"
    log = pg.execute(
        "SELECT fetched_on, source_file, code_count, etf_count, added_count, removed_count, regrouped_count,"
        " imported_by FROM workflow.eln_whitelist_import ORDER BY id"
    ).fetchall()
    assert log == [
        (FETCHED, "a.xlsx", 5, 1, 5, 0, 0, "op1"),
        (FETCHED, "a.xlsx", 5, 1, 0, 0, 0, "op1"),
        (nov_14, "b.xlsm", 5, 3, 1, 1, 1, "op2"),
        (FETCHED, "a.xlsx", 5, 1, 1, 1, 1, "op1"),
    ]


def underlying_type(pg) -> str:
    risk = risk_level_of(pg, A)
    assert risk is not None and risk.detail is not None
    return risk.detail.underlying_type


@pytest.mark.db
def test_the_risk_level_reads_etfs_from_the_imported_whitelist(pg, tmp_path) -> None:
    """匯入前用設定檔的備用清單；匯入後只看白名單（加上設定檔的補充 ETF），白名單上不是 ETF 的照個股算。"""
    import_prices(pg, tmp_path, OCT_15, [vol_price("AAA UW", 20), vol_price("BBB UN", 40)])
    import_order(pg)
    config = dataclasses.replace(CONFIG, fallback_etf_underlyings=frozenset({"AAA US Equity"}))

    recalculate(pg, config)
    assert underlying_type(pg) == "ETF", "還沒匯入：用備用清單"

    import_whitelist(pg, read_whitelist(write_whitelist(tmp_path / "白名單.xlsx")), tmp_path / "白名單.xlsx")
    recalculate(pg, config)
    assert underlying_type(pg) == "個股", "匯入後：白名單上 AAA UW 是 SP、NQ 成分股，不是 ETF"

    recalculate(pg, dataclasses.replace(config, etf_underlyings=frozenset({"AAA UW"})))
    assert underlying_type(pg) == "ETF", "補充清單照樣合併"


@pytest.mark.db
def test_the_window_action_imports_then_recalculates(pg, pg_settings, tmp_path) -> None:
    """維護選單的匯入：整份寫入後重算還沒鎖住的商品；檔案格式不對時不寫入。"""
    import_prices(pg, tmp_path, OCT_15, [vol_price("QQQ UQ", 20), vol_price("BBB UN", 40)])
    import_order(pg, **{"BBG Code 1": "QQQ UQ"})

    result = import_eln_whitelist(pg_settings, write_whitelist(tmp_path / "白名單.xlsm"), PATHS)

    assert (result.receipt.code_count, result.receipt.etf_count) == (5, 1)
    assert result.recalc.receipt is not None and result.recalc.error == ""
    assert underlying_type(pg) == "ETF"
    with pytest.raises(WhitelistError):
        import_eln_whitelist(pg_settings, write_whitelist(tmp_path / "壞.xlsx", sheets=("說明",)), PATHS)
    assert pg.execute("SELECT count(*) FROM workflow.eln_whitelist_import").fetchone() == (1,)
