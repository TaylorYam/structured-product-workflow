"""作業進度（#156、#186、ADR 0019）：每檔商品在五個階段的哪一個，由資料庫事實推出，不另外手填。

核對前要齊三件，彼此平行、沒有先後（`ready_of`）：交易明細（有這檔的交易明細）、商品條件（有、而且不缺必填欄；
每個標的都有進場價時期初價格空白不算缺，#182）、股價（核對條件是「完成」）。
一檔一個階段（`progress_of`），照順序判斷、第一個符合的就是：有「已寄出」或以前清單更新建立、已鎖住、沒有步驟紀錄
的舊商品（以前在 Excel 走完，#184）→ 完成；三件缺任一件 → 資料準備中；沒有核對結果 → 待核對；有核對結果、
還沒鎖住 → 待修正；已鎖住 → 待寄信。
商品的範圍是有交易明細或有商品條件的 TDCC。各步日期只顯示、不決定階段：交易明細匯入、商品條件匯入（Order 匯入、
人工維護存檔或清單更新）、核對條件完成（最後一次計算）、已準備、核對、鎖住、已放回、草稿已建立、已寄出；同一步有多筆
紀錄取最新一筆。批次登記已寄出（`sent_batch`）只登記交易日在選定日期以前（含）、目前是待寄信的商品；寫入前再用
同一套事實確認一次還是待寄信（`still_pending_mail`，#205）。
比價狀態（#170、ADR 0020）照 `workflow.pricing_status` 帶出：狀態、原因、已比到的日期（只講 KO）與 KI 欄（#196、ADR 0022）；
還沒比過的空白。
只讀資料庫；商品條件與標的價格經 `product_terms.read_many` 讀（#204），匯出 Excel 用 `file_tools.save_rows`。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from sp_workflow.database import require_in_transaction
from sp_workflow.file_tools import save_rows
from sp_workflow.lifecycle import Objection, lifecycle_status, open_objections
from sp_workflow.product_record import PRICING_FIELD, REQUIRED, missing_fields
from sp_workflow.product_terms import ImportConfig, issuer_code, locked, read_many
from sp_workflow.status import CheckTerms, OrderReview, Pricing, ProductSource, Progress
from sp_workflow.step_records import DRAFTED_STEP, PREPARED_STEP, RETURNED_STEP, SENT_STEP

if TYPE_CHECKING:
    import psycopg

_FLOW_STEPS = (PREPARED_STEP, RETURNED_STEP, DRAFTED_STEP, SENT_STEP)  # 新流程的步驟紀錄
_EXCEL_SOURCES = (ProductSource.INVENTORY, ProductSource.BACKFILL)  # 以前在 Excel 走完、建立時就鎖住的商品

PREPARING = Progress.PREPARING
TO_CHECK = Progress.TO_CHECK
TO_FIX = Progress.TO_FIX
TO_MAIL = Progress.TO_MAIL
FINISHED = Progress.FINISHED
PROGRESS = tuple(Progress)
_UNCHECKED = (PREPARING, TO_CHECK, TO_FIX)  # 還沒核對完（批次登記已寄出時提示）
READY_COLUMNS = ("交易明細", "商品條件", "股價")  # 核對前要齊的三件（#186），畫面打勾
# 每個標的都有進場價時的必填欄（#182）：期初價格（定價方式）只是用來取進場價，取到了就沒差
_PRICED_REQUIRED = tuple(name for name in REQUIRED if name != PRICING_FIELD)

TRADES_IMPORTED = "交易明細匯入"
TERMS_IMPORTED = "商品條件匯入"
CHECK_TERMS_DONE = "核對條件完成"
CHECKED = "核對"
LOCKED = "鎖住"
DATE_COLUMNS = (
    TRADES_IMPORTED, TERMS_IMPORTED, CHECK_TERMS_DONE, PREPARED_STEP, CHECKED, LOCKED, RETURNED_STEP, DRAFTED_STEP,
    SENT_STEP,
)  # fmt: skip
PRICING_COLUMNS = ("比價狀態", "KO比價", "比價原因", "KI")  # #170；比價三欄只講 KO，KI 另一欄（#196）
EXPORT_COLUMNS = (
    "TDCC", "上手", "交易日", "距今天數", "作業進度", *READY_COLUMNS, "存續狀態", *PRICING_COLUMNS, "異議", *DATE_COLUMNS,
    "卡住的原因",
)  # fmt: skip
TICK = "✓"


@dataclass(frozen=True)
class Facts:
    """一檔商品在資料庫裡的事實；`dates` 是 `DATE_COLUMNS` → 最新一筆的時間（沒有時不放）。"""

    tdcc: str
    has_trades: bool = False  # 有交易明細
    has_terms: bool = False  # 有商品條件
    locked_inventory: bool = False  # 以前清單更新建立或歷史回補（#303）、已鎖住的舊商品（ADR 0020）
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
    objection: str = ""  # 還沒解除的異議：「待確認：事件 事件日：備註」（#157）；沒有時空白
    pricing: str = ""  # 比價狀態（#170）：在庫、等股價、待確認、已出場；還沒比過空白
    pricing_reason: str = ""  # 等股價的標的與日期、待確認的原因、出場的事件
    observed_through: dt.date | None = None  # 已比到的日期
    pricing_unlocked: bool = False  # 比價時還沒鎖住
    ki: str = ""  # KI 欄（#196、#198）：-（沒有 KI）、EKI、AKI、MKI (manual)、`AKI hit 日期 標的`；還沒比過空白
    ready: tuple[bool, ...] = (False,) * len(READY_COLUMNS)  # 依 `READY_COLUMNS`：三件齊了沒

    @property
    def ready_marks(self) -> tuple[str, ...]:
        """畫面與匯出的三欄打勾：齊了打勾，沒齊空白。"""
        return tuple(TICK if ready else "" for ready in self.ready)

    @property
    def pricing_text(self) -> str:
        """畫面與匯出的比價狀態：比價時還沒鎖住的後面標「（未鎖住）」。"""
        return f"{self.pricing}（{OrderReview.UNLOCKED}）" if self.pricing and self.pricing_unlocked else self.pricing

    @property
    def problem(self) -> bool:
        """畫面用問題色：還沒完成又有卡住的原因，或比價狀態是等股價、待確認。"""
        return (self.progress != FINISHED and bool(self.reason)) or self.pricing in (
            Pricing.WAIT_PRICES,
            Pricing.TO_CONFIRM,
        )


@dataclass(frozen=True)
class ProgressExport:
    path: Path
    count: int


def progress_of(facts: Facts) -> tuple[str, str]:
    """(作業進度, 卡住的原因)：照 ADR 0019 的表依序判斷，第一個符合的就是目前的階段。"""
    dates = facts.dates
    if SENT_STEP in dates:
        return FINISHED, ""
    if facts.locked_inventory and not any(name in dates for name in _FLOW_STEPS):
        return FINISHED, ""  # 以前在 Excel 走完（#184）；走過新流程的照下面的順序
    gaps = _gaps(facts)
    if gaps:
        return PREPARING, "；".join(gaps.values())
    if not facts.checks:
        return TO_CHECK, ""
    if LOCKED not in dates:
        return TO_FIX, "、".join(f"{kind}：{category}" for kind, category in sorted(facts.checks.items()))
    return TO_MAIL, ""


def ready_of(facts: Facts) -> tuple[bool, ...]:
    """核對前的三件（依 `READY_COLUMNS`）齊了沒。"""
    gaps = _gaps(facts)
    return tuple(name not in gaps for name in READY_COLUMNS)


def _gaps(facts: Facts) -> dict[str, str]:
    """三件裡還沒齊的：件 → 缺什麼（依 `READY_COLUMNS` 的順序）；都齊了是空的。"""
    trades, terms, prices = READY_COLUMNS
    gaps = {}
    if not facts.has_trades:
        gaps[trades] = "還沒有交易明細"
    if not facts.has_terms:
        gaps[terms] = "還沒有商品條件"
    elif facts.missing:
        gaps[terms] = f"缺：{'、'.join(facts.missing)}"
    if facts.terms_status is None:
        gaps[prices] = "還沒算核對條件"
    elif facts.terms_status != CheckTerms.DONE:
        gaps[prices] = f"核對條件{facts.terms_status}" + (f"：{facts.terms_reason}" if facts.terms_reason else "")
    return gaps


@dataclass
class _AllFacts:
    """`_read_facts` 讀到的：每檔的事實（`Facts` 的欄）、資料庫的上手欄與交易日。"""

    facts: dict[str, dict] = field(default_factory=dict)
    issuers: dict[str, str | None] = field(default_factory=dict)
    trade_dates: dict[str, dt.date | None] = field(default_factory=dict)


def _read_facts(conn: psycopg.Connection) -> _AllFacts:
    """所有有交易明細或商品條件的商品的事實（判斷階段用的）；在呼叫端的 transaction 裡讀。"""
    require_in_transaction(conn)
    found = _AllFacts()
    facts, trade_dates = found.facts, found.trade_dates

    def of(tdcc: str) -> dict:
        return facts.setdefault(tdcc, {"tdcc": tdcc, "dates": {}, "checks": {}})

    for tdcc, first_trade, imported in conn.execute(
        "SELECT t.tdcc, min(t.trade_date), max(r.imported_at) FROM workflow.trade_row t"
        " JOIN workflow.import_run r ON r.run_id = t.first_run_id GROUP BY t.tdcc"
    ):
        item = of(tdcc)
        item["dates"][TRADES_IMPORTED] = imported
        item["has_trades"] = True
        trade_dates[tdcc] = first_trade
    terms_imported = dict(
        conn.execute(  # 商品條件匯入的日期：Order 匯入、清單更新、歷史回補（#303）或最後一次人工維護存檔
            "SELECT p.tdcc, CASE p.source WHEN 'Order' THEN o.imported_at WHEN '清單更新' THEN r.imported_at"
            " WHEN '歷史回補' THEN p.locked_at"
            " ELSE (SELECT max(c.corrected_at) FROM workflow.term_correction c WHERE c.tdcc = p.tdcc) END"
            " FROM workflow.product p"
            " LEFT JOIN workflow.order_import o ON o.import_id = p.order_import_id"
            " LEFT JOIN workflow.import_run r ON r.run_id = p.first_run_id"
        ).fetchall()
    )
    for tdcc, stored in read_many(conn).items():
        product = stored.columns
        item = of(tdcc)
        initial_prices = {price.seq: price.initial for price in stored.prices}
        required = _PRICED_REQUIRED if _has_initial_prices(product, initial_prices) else REQUIRED
        item.update(
            has_terms=True,
            locked_inventory=stored.source in _EXCEL_SOURCES and locked(stored),
            missing=tuple(missing_fields(product, required)),
        )
        _put(item["dates"], TERMS_IMPORTED, terms_imported.get(tdcc))
        _put(item["dates"], LOCKED, stored.locked_at)
        found.issuers[tdcc] = product["issuer"]
        trade_dates[tdcc] = product["trade_date"] or trade_dates.get(tdcc)
    for tdcc, status, reason, calculated_at in conn.execute(
        "SELECT tdcc, status, reason, calculated_at FROM workflow.check_terms"
    ):
        item = of(tdcc)
        item.update(terms_status=status, terms_reason=reason)
        if status == CheckTerms.DONE:
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
    return found


def still_pending_mail(conn: psycopg.Connection, tdccs: Sequence[str]) -> tuple[str, ...]:
    """這些 TDCC 裡目前還是待寄信的（和 `load_progress` 同一套事實與 `progress_of`），依傳入的順序、不重複。

    批次登記已寄出寫入前用（#205）：找出這批之後才寄出、被更正解鎖或條件變了的檔不再是待寄信，跳過。在同一個 transaction 裡讀。
    """
    with conn.transaction():
        facts = _read_facts(conn).facts
    return tuple(t for t in dict.fromkeys(tdccs) if t in facts and progress_of(Facts(**facts[t]))[0] == TO_MAIL)


def load_progress(conn: psycopg.Connection, config: ImportConfig, today: dt.date) -> tuple[ProgressRow, ...]:
    """所有有交易明細或商品條件的商品的進度，依 TDCC 排（畫面再依天數排）。在同一個 transaction 裡讀。"""
    with conn.transaction():
        known = _read_facts(conn)
        facts, trade_dates = known.facts, known.trade_dates
        pricing = {
            tdcc: {
                "pricing": status,
                "pricing_reason": reason or "",
                "observed_through": through,
                "pricing_unlocked": not locked,
                "ki": ki or "",
            }
            for tdcc, status, reason, through, locked, ki in conn.execute(
                "SELECT tdcc, status, reason, observed_through, locked, ki FROM workflow.pricing_status"
            )
        }
        tdccs = sorted(facts)
        statuses = lifecycle_status(conn, tdccs)
        objections = open_objections(conn, tdccs)
    issuers = {tdcc: issuer_code(tdcc, issuer, config) for tdcc, issuer in known.issuers.items()}
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
                **pricing.get(tdcc, {}),
                ready=ready_of(found),
            )
        )
    return tuple(rows)


def _has_initial_prices(product: Mapping[str, object], prices: Mapping[int, Decimal | None]) -> bool:
    """商品有填的每個標的（UL_1～UL_5）都有大於 0 的進場價（`prices` 是標的序號 → 進場價）。"""
    seqs = [n for n in range(1, 6) if product[f"underlying_{n}"] is not None]
    return bool(seqs) and all((prices.get(n) or 0) > 0 for n in seqs)


def filter_rows(
    rows: Iterable[ProgressRow],
    *,
    first: dt.date | None = None,
    last: dt.date | None = None,
    progress: Collection[str] = (),
    lifecycle: Collection[str] = (),
    pricing: Collection[str] = (),
    issuer: str = "",
) -> list[ProgressRow]:
    """篩選：交易日區間（含頭尾；有區間時沒有交易日的不列）、作業進度、存續狀態與比價狀態（多選，空的是全部；
    選了比價狀態時還沒比過的不列）、上手。"""
    return [
        row
        for row in rows
        if (first is None or (row.trade_date is not None and row.trade_date >= first))
        and (last is None or (row.trade_date is not None and row.trade_date <= last))
        and (not progress or row.progress in progress)
        and (not lifecycle or row.lifecycle in lifecycle)
        and (not pricing or row.pricing in pricing)
        and (not issuer or row.issuer == issuer)
    ]


@dataclass(frozen=True)
class SentBatch:
    """批次登記已寄出（#186）的範圍：交易日在 `through` 以前（含）的商品裡，要登記的與還沒核對完、這次不登記的。"""

    through: dt.date
    tdccs: tuple[str, ...]  # 目前是待寄信的，依 TDCC
    unchecked: int  # 還沒核對完（資料準備中、待核對、待修正）的檔數


def sent_batch(rows: Iterable[ProgressRow], through: dt.date) -> SentBatch:
    """交易日在 `through` 以前（含）、目前是待寄信的商品；沒有交易日的（例如還沒有交易明細）兩邊都不算。"""
    due = [row for row in rows if row.trade_date is not None and row.trade_date <= through]
    return SentBatch(
        through,
        tuple(sorted(row.tdcc for row in due if row.progress == TO_MAIL)),
        sum(row.progress in _UNCHECKED for row in due),
    )


def to_confirm_tdccs(rows: Iterable[ProgressRow]) -> list[str]:
    """比價狀態是待確認的 TDCC（#289），照列的順序、重複的只取一次；未鎖住的也算。"""
    return list(dict.fromkeys(row.tdcc for row in rows if row.pricing == Pricing.TO_CONFIRM))


def by_trade_date(rows: Iterable[ProgressRow]) -> list[ProgressRow]:
    """預設排序：交易日由新至舊（最近的交易在上面），沒有交易日的放最後，同一天依 TDCC。"""
    return sorted(
        rows, key=lambda row: (row.trade_date is None, -(row.trade_date or dt.date.min).toordinal(), row.tdcc)
    )


def export_name(now: dt.datetime | None = None) -> str:
    """存檔視窗預設的檔名。"""
    return f"作業進度_{now or dt.datetime.now():%Y%m%d_%H%M%S}.xlsx"


def export_progress(rows: Sequence[ProgressRow], path: Path) -> ProgressExport:
    """把（篩選後的）進度寫成 Excel，欄位同畫面；寫不進去時丟 `FileWriteError`、原本的同名檔不動。"""
    save_rows(path, "作業進度", EXPORT_COLUMNS, [export_values(row) for row in rows])
    return ProgressExport(path, len(rows))


def export_values(row: ProgressRow) -> tuple[object, ...]:
    """一列的值，依 `EXPORT_COLUMNS`。"""
    return (
        row.tdcc, row.issuer, row.trade_date, row.days, row.progress, *row.ready_marks, row.lifecycle,
        row.pricing_text, row.observed_through, row.pricing_reason, row.ki, row.objection, *row.dates, row.reason,
    )  # fmt: skip


def objection_text(objections: Iterable[Objection]) -> str:
    """進度頁的異議欄：有還沒解除的異議時是「待確認：事件 事件日：備註」（多筆用「；」接）。"""
    notes = [f"{o.event} {o.event_date or ''}".strip() + f"：{o.note}" for o in objections]
    return f"待確認：{'；'.join(notes)}" if notes else ""


def _put(dates: dict[str, dt.datetime], name: str, value: dt.datetime | None) -> None:
    if value is not None:
        dates[name] = value


def _latest(dates: dict[str, dt.datetime], name: str, value: dt.datetime) -> None:
    if name not in dates or value > dates[name]:
        dates[name] = value
