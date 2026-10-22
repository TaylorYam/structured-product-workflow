"""作業進度（#156、ADR 0019）：每檔商品走到九個作業進度的哪一步，由資料庫事實推出，不另外手填。

一檔一個進度：有「已寄出」一律完成；其餘依序判斷（`progress_of`），同一步有多筆紀錄取最新一筆：
等商品條件（沒有商品條件或缺必填欄）→ 等核對條件（不是「完成」）→ 待準備（沒有「已準備」）→
待核對（沒有核對結果）→ 核對未通過（有核對結果但還沒鎖住）→ 待放回（沒有「已放回」）→
待寄信（沒有「草稿已建立」）→ 待寄出。
商品的範圍是有交易明細或有商品條件的 TDCC。各步日期：交易明細匯入、商品條件匯入（Order 匯入、人工維護
存檔或清單更新）、核對條件完成（最後一次計算）、已準備、核對、鎖住、已放回、草稿已建立、已寄出。
只讀資料庫；匯出 Excel 的存檔方式同商品匯出（`product_export.save_rows`）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from sp_workflow.check_terms import DONE as TERMS_DONE
from sp_workflow.lifecycle import Objection, lifecycle_status, open_objections
from sp_workflow.order_import import ImportConfig, issuer_code
from sp_workflow.product_export import save_rows
from sp_workflow.product_record import PRODUCT_COLUMNS, missing_fields
from sp_workflow.step_records import DRAFTED_STEP, PREPARED_STEP, RETURNED_STEP

if TYPE_CHECKING:
    import psycopg

SENT_STEP = "已寄出"  # 進度頁登記（#157）

WAIT_TERMS = "等商品條件"
WAIT_CHECK_TERMS = "等核對條件"
TO_PREPARE = "待準備"
TO_CHECK = "待核對"
CHECK_FAILED = "核對未通過"
TO_RETURN = "待放回"
TO_MAIL = "待寄信"
TO_SEND = "待寄出"
FINISHED = "完成"
PROGRESS = (WAIT_TERMS, WAIT_CHECK_TERMS, TO_PREPARE, TO_CHECK, CHECK_FAILED, TO_RETURN, TO_MAIL, TO_SEND, FINISHED)

TRADES_IMPORTED = "交易明細匯入"
TERMS_IMPORTED = "商品條件匯入"
CHECK_TERMS_DONE = "核對條件完成"
CHECKED = "核對"
LOCKED = "鎖住"
DATE_COLUMNS = (
    TRADES_IMPORTED, TERMS_IMPORTED, CHECK_TERMS_DONE, PREPARED_STEP, CHECKED, LOCKED, RETURNED_STEP, DRAFTED_STEP,
    SENT_STEP,
)  # fmt: skip
EXPORT_COLUMNS = ("TDCC", "上手", "交易日", "距今天數", "作業進度", "存續狀態", "異議", *DATE_COLUMNS, "卡住的原因")


@dataclass(frozen=True)
class Facts:
    """一檔商品在資料庫裡的事實；`dates` 是 `DATE_COLUMNS` → 最新一筆的時間（沒有時不放）。"""

    tdcc: str
    has_terms: bool = False  # 有商品條件
    missing: tuple[str, ...] = ()  # 缺的必填欄（畫面的欄名）
    terms_status: str | None = None  # 核對條件狀態；還沒算過時是 None
    terms_reason: str | None = None
    checks: Mapping[str, str] = field(default_factory=dict)  # 核對結果：說明書／投資人須知 → 分類（通過、不一致…）
    dates: Mapping[str, dt.datetime] = field(default_factory=dict)


@dataclass(frozen=True)
class ProgressRow:
    """進度頁的一列。"""

    tdcc: str
    issuer: str  # 上手代號
    trade_date: dt.date | None
    days: int | None  # 距交易日天數（今天 − 交易日）
    progress: str
    lifecycle: str  # 存續狀態
    dates: tuple[dt.datetime | None, ...]  # 依 `DATE_COLUMNS`
    reason: str  # 卡住的原因
    objection: str = ""  # 還沒解除的異議：「待確認：備註」（#157）；沒有時空白


@dataclass(frozen=True)
class ProgressExport:
    path: Path
    count: int


def progress_of(facts: Facts) -> tuple[str, str]:
    """(作業進度, 卡住的原因)：照 ADR 0019 的表依序判斷，第一個不成立的就是目前的進度。"""
    dates = facts.dates
    if SENT_STEP in dates:
        return FINISHED, ""
    if not facts.has_terms:
        return WAIT_TERMS, "還沒有商品條件"
    if facts.missing:
        return WAIT_TERMS, f"缺：{'、'.join(facts.missing)}"
    if facts.terms_status != TERMS_DONE:
        if facts.terms_status is None:
            return WAIT_CHECK_TERMS, "還沒算核對條件"
        return WAIT_CHECK_TERMS, f"核對條件{facts.terms_status}" + (
            f"：{facts.terms_reason}" if facts.terms_reason else ""
        )
    if PREPARED_STEP not in dates:
        return TO_PREPARE, ""
    if not facts.checks:
        return TO_CHECK, ""
    if LOCKED not in dates:
        return CHECK_FAILED, "、".join(f"{kind}：{category}" for kind, category in sorted(facts.checks.items()))
    if RETURNED_STEP not in dates:
        return TO_RETURN, ""
    if DRAFTED_STEP not in dates:
        return TO_MAIL, ""
    return TO_SEND, ""


def load_progress(conn: psycopg.Connection, config: ImportConfig, today: dt.date) -> tuple[ProgressRow, ...]:
    """所有有交易明細或商品條件的商品的進度，依 TDCC 排（畫面再依天數排）。在同一個 transaction 裡讀。"""
    with conn.transaction():
        facts: dict[str, dict] = {}
        issuers: dict[str, str] = {}
        trade_dates: dict[str, dt.date | None] = {}

        def of(tdcc: str) -> dict:
            return facts.setdefault(tdcc, {"tdcc": tdcc, "dates": {}, "checks": {}})

        for tdcc, first_trade, imported in conn.execute(
            "SELECT t.tdcc, min(t.trade_date), max(r.imported_at) FROM workflow.trade_row t"
            " JOIN workflow.import_run r ON r.run_id = t.first_run_id GROUP BY t.tdcc"
        ):
            of(tdcc)["dates"][TRADES_IMPORTED] = imported
            trade_dates[tdcc] = first_trade
        columns = ", ".join(f"p.{c}" for c in PRODUCT_COLUMNS)
        for tdcc, locked_at, terms_imported, *row in conn.execute(
            f"SELECT p.tdcc, p.locked_at, CASE p.source WHEN 'Order' THEN o.imported_at WHEN '清單更新' THEN r.imported_at"
            " ELSE (SELECT max(c.corrected_at) FROM workflow.term_correction c WHERE c.tdcc = p.tdcc) END,"
            f" {columns} FROM workflow.product p"
            " LEFT JOIN workflow.order_import o ON o.import_id = p.order_import_id"
            " LEFT JOIN workflow.import_run r ON r.run_id = p.first_run_id"
        ):
            product = dict(zip(PRODUCT_COLUMNS, row, strict=True))
            item = of(tdcc)
            item.update(has_terms=True, missing=tuple(missing_fields(product)))
            _put(item["dates"], TERMS_IMPORTED, terms_imported)
            _put(item["dates"], LOCKED, locked_at)
            issuers[tdcc] = issuer_code(tdcc, product["issuer"], config)
            trade_dates[tdcc] = product["trade_date"] or trade_dates.get(tdcc)
        for tdcc, status, reason, calculated_at in conn.execute(
            "SELECT tdcc, status, reason, calculated_at FROM workflow.check_terms"
        ):
            item = of(tdcc)
            item.update(terms_status=status, terms_reason=reason)
            if status == TERMS_DONE:
                item["dates"][CHECK_TERMS_DONE] = calculated_at
        for tdcc, kind, category, checked_at in conn.execute(
            "SELECT tdcc, kind, category, checked_at FROM workflow.check_result"
        ):
            item = of(tdcc)
            item["checks"][kind] = category
            _latest(item["dates"], CHECKED, checked_at)
        for tdcc, step, recorded_at in conn.execute(
            "SELECT tdcc, step, max(recorded_at) FROM workflow.step_record GROUP BY tdcc, step"
        ):
            if tdcc in facts:  # 步驟紀錄沒有外鍵：交易明細與商品條件都沒有的 TDCC 不列
                facts[tdcc]["dates"][step] = recorded_at
        tdccs = sorted(facts)
        statuses = lifecycle_status(conn, tdccs)
        objections = open_objections(conn, tdccs)
    rows = []
    for tdcc in tdccs:
        found = Facts(**facts[tdcc])
        progress, reason = progress_of(found)
        trade_date = trade_dates.get(tdcc)
        rows.append(
            ProgressRow(
                tdcc,
                issuers.get(tdcc) or config.issuer_prefixes.get(tdcc[:3], ""),
                trade_date,
                None if trade_date is None else (today - trade_date).days,
                progress,
                statuses[tdcc].status,
                tuple(found.dates.get(name) for name in DATE_COLUMNS),
                reason,
                objection_text(objections.get(tdcc, ())),
            )
        )
    return tuple(rows)


def filter_rows(
    rows: Iterable[ProgressRow],
    *,
    first: dt.date | None = None,
    last: dt.date | None = None,
    progress: Collection[str] = (),
    lifecycle: Collection[str] = (),
    issuer: str = "",
) -> list[ProgressRow]:
    """篩選：交易日區間（含頭尾；有區間時沒有交易日的不列）、作業進度與存續狀態（多選，空的是全部）、上手。"""
    return [
        row
        for row in rows
        if (first is None or (row.trade_date is not None and row.trade_date >= first))
        and (last is None or (row.trade_date is not None and row.trade_date <= last))
        and (not progress or row.progress in progress)
        and (not lifecycle or row.lifecycle in lifecycle)
        and (not issuer or row.issuer == issuer)
    ]


def by_days(rows: Iterable[ProgressRow]) -> list[ProgressRow]:
    """預設排序：距交易日天數由大到小（落後的在上面），沒有交易日的放最後，同天數依 TDCC。"""
    return sorted(rows, key=lambda row: (row.days is None, -(row.days or 0), row.tdcc))


def export_name(now: dt.datetime | None = None) -> str:
    """存檔視窗預設的檔名。"""
    return f"作業進度_{now or dt.datetime.now():%Y%m%d_%H%M%S}.xlsx"


def export_progress(rows: Sequence[ProgressRow], path: Path) -> ProgressExport:
    """把（篩選後的）進度寫成 Excel，欄位同畫面；寫不進去時丟 `ProductExportError`、原本的同名檔不動。"""
    save_rows(path, "作業進度", EXPORT_COLUMNS, [export_values(row) for row in rows])
    return ProgressExport(path, len(rows))


def export_values(row: ProgressRow) -> tuple[object, ...]:
    """一列的值，依 `EXPORT_COLUMNS`。"""
    return (
        row.tdcc, row.issuer, row.trade_date, row.days, row.progress, row.lifecycle, row.objection, *row.dates,
        row.reason,
    )  # fmt: skip


def objection_text(objections: Iterable[Objection]) -> str:
    """進度頁的異議欄：有還沒解除的異議時是「待確認：備註」（多筆用「；」接）。"""
    notes = [f"{o.event} {o.event_date or ''}".strip() + f"：{o.note}" for o in objections]
    return f"待確認：{'；'.join(notes)}" if notes else ""


def _put(dates: dict[str, dt.datetime], name: str, value: dt.datetime | None) -> None:
    if value is not None:
        dates[name] = value


def _latest(dates: dict[str, dt.datetime], name: str, value: dt.datetime) -> None:
    if name not in dates or value > dates[name]:
        dates[name] = value
