"""起單資料確認與匯入（#86、#97、ADR 0009、0010）：讀取結果用 `order_row` 組出，資料庫測試對真的 PostgreSQL。資料全部虛構。"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from db_synth import inventory, seed_mailbox, seed_products, trade, write_daily
from fcn_checker.check_config import ConfigPaths
from order_synth import CONFIG, PREFIXES, A, B, drafts_of, missing_row, order_row, sheet_line, write_sheet
from sp_workflow import database
from sp_workflow.daily_import import import_daily
from sp_workflow.mail_drafts import PENDING as MAIL_PENDING
from sp_workflow.mail_drafts import create_drafts
from sp_workflow.order_terms import TDCC_FIELD, OrderRow, collect_orders, describe
from sp_workflow.product_record import FIELDS
from sp_workflow.product_terms import load_import_config, problems, read, write_batch
from sp_workflow.status import ImportResult, OrderReview

BUILTIN = ConfigPaths.with_fallback(Path(__file__).resolve().parents[1] / "config" / "review_standard.toml")
CONFLICT = "起單建檔裡這檔有好幾列、這欄不同，要填正確的值（正確是空白的話請改起單建檔後重新讀取）"


def draft_of(row: OrderRow, conn=None):
    (draft,) = drafts_of([row], conn)
    return draft


# ---------- 確認資料（不需要資料庫） ----------


def test_draft_holds_the_sheet_fields() -> None:
    draft = draft_of(order_row())

    assert draft.values["KO Type"] == "Daily Memory"
    assert draft.values["Upfront / NotePrice (%)"] == Decimal("98.5")
    assert draft.values["Barrier Type"] == "EKI"
    assert set(draft.values) == set(FIELDS)
    assert problems(draft, CONFIG) == {}
    assert draft.edits() == []
    assert draft.import_review(CONFIG) == (OrderReview.READY, "", False)


def test_rows_not_in_the_sheet_have_no_draft() -> None:
    row = missing_row(A)

    assert drafts_of([row]) == []
    # 畫面照讀取狀態顯示（`tabs.orders`）
    assert (row.status, describe(row.notes)) == ("找不到", "TDCC：起單建檔_20261015.xlsm 裡沒有這檔")


def test_missing_final_valuation_and_maturity_dates_block_the_import() -> None:
    draft = draft_of(order_row(changes={"Final valuation date": None, "Maturity date": None, "Issue date": None}))

    assert problems(draft, CONFIG) == {"Final valuation date": "必填", "Maturity date": "必填"}
    assert draft.import_review(CONFIG)[0] == OrderReview.BLOCKED


def test_filling_the_red_cells_makes_the_product_importable() -> None:
    draft = draft_of(order_row(changes={"Final valuation date": None, "Maturity date": None}))
    draft.values["Final valuation date"] = dt.date(2027, 4, 15)
    draft.values["Maturity date"] = dt.date(2027, 4, 22)

    assert problems(draft, CONFIG) == {}
    assert draft.edits() == [
        ("Final valuation date", None, dt.date(2027, 4, 15)),
        ("Maturity date", None, dt.date(2027, 4, 22)),
    ]


def test_sheet_notes_are_shown_but_do_not_block_on_their_own() -> None:
    notes = {"Issue date": "讀到「10/22」，日期要寫成 2026-10-21"}
    draft = draft_of(order_row(changes={"Issue date": None}, notes=notes))

    assert draft.notes == notes
    assert problems(draft, CONFIG) == {}
    assert draft.import_review(CONFIG) == (
        OrderReview.TO_CHECK,
        "Issue date：讀到「10/22」，日期要寫成 2026-10-21",
        False,
    )


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"Barrier Type": "NONE"}, {"KI Barrier (%)": "Barrier Type 是 NONE 時要空白"}),
        ({"KI Barrier (%)": None}, {"KI Barrier (%)": "有 KI 時必填"}),
        ({"Currency": "TWD"}, {"Currency": "這個幣別沒有單位面額預設值"}),
        ({"Product": "BEN"}, {"Product": "目前只做 FCN"}),
        ({"Guaranteed Periods (m)": 0}, {"Guaranteed Periods (m)": "至少 1"}),
        ({"BBG Code 2": None, "BBG Code 3": "CCC UQ"}, {"BBG Code 2": "標的要從 BBG Code 1 依序填，中間不能空"}),
        ({"KO Type": "Weekly"}, {"KO Type": "要選 Daily／Daily Memory／Period End／Period End Memory"}),
        ({"Tenor (m)": "6"}, {"Tenor (m)": "要填整數"}),
        ({"Strike date": dt.date(2026, 10, 14)}, {"Strike date": "不能早於 Trade date"}),
        ({"Strike date": None}, {"Strike date": "必填"}),
    ],
)
def test_field_checks(changes, expected) -> None:
    draft = draft_of(order_row())
    draft.values.update(changes)

    assert problems(draft, CONFIG) == expected


def test_conflicting_duplicate_fields_must_be_filled() -> None:
    changes = {"Coupon p.a. (%)": None, "BBG Code 3": None}
    draft = draft_of(order_row(changes=changes, conflicts=("Coupon p.a. (%)", "BBG Code 3")))

    assert problems(draft, CONFIG) == {"BBG Code 3": CONFLICT, "Coupon p.a. (%)": CONFLICT}
    draft.values.update({"Coupon p.a. (%)": Decimal(13), "BBG Code 3": "CCC UQ"})
    assert problems(draft, CONFIG) == {}


def test_unresolved_strike_date_must_be_filled() -> None:
    row = order_row(changes={"Strike date": None}, notes={"Strike date": "算不出來"})
    row = dataclasses.replace(row, unresolved=frozenset({"Strike date"}))
    draft = draft_of(row)

    assert problems(draft, CONFIG) == {"Strike date": "起單建檔算不出來，要填"}


def test_strike_date_follows_an_edited_trade_date_when_they_were_equal() -> None:
    draft = draft_of(order_row())

    assert draft.set_value("Trade date", dt.date(2026, 10, 16)) == ["Strike date"]
    assert draft.values["Strike date"] == dt.date(2026, 10, 16)
    assert draft.set_value("Trade date", dt.date(2026, 10, 19), follow=False) == []
    assert draft.values["Strike date"] == dt.date(2026, 10, 16)


def test_a_next_day_strike_date_stays_when_the_trade_date_changes() -> None:
    draft = draft_of(order_row(changes={"Strike date": dt.date(2026, 10, 16)}))

    assert draft.set_value("Trade date", dt.date(2026, 10, 14)) == []
    assert draft.values["Strike date"] == dt.date(2026, 10, 16)
    draft.set_value("Trade date", dt.date(2026, 10, 19))
    assert problems(draft, CONFIG) == {"Strike date": "不能早於 Trade date"}


def test_mistyped_fields_block_only_that_product() -> None:
    draft = draft_of(order_row())
    draft.input_errors["Trade date"] = "日期要寫成 2026-10-21"

    assert problems(draft, CONFIG) == {"Trade date": "日期要寫成 2026-10-21"}


def test_issuer_without_a_configured_name_cannot_be_imported() -> None:
    draft = draft_of(order_row("092199990001"))

    assert problems(draft, CONFIG) == {"TDCC": "上手 UBS 在參考條件表設定沒有發行機構寫法"}


def test_unknown_issuer_prefix_cannot_be_imported() -> None:
    draft = draft_of(order_row("999199990001", notes={TDCC_FIELD: "TDCC 前三碼 999 不在上手編號對照"}))

    assert problems(draft, CONFIG) == {"TDCC": "TDCC 前三碼 999 不在上手編號對照"}


def test_builtin_config_has_denominations_issuer_names_and_prefixes() -> None:
    config = load_import_config(BUILTIN)

    assert config.denominations["USD"] == Decimal(10000)
    assert config.denominations["JPY"] == Decimal(1000000)
    assert len(config.denominations) == 8
    assert config.issuer_names["BARC"] == "Barclays"
    assert config.issuer_prefixes["029"] == "BARC"
    assert set(config.issuer_prefixes.values()) <= set(config.issuer_names)


# ---------- 匯入資料庫 ----------


def product(pg, tdcc: str) -> dict:
    cur = pg.execute("SELECT * FROM workflow.product WHERE tdcc = %s", (tdcc,))
    names = [d.name for d in cur.description]
    found = cur.fetchone()
    return None if found is None else dict(zip(names, found, strict=True))


@pytest.mark.db
def test_import_writes_a_new_product_from_the_confirmed_values(pg) -> None:
    draft = draft_of(order_row(changes={"Maturity date": None}), pg)
    draft.values["Maturity date"] = dt.date(2027, 4, 22)

    receipt = write_batch(pg, [draft], CONFIG, user="tester")

    assert [(line.tdcc, line.result) for line in receipt.lines] == [(A, ImportResult.NEW)]
    p = product(pg, A)
    assert p["source"] == "Order" and p["order_import_id"] == receipt.import_id and p["first_run_id"] is None
    assert (p["product"], p["issuer"], p["currency"], p["isin"]) == ("FCN", "Barclays", "USD", None)
    assert (p["ko_freq"], p["ko_memory"], p["ko_type"]) == ("D", "Y", "Daily Memory")
    assert (p["ki_freq"], p["barrier_type"], p["ki_pct"]) == ("AM", "EKI", Decimal(65))
    assert p["upfront_pct"] == Decimal("1.5")
    assert p["denomination"] == Decimal(10000) and p["order_amount"] is None  # 金額以交易明細為準（#97）
    assert p["maturity_date"] == dt.date(2027, 4, 22) and "status_event" not in p  # 舊狀態欄已移除（#172）
    assert p["strike_date"] == dt.date(2026, 10, 15)
    assert (p["underlying_1"], p["underlying_2"], p["underlying_3"]) == ("AAA UW", "BBB UN", None)
    imported_by, counts = pg.execute(
        "SELECT imported_by, summary -> 'counts' FROM workflow.order_import WHERE import_id = %s",
        (receipt.import_id,),
    ).fetchone()
    assert imported_by == "tester" and counts[ImportResult.NEW] == 1
    result, source, confirmed, edits = pg.execute(
        "SELECT result, source_mail, confirmed, edits FROM workflow.order_import_line WHERE import_id = %s",
        (receipt.import_id,),
    ).fetchone()
    assert (result, source) == (ImportResult.NEW, "起單建檔_20261015.xlsm 第 3 列")
    assert confirmed["Maturity date"] == "2027-04-22" and confirmed["Coupon p.a. (%)"] == "12.5"
    assert edits == [["Maturity date", None, "2027-04-22"]]


@pytest.mark.db
def test_a_sheet_read_from_disk_imports_end_to_end(pg, tmp_path: Path) -> None:
    lines = [
        sheet_line(A, {"KO Type": "DAILY MEMORY"}),
        sheet_line(B, {"Barrier Type": "None", "KI Barrier (%)": None}),
    ]
    path = write_sheet(tmp_path / "起單建檔_20261015.xlsx", lines)
    rows = collect_orders(f"{A}\n{B}", path, PREFIXES, CONFIG.calendar)

    receipt = write_batch(pg, drafts_of(rows, pg), CONFIG, user="tester")

    assert [(line.tdcc, line.result) for line in receipt.lines] == [(A, ImportResult.NEW), (B, ImportResult.NEW)]
    assert product(pg, A)["coupon_pct"] == Decimal("12.5") and product(pg, A)["upfront_pct"] == Decimal("1.5")
    assert (product(pg, B)["issuer"], product(pg, B)["ki_freq"], product(pg, B)["ki_pct"]) == ("SG", "-", None)


@pytest.mark.db
@pytest.mark.parametrize("currency", ["USD", "JPY", "CNH", "HKD", "AUD", "EUR", "GBP", "ZAR"])
def test_denomination_is_the_currency_default(pg, currency) -> None:
    config = load_import_config(BUILTIN)

    write_batch(pg, [draft_of(order_row(changes={"Currency": currency}), pg)], config, user="tester")

    assert product(pg, A)["denomination"] == config.denominations[currency]


@pytest.mark.db
def test_products_with_problems_are_skipped_and_the_rest_imported(pg) -> None:
    bad = draft_of(order_row(changes={"Maturity date": None}), pg)
    good = draft_of(order_row(B), pg)
    unknown = draft_of(order_row("029199990009", {"Currency": "TWD"}), pg)

    receipt = write_batch(pg, [bad, good, unknown], CONFIG, user="tester")

    assert [(line.tdcc, line.result, line.detail) for line in receipt.lines] == [
        (A, ImportResult.SKIPPED, "Maturity date：必填"),
        (B, ImportResult.NEW, ""),
        ("029199990009", ImportResult.SKIPPED, "Currency：這個幣別沒有單位面額預設值"),
    ]
    assert product(pg, A) is None and product(pg, B)["issuer"] == "SG"


@pytest.mark.db
def test_reimport_overwrites_until_locked_and_keeps_the_previous_values(pg) -> None:
    write_batch(pg, [draft_of(order_row(), pg)], CONFIG, user="tester")

    same = write_batch(pg, [draft_of(order_row(), pg)], CONFIG, user="tester")
    changed = draft_of(order_row(), pg)
    changed.values["Coupon p.a. (%)"] = Decimal("11.25")
    overwritten = write_batch(pg, [changed], CONFIG, user="tester")

    assert same.lines[0].result == ImportResult.SAME
    assert (overwritten.lines[0].result, overwritten.lines[0].detail) == (
        ImportResult.OVERWRITTEN,
        "改了：Coupon p.a. (%)",
    )
    assert product(pg, A)["coupon_pct"] == Decimal("11.25")
    assert product(pg, A)["order_import_id"] == overwritten.import_id
    previous = pg.execute(
        "SELECT previous FROM workflow.order_import_line WHERE import_id = %s", (overwritten.import_id,)
    ).fetchone()[0]
    assert previous["coupon_pct"] == "12.5" and previous["issuer"] == "Barclays" and previous["denomination"] == "10000"

    pg.execute("UPDATE workflow.product SET locked_at = now() WHERE tdcc = %s", (A,))
    locked = draft_of(order_row(changes={"Coupon p.a. (%)": Decimal(10)}), pg)
    receipt = write_batch(pg, [locked], CONFIG, user="tester")

    assert locked.locked.startswith("已核對通過")
    assert receipt.lines[0].result == ImportResult.LOCKED
    assert product(pg, A)["coupon_pct"] == Decimal("11.25")


def _insert_old_product(pg, tdcc: str) -> None:
    """0005 版時以清單更新匯入的商品（必填欄位都填）。"""
    run_id = pg.execute(
        "INSERT INTO workflow.import_run (daily_file_name, daily_sha256, summary) VALUES ('d.xlsx', 'x', '{}')"
        " RETURNING run_id"
    ).fetchone()[0]
    pg.execute(
        "INSERT INTO workflow.product (tdcc, product, issuer, isin, currency, trade_date, issue_date,"
        " final_valuation_date, maturity_date, non_call_months, underlying_1, strike_pct, ko_freq, ko_memory,"
        " ko_type, ko_pct, coupon_pct, upfront_pct, tenor_months, ki_freq, barrier_type, status_event, first_run_id)"
        " VALUES (%s, 'FCN', 'Barclays', 'XS0000000001', 'USD', '2026-01-15', '2026-01-22', '2026-07-15',"
        " '2026-07-22', 1, 'AAA UW', 80, 'D', 'Y', 'Daily Memory', 100, 12.5, 1.5, 6, 'AM', 'EKI', '在庫', %s)",
        (tdcc, run_id),
    )


@pytest.mark.db
def test_products_already_in_the_database_are_locked_by_the_migration(pg) -> None:
    pg.execute("DROP SCHEMA mailbox, workflow CASCADE")
    pg.execute(f"DELETE FROM public.{database.VERSION_TABLE}")
    for version, name, text in database.migration_files()[:5]:
        pg.execute(text)
        pg.execute(f"INSERT INTO public.{database.VERSION_TABLE} (version, name) VALUES (%s, %s)", (version, name))
    _insert_old_product(pg, A)

    assert database.apply_migrations(pg)[0] == "0006_order_term_import.sql"
    assert product(pg, A)["strike_date"] == dt.date(2026, 1, 15), "migration 0008 補成交易日"
    draft = draft_of(order_row(), pg)
    receipt = write_batch(pg, [draft], CONFIG, user="tester")

    assert draft.locked.startswith("以前清單更新匯入的商品")
    assert problems(draft, CONFIG) == {"TDCC": draft.locked}
    assert receipt.lines[0].result == ImportResult.LOCKED
    assert product(pg, A)["source"] == "清單更新" and product(pg, A)["locked_at"] is not None


@pytest.mark.db
def test_order_import_takes_over_a_product_the_inventory_created_later(pg) -> None:
    """migration 0006 之後、清單更新停用（#87）之前由清單更新建立的商品。"""
    seed_products(pg, [inventory(A, [("北區", 300000)])])

    draft = draft_of(order_row(), pg)
    receipt = write_batch(pg, [draft], CONFIG, user="tester")

    assert draft.locked == ""
    line = receipt.lines[0]
    assert line.result == ImportResult.OVERWRITTEN and line.detail.startswith(
        "清單更新建立的商品改由起單資料匯入接手；改了："
    )
    p = product(pg, A)
    assert (p["source"], p["order_import_id"], p["issuer"]) == ("Order", receipt.import_id, "Barclays")
    assert p["coupon_pct"] == Decimal("12.5") and p["denomination"] == Decimal(10000)
    assert p["isin"] == "XS0000000001"  # ISIN 與分公司配置保留
    assert pg.execute("SELECT count(*) FROM workflow.branch_allocation WHERE tdcc = %s", (A,)).fetchone()[0] == 1


@pytest.mark.db
def test_rereading_keeps_manual_fills_and_shows_database_differences(pg) -> None:
    first = draft_of(order_row(changes={"Maturity date": None}), pg)
    first.values["Maturity date"] = dt.date(2027, 4, 22)
    write_batch(pg, [first], CONFIG, user="tester")

    again = draft_of(order_row(changes={"Maturity date": None, "Coupon p.a. (%)": Decimal(13)}), pg)

    assert again.values["Maturity date"] == dt.date(2027, 4, 22)  # 起單建檔空白 → 帶入資料庫的值
    assert again.values["Coupon p.a. (%)"] == Decimal(13)  # 兩邊不同 → 預設用起單建檔的值
    assert again.differences() == {"Coupon p.a. (%)": Decimal("12.5")}
    assert again.edits() == [("Maturity date", None, dt.date(2027, 4, 22))]


@pytest.mark.db
def test_reimporting_over_partial_database_terms(pg) -> None:
    """#114：資料庫的商品條件只有一部分（人工維護先存的）：只比兩邊都有值的欄，起單建檔也空白的必填欄照舊擋下，
    補齊後照常覆蓋。"""
    write_batch(pg, [draft_of(order_row(), pg)], CONFIG, user="tester")
    pg.execute(
        "UPDATE workflow.product SET maturity_date = NULL, coupon_pct = NULL, strike_pct = 85 WHERE tdcc = %s", (A,)
    )

    blank = draft_of(order_row(changes={"Maturity date": None}), pg)
    skipped = write_batch(pg, [blank], CONFIG, user="tester")
    full = draft_of(order_row(), pg)
    filled = write_batch(pg, [full], CONFIG, user="tester")

    assert blank.differences() == {"Strike (%)": Decimal(85)}
    assert (skipped.lines[0].result, skipped.lines[0].detail) == (ImportResult.SKIPPED, "Maturity date：必填")
    assert full.differences() == {"Strike (%)": Decimal(85)}
    assert (filled.lines[0].result, filled.lines[0].detail) == (
        ImportResult.OVERWRITTEN,
        "改了：Maturity date、Strike (%)、Coupon p.a. (%)",
    )
    p = product(pg, A)
    assert (p["maturity_date"], p["coupon_pct"], p["strike_pct"]) == (dt.date(2027, 4, 22), Decimal("12.5"), 80)


@pytest.mark.db
def test_conflicting_fields_are_not_filled_from_the_database(pg) -> None:
    write_batch(pg, [draft_of(order_row(), pg)], CONFIG, user="tester")

    draft = draft_of(order_row(changes={"Coupon p.a. (%)": None}, conflicts=("Coupon p.a. (%)",)), pg)

    assert draft.values["Coupon p.a. (%)"] is None
    assert problems(draft, CONFIG) == {"Coupon p.a. (%)": CONFLICT}


@pytest.mark.db
def test_an_unresolved_strike_date_is_not_filled_from_the_database(pg) -> None:
    write_batch(pg, [draft_of(order_row(), pg)], CONFIG, user="tester")
    row = dataclasses.replace(order_row(changes={"Strike date": None}), unresolved=frozenset({"Strike date"}))

    draft = draft_of(row, pg)
    receipt = write_batch(pg, [draft], CONFIG, user="tester")

    assert draft.values["Strike date"] is None
    assert (receipt.lines[0].result, receipt.lines[0].detail) == (
        ImportResult.SKIPPED,
        "Strike date：起單建檔算不出來，要填",
    )


@pytest.mark.db
def test_conflicting_fields_stay_blocked_when_the_product_appears_after_reading(pg) -> None:
    draft = draft_of(order_row(changes={"Coupon p.a. (%)": None}, conflicts=("Coupon p.a. (%)",)), pg)
    write_batch(pg, [draft_of(order_row(), pg)], CONFIG, user="tester")  # 讀取之後才有人匯入同一檔

    receipt = write_batch(pg, [draft], CONFIG, user="tester")

    assert (receipt.lines[0].result, receipt.lines[0].detail) == (ImportResult.SKIPPED, f"Coupon p.a. (%)：{CONFLICT}")


@pytest.mark.db
def test_blank_fields_do_not_wipe_values_the_operator_never_saw(pg) -> None:
    seed_mailbox(pg)
    write_batch(pg, [draft_of(order_row(changes={"Issue date": None}), pg)], CONFIG, user="tester")
    stale = draft_of(order_row(changes={"Issue date": None}), pg)  # 畫面打開時資料庫還沒有發行日
    pg.execute(
        "UPDATE workflow.product SET issue_date = '2026-10-22' WHERE tdcc = %s", (A,)
    )  # 之後才補上（例如核對回填）

    receipt = write_batch(pg, [stale], CONFIG, user="tester")

    assert receipt.lines[0].result == ImportResult.SAME
    assert product(pg, A)["issue_date"] == dt.date(2026, 10, 22)  # 之後補上的發行日沒被清掉

    seen = draft_of(order_row(changes={"Issue date": None}), pg)  # 這次畫面上看得到發行日，作業人員刻意清空
    seen.values["Issue date"] = None
    cleared = write_batch(pg, [seen], CONFIG, user="tester")

    assert (cleared.lines[0].result, cleared.lines[0].detail) == (ImportResult.OVERWRITTEN, "改了：Issue date")
    assert product(pg, A)["issue_date"] is None


@pytest.mark.db
def test_a_draft_read_without_the_database_cannot_overwrite_it(pg) -> None:
    write_batch(pg, [draft_of(order_row(), pg)], CONFIG, user="tester")
    offline = draft_of(order_row(changes={"Coupon p.a. (%)": Decimal(10)}))  # 讀取時沒連上資料庫

    receipt = write_batch(pg, [offline], CONFIG, user="tester")

    assert receipt.lines[0].result == ImportResult.SKIPPED
    assert receipt.lines[0].detail.startswith("讀取時沒有連上資料庫")
    assert product(pg, A)["coupon_pct"] == Decimal("12.5")


@pytest.mark.db
def test_kept_database_values_survive_a_second_import(pg) -> None:
    write_batch(pg, [draft_of(order_row(changes={"Issue date": None}), pg)], CONFIG, user="tester")
    stale = draft_of(order_row(changes={"Issue date": None}), pg)  # 畫面打開時資料庫還沒有發行日
    pg.execute("UPDATE workflow.product SET issue_date = '2026-10-22' WHERE tdcc = %s", (A,))  # 之後才補上

    write_batch(pg, [stale], CONFIG, user="tester")  # 保留之後補上的發行日
    stale.reload(read(pg, A))
    again = write_batch(pg, [stale], CONFIG, user="tester")  # 同一批再按一次匯入

    assert stale.values["Issue date"] == dt.date(2026, 10, 22), "畫面也跟著資料庫"
    assert again.lines[0].result == ImportResult.SAME
    assert product(pg, A)["issue_date"] == dt.date(2026, 10, 22)


@pytest.mark.db
def test_mail_draft_waits_for_the_issue_date(pg, tmp_path: Path) -> None:
    seed_mailbox(pg)
    write_batch(pg, [draft_of(order_row(changes={"Issue date": None}), pg)], CONFIG, user="tester")
    import_daily(write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1)]), pg)
    root, out = tmp_path / "FCN", tmp_path / "out"
    folder = root / A
    folder.mkdir(parents=True)
    out.mkdir()
    (folder / f"{A}_TS.pdf").write_bytes(b"%PDF ts")
    (folder / f"{A}_IIS.pdf").write_bytes(b"%PDF iis")

    receipt = create_drafts(A, root, out, pg, drafter=lambda draft: None, now=dt.datetime(2026, 10, 21, 9, 0))

    (outcome,) = receipt.outcomes
    assert outcome.status == MAIL_PENDING and "商品資料還沒有發行日（等核對回填）" in outcome.reason
