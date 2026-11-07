"""拆股行事曆（`sp_workflow.split_calendar`，#509、ADR 0031）：解析、代碼對照、要登記的、對齊價格斷層與每日匯入。

不上網：行事曆的回應用假資料（欄位照 2026-11 實際抓到的格式）；資料庫的部分對真的 PostgreSQL 跑，代碼與價格全部虛構。
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
from decimal import Decimal
from pathlib import Path

import pytest

from db_synth import price, seed_mailbox, trade, write_daily
from fcn_checker.check_config import ConfigPaths
from order_synth import A
from sp_workflow import split_calendar
from sp_workflow.check_terms import load_calendar
from sp_workflow.split_calendar import (
    AUTO_NOTE,
    NASDAQ,
    YAHOO,
    Announced,
    CalendarReceipt,
    Fetched,
    Skipped,
    align,
    candidates,
    fetch,
    misaligned,
    parse_nasdaq,
    parse_yahoo,
    register,
    source_key,
    summary,
    yahoo_rows,
)
from sp_workflow.stock_splits import Split, SplitBook, add_split, load_splits
from sp_workflow.window_actions import run_daily_import

D = Decimal
PATHS = ConfigPaths.with_fallback(Path(__file__).resolve().parents[1] / "config" / "review_standard.toml")
CALENDAR = load_calendar(PATHS.exchange_holidays)
TODAY = dt.date(2026, 11, 8)
DAY = dt.date(2026, 11, 20)


def yahoo_page(*rows: list[object]) -> dict[str, object]:
    columns = ["ticker", "startdatetime", "old_share_worth", "share_worth"]
    return {"finance": {"result": [{"documents": [{"columns": [{"id": c} for c in columns], "rows": list(rows)}]}]}}


# ---------- 解析、代碼對照 ----------


def test_yahoo_rows_keep_us_tokyo_and_hong_kong() -> None:
    rows = yahoo_rows(
        yahoo_page(
            ["AAA", "2026-11-20T05:00:00.000Z", 1, 8],
            ["1234.T", "2026-11-19T05:00:00.000Z", 1, 5],
            ["0700.HK", "2026-12-17T05:00:00.000Z", 10, 1],
            ["BBB-B", "2026-12-01T05:00:00.000Z", 1, 2],
            ["048550.KQ", "2026-12-30T05:00:00.000Z", 5, 1],
            ["CCC", "2026-12-01T05:00:00.000Z", 1, 0],
            ["DDD", "not a date", 1, 2],
            ["EEE", "2026-12-01T05:00:00.000Z", 2.0, 3.0],
        )
    )

    assert parse_yahoo(rows) == [
        Announced(("US", "AAA"), DAY, 1, 8, YAHOO),
        Announced(("JP", "1234"), dt.date(2026, 11, 19), 1, 5, YAHOO),
        Announced(("HK", "700"), dt.date(2026, 12, 17), 10, 1, YAHOO),
        Announced(("US", "BBB/B"), dt.date(2026, 12, 1), 1, 2, YAHOO),
        Announced(("US", "EEE"), dt.date(2026, 12, 1), 2, 3, YAHOO),
    ], "其他交易所、股數 0、讀不懂的日期略過；2.0 當 2"


def test_nasdaq_ratio_is_after_to_before() -> None:
    rows = [
        {"symbol": "AAA", "ratio": "8:1", "executionDate": "11/20/2026"},
        {"symbol": "BBB", "ratio": "1 : 2", "executionDate": "11/19/2026"},
        {"symbol": "CCC.B", "ratio": "3:2", "executionDate": "12/01/2026"},
        {"symbol": "DDD", "ratio": "8:1", "executionDate": ""},
        {"symbol": "EEE", "ratio": "1.5:1", "executionDate": "12/01/2026"},
    ]

    assert parse_nasdaq(rows) == [
        Announced(("US", "AAA"), DAY, 1, 8, NASDAQ),
        Announced(("US", "BBB"), dt.date(2026, 11, 19), 2, 1, NASDAQ),
        Announced(("US", "CCC/B"), dt.date(2026, 12, 1), 2, 3, NASDAQ),
    ]


@pytest.mark.parametrize(
    ("code", "key"),
    [
        ("AAA UW", ("US", "AAA")),
        ("AAA UQ", ("US", "AAA")),
        ("BBB/B UN", ("US", "BBB/B")),
        ("1234 JT", ("JP", "1234")),
        ("285A JT", ("JP", "285A")),
        ("700 HK", ("HK", "700")),
        ("0700 HK", ("HK", "700")),
        ("AAA ZZ", None),
        ("AAA", None),
    ],
)
def test_source_key_follows_the_exchange_code(code: str, key: tuple[str, str] | None) -> None:
    assert source_key(code, CALENDAR) == key


# ---------- 要登記的 ----------

CODES = {"AAA UW": ("US", "AAA"), "AAA UQ": ("US", "AAA"), "1234 JT": ("JP", "1234"), "BBB UN": ("US", "BBB")}


def test_both_sources_agreeing_make_one_entry_per_code() -> None:
    fetched = Fetched(
        TODAY,
        (
            Announced(("US", "AAA"), DAY, 1, 8, YAHOO),
            Announced(("US", "AAA"), DAY, 1, 8, NASDAQ),
            Announced(("JP", "1234"), dt.date(2026, 11, 19), 1, 5, YAHOO),
            Announced(("JP", "1234"), dt.date(2026, 11, 19), 1, 5, YAHOO),
            Announced(("US", "ZZZ"), DAY, 1, 2, NASDAQ),
        ),
    )

    adds, skipped = candidates(fetched, CODES, [])

    note = f"{AUTO_NOTE}：Yahoo、Nasdaq 行事曆（2026-11-08 抓取）"
    assert adds == [
        Split("1234 JT", dt.date(2026, 11, 19), 1, 5, f"{AUTO_NOTE}：Yahoo 行事曆（2026-11-08 抓取）"),
        Split("AAA UQ", DAY, 1, 8, note),
        Split("AAA UW", DAY, 1, 8, note),
    ], "同一個代號的兩個交易所碼都登記；不認得的代號不收"
    assert skipped == []


def test_disagreements_and_odd_ratios_are_listed_not_registered() -> None:
    fetched = Fetched(
        TODAY,
        (
            Announced(("US", "AAA"), DAY, 1, 8, YAHOO),
            Announced(("US", "AAA"), DAY + dt.timedelta(days=1), 1, 8, NASDAQ),
            Announced(("JP", "1234"), DAY, 3124872, 1, YAHOO),
            Announced(("US", "BBB"), DAY, 1, 1, YAHOO),
        ),
    )

    adds, skipped = candidates(
        fetched, {"AAA UW": ("US", "AAA"), "1234 JT": ("JP", "1234"), "BBB UN": ("US", "BBB")}, []
    )

    assert adds == []
    assert skipped == [
        Skipped("1234 JT", "Yahoo 寫 2026-11-20 3124872 合 1，比例異常，沒有登記"),
        Skipped(
            "AAA UW",
            "行事曆不一致（Yahoo 2026-11-20 1 拆 8、Nasdaq 2026-11-21 1 拆 8），請人工確認後登記",
        ),
        Skipped("BBB UN", "Yahoo 寫 2026-11-20 1 拆 1，比例異常，沒有登記"),
    ]


def test_anything_already_registered_nearby_is_left_alone() -> None:
    fetched = Fetched(TODAY, (Announced(("US", "AAA"), DAY, 1, 8, YAHOO), Announced(("US", "BBB"), DAY, 1, 2, YAHOO)))
    existing = [Split("AAA UW", DAY - dt.timedelta(days=7), 1, 4), Split("BBB UN", DAY - dt.timedelta(days=8), 1, 2)]

    adds, skipped = candidates(fetched, {"AAA UW": ("US", "AAA"), "BBB UN": ("US", "BBB")}, existing)

    assert [s.code for s in adds] == ["BBB UN"], "7 天內已有登記（不管比例）不再登記、也不列出"
    assert skipped == []


# ---------- 抓行事曆（假的連線） ----------


class FakeWeb:
    def __init__(self, *, nasdaq_down: bool = False) -> None:
        self.nasdaq_down = nasdaq_down
        self.bodies: list[dict[str, object]] = []

    def __call__(self, request) -> bytes:
        url = request.full_url
        if url == "https://fc.yahoo.com":
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if "getcrumb" in url:
            return b"abc/def"
        if "visualization" in url:
            assert "crumb=abc%2Fdef" in url
            body = json.loads(request.data)
            self.bodies.append(body)
            if body["offset"] == 0:
                return json.dumps(yahoo_page(*[[f"X{i}", "2026-11-20T05:00:00Z", 1, 2] for i in range(100)])).encode()
            return json.dumps(yahoo_page(["AAA", "2026-11-20T05:00:00Z", 1, 8])).encode()
        if "nasdaq" in url:
            if self.nasdaq_down:
                raise urllib.error.URLError("timed out")
            rows = [{"symbol": "AAA", "ratio": "8:1", "executionDate": "11/20/2026"}]
            return json.dumps({"data": {"rows": rows}}).encode()
        raise AssertionError(url)


def test_fetch_pages_through_yahoo_and_reads_nasdaq() -> None:
    web = FakeWeb()

    fetched = fetch(TODAY, web)

    assert len(fetched.announced) == 102 and not fetched.errors
    assert fetched.announced[100] == Announced(("US", "AAA"), DAY, 1, 8, YAHOO)
    assert fetched.announced[101] == Announced(("US", "AAA"), DAY, 1, 8, NASDAQ)
    assert [b["offset"] for b in web.bodies] == [0, 100]
    assert web.bodies[0]["query"]["operands"][0]["operands"][1] == "2026-10-29", "今天前 10 天起"


def test_one_source_down_keeps_the_other() -> None:
    fetched = fetch(TODAY, FakeWeb(nasdaq_down=True))

    assert {a.source for a in fetched.announced} == {YAHOO}
    (error,) = fetched.errors
    assert error.startswith("Nasdaq 拆股行事曆沒抓到（URLError")


# ---------- 對齊價格斷層 ----------

MON = dt.date(2026, 11, 16)
WEEK = [MON + dt.timedelta(days=i) for i in range(5)]  # 週一到週五


def closes(*values: int) -> dict[dt.date, Decimal]:
    return {day: D(v) for day, v in zip(WEEK, values, strict=False)}


def test_a_split_registered_a_day_late_moves_to_the_gap() -> None:
    split = Split("AAA UW", WEEK[3], 1, 5, AUTO_NOTE)

    assert misaligned(split, SplitBook(), closes(100, 101, 20, 21, 20)) == WEEK[2]
    assert misaligned(split, SplitBook(), closes(100, 101, 102, 20, 21)) is None, "斷層就在生效日"
    assert misaligned(split, SplitBook(), closes(100, 101)) is None, "還沒有生效日附近的收盤價"
    assert misaligned(split, SplitBook(), closes(100, 101, 50, 51, 50)) is None, "斷層比例跟這次拆股對不起來"
    assert misaligned(Split("AAA UW", dt.date(2026, 11, 26), 1, 5), SplitBook(), closes(100, 20, 21, 20, 21)) is None, (
        "斷層離生效日超過 3 個收盤日"
    )


def test_a_split_registered_early_also_moves() -> None:
    split = Split("AAA UW", WEEK[1], 1, 5, AUTO_NOTE)

    assert misaligned(split, SplitBook(), closes(100, 101, 20, 21, 20)) == WEEK[2]


# ---------- 資料庫 ----------


def close(pg, code: str, day: dt.date, value: int = 10) -> None:
    (import_id,) = pg.execute(
        "INSERT INTO workflow.close_price_import (source, file_name, file_sha256, imported_by, summary)"
        " VALUES ('歷史收盤價', 'HP.xlsx', 'x', 'tester', '{}') RETURNING import_id"
    ).fetchone()
    pg.execute(
        "INSERT INTO workflow.close_price (close_date, code, close, import_id) VALUES (%s, %s, %s, %s)",
        (day, code, value, import_id),
    )


@pytest.mark.db
def test_register_writes_known_codes_with_a_log(pg) -> None:
    close(pg, "AAA UW", TODAY - dt.timedelta(days=3))
    fetched = Fetched(
        TODAY,
        (Announced(("US", "AAA"), DAY, 1, 8, YAHOO), Announced(("US", "ZZZ"), DAY, 1, 2, YAHOO)),
        ("Nasdaq 拆股行事曆沒抓到（URLError：timed out）",),
    )

    receipt = register(pg, fetched, CALENDAR, user="tester")

    expected = Split("AAA UW", DAY, 1, 8, f"{AUTO_NOTE}：Yahoo 行事曆（2026-11-08 抓取）")
    assert receipt.registered == (expected,) and receipt.errors == fetched.errors
    assert load_splits(pg) == (expected,)
    assert pg.execute("SELECT changed_by, action FROM workflow.stock_split_log").fetchall() == [("tester", "新增")]
    assert register(pg, fetched, CALENDAR, user="tester").registered == (), "隔天再抓到同一筆不重複登記"


@pytest.mark.db
def test_align_moves_auto_entries_only(pg) -> None:
    for day, value in zip(WEEK, (100, 101, 20, 21, 20), strict=True):
        close(pg, "AAA UW", day, value)
        close(pg, "BBB UW", day, value)
    add_split(pg, Split("AAA UW", WEEK[3], 1, 5, f"{AUTO_NOTE}：Yahoo 行事曆（2026-11-08 抓取）"), user="tester")
    add_split(pg, Split("BBB UW", WEEK[3], 1, 5, "人工"), user="tester")

    receipt = align(pg, WEEK[4], user="tester")

    (moved,) = receipt.moved
    assert (moved.before.effective_date, moved.after.effective_date) == (WEEK[3], WEEK[2])
    assert moved.after.note.endswith("；依收盤價斷層從 2026-11-19 改到 2026-11-18")
    assert {(s.code, s.effective_date) for s in load_splits(pg)} == {("AAA UW", WEEK[2]), ("BBB UW", WEEK[3])}
    assert [a for (a,) in pg.execute("SELECT action FROM workflow.stock_split_log ORDER BY change_id")] == [
        "新增",
        "新增",
        "刪除",
        "新增",
    ]
    assert align(pg, WEEK[4], user="tester").moved == (), "對齊後不再動"


@pytest.mark.db
def test_daily_import_registers_before_observing(pg, pg_settings, tmp_path: Path, monkeypatch) -> None:
    seed_mailbox(pg)
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 100000)], [price("AAA UW")])
    monkeypatch.setattr(
        split_calendar,
        "fetch",
        lambda today, opener=None: Fetched(today, (Announced(("US", "AAA"), DAY, 1, 8, YAHOO),), ("Nasdaq 沒抓到",)),
    )

    result = run_daily_import(pg_settings, daily, PATHS, prices=True)

    assert result.splits is not None and [s.code for s in result.splits.registered] == ["AAA UW"]
    assert [(s.code, s.effective_date) for s in load_splits(pg)] == [("AAA UW", DAY)]
    assert result.observation.receipt is not None
    assert summary(result.splits).splitlines() == [
        "拆股行事曆：新登記 1 筆（AAA UW 2026-11-20 1 拆 8）。",
        "拆股行事曆：Nasdaq 沒抓到",
    ]


@pytest.mark.db
def test_trades_only_import_does_not_fetch(pg, pg_settings, tmp_path: Path, monkeypatch) -> None:
    seed_mailbox(pg)
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 100000)], [price("AAA UW")])

    def no_network(today, opener=None):
        raise AssertionError("只匯交易明細不該抓行事曆")

    monkeypatch.setattr(split_calendar, "fetch", no_network)

    assert run_daily_import(pg_settings, daily, PATHS, prices=False).splits is None


def test_summary_when_nothing_changed() -> None:
    assert summary(CalendarReceipt()) == "拆股行事曆：沒有新的拆股。"
