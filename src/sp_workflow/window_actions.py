"""視窗按鈕背後的動作（#125）：每個作業員動作一個 function，吃連線設定與輸入、回傳結果。

連資料庫、transaction 的邊界與容錯都在這裡；視窗（`app.py`）只收輸入、在背景呼叫一個動作、顯示結果。
匯入成功、之後的重算失敗時匯入保留（ADR 0012）：重算的錯誤不丟出，原因放在結果裡；起單資料匯入之後還重讀每檔資料庫的值。
寄信草稿分兩個動作（讀最近一次匯入 → 建草稿），中間的確認對話框留在視窗。
準備、放回、建草稿的步驟紀錄（#151）在清單產出之後另寫；寫入失敗不丟出（清單已經產出），原因放在結果裡。
收盤價（#153）要用交易所假日表判斷休市日：回補股價讀不到假日表時照樣匯股價、收盤價不存；
歷史收盤價讀不到假日表就整份不匯入。
比價（#155）在回補股價與歷史收盤價匯入之後跑；比價失敗時匯入保留，原因放在結果裡。
信箱維護（#158）每次存檔一個動作，寫完重讀全部人員與別名給畫面重新顯示；發行機構評等（#237）也一樣。
拆股登記（#270）存檔後同樣重讀，並比價全部（受影響、由比價判定出場的商品重新比）；比價失敗時登記保留，原因放在結果裡。
可連結標的白名單（#273）分兩個動作（讀目前這一份 → 匯入），中間的確認與選檔留在視窗；匯入整份一個 transaction，
之後的重算失敗時匯入保留。
transaction 的範圍（#205）：每日匯入、回補股價、歷史收盤價、起單資料匯入、舊商品補資料與準備、放回、建草稿的步驟紀錄
整批一個；進度頁的登記已寄出（含批次）、顧客贖回一檔一個（`_each`），寫不進去只影響那一檔、之前寫好的保留。
這裡不自己寫 SQL：要判斷進度、查已寄出過的，問 `progress`、`step_records`。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from fcn_checker.check_config import ConfigPaths
from fcn_checker.config import load_reference_format
from fcn_checker.ingestion import IngestionError
from fcn_checker.trading_calendar import TradingCalendar
from sp_workflow.check_terms import (
    CalcLine,
    CalcReceipt,
    CheckTermsError,
    Difference,
    load_calendar,
    recalculate,
    release_differences,
)
from sp_workflow.close_prices import HistoryReceipt, import_history
from sp_workflow.credit_ratings import Entity, Rating, RatingRows, add_entity, add_rating, delete_latest, edit_latest
from sp_workflow.credit_ratings import load_rows as load_ratings
from sp_workflow.daily_import import (
    ImportInfo,
    ImportReceipt,
    PriceCsvReceipt,
    import_daily,
    import_price_csv,
    latest_import,
)
from sp_workflow.database import ConnectionSettings, DatabaseError, SchemaOutdatedError, connect
from sp_workflow.eln_whitelist import (
    WhitelistImport,
    WhitelistReceipt,
    current_import,
    import_whitelist,
    read_whitelist,
)
from sp_workflow.exits import ExitList, load_exits
from sp_workflow.legacy_backfill import BackfillReceipt, LegacyBackfillError, backfill_legacy
from sp_workflow.lifecycle import (
    OBSERVATION,
    LifecycleError,
    Objection,
    StoredEvent,
    current_events,
    lifecycle_status,
    open_objections,
    raise_objection,
    record_redemption,
    resolve_objection,
)
from sp_workflow.mail_drafts import DraftReceipt, create_drafts
from sp_workflow.mailbox import (
    Alias,
    MailboxRows,
    Person,
    delete_alias,
    delete_person,
    load_rows,
    save_alias,
    save_person,
)
from sp_workflow.observation import ObservationReceipt, observe_all
from sp_workflow.order_terms import OrderRow, collect_orders
from sp_workflow.preparation import PrepareReceipt, prepare_batch
from sp_workflow.product_export import ExportReceipt, export_products
from sp_workflow.product_terms import (
    BatchReceipt,
    Draft,
    ImportConfig,
    MissingProduct,
    Receipt,
    TermsError,
    draft_from,
    load_import_config,
    missing_products,
    read,
    write,
    write_batch,
)
from sp_workflow.progress import (
    ProgressRow,
    SentBatch,
    load_progress,
    sent_batch,
    still_pending_mail,
)
from sp_workflow.returning import ReturnReceipt, return_to_folders
from sp_workflow.risk_level import RiskLevel, risk_levels
from sp_workflow.step_records import (
    SENT_STEP,
    Recorded,
    StepRecord,
    already_sent,
    drafted_records,
    prepared_records,
    returned_records,
    write_records,
)
from sp_workflow.stock_splits import Split, add_split, adjusted_count, affected_exits, delete_split, load_splits
from sp_workflow.trade_rows import TradeRowsDraft, load_trade_rows

if TYPE_CHECKING:
    import psycopg


@dataclass(frozen=True)
class Recalc:
    """匯入後重算核對條件（#88）與風險等級（#238）的結果；重算失敗時只有 error（匯入已經寫進資料庫）。"""

    receipt: CalcReceipt | None = None
    config: ImportConfig | None = None  # 放行後重算要用
    error: str = ""


@dataclass(frozen=True)
class Observation:
    """匯入收盤價後的比價（#155）結果；比價失敗時只有 error（匯入已經寫進資料庫）。"""

    receipt: ObservationReceipt | None = None
    error: str = ""


@dataclass(frozen=True)
class DailyResult:
    receipt: ImportReceipt
    recalc: Recalc = Recalc()


@dataclass(frozen=True)
class PriceCsvResult:
    receipt: PriceCsvReceipt
    recalc: Recalc = Recalc()
    observation: Observation = Observation()


@dataclass(frozen=True)
class HistoryResult:
    receipt: HistoryReceipt
    observation: Observation = Observation()


@dataclass(frozen=True)
class LegacyResult:
    """匯入舊商品的價格與比價日（#154）與之後的比價。"""

    receipt: BackfillReceipt
    observation: Observation = Observation()


@dataclass(frozen=True)
class SplitResult:
    """拆股登記存檔（#270）後的全部登記與比價結果。"""

    splits: tuple[Split, ...]
    observation: Observation = Observation()
    adjusted: int = 0  # 這個代碼的價格已經含這次拆股的標的數（#275；比價跑了才算出）


@dataclass(frozen=True)
class WhitelistResult:
    """匯入可連結標的白名單（#273）與之後的重算。"""

    receipt: WhitelistReceipt
    recalc: Recalc = Recalc()


@dataclass(frozen=True)
class PrepareResult:
    receipt: PrepareReceipt
    recorded: Recorded = Recorded()


@dataclass(frozen=True)
class ReturnResult:
    receipt: ReturnReceipt
    recorded: Recorded = Recorded()


@dataclass(frozen=True)
class DraftResult:
    receipt: DraftReceipt
    recorded: Recorded = Recorded()


@dataclass(frozen=True)
class ReadResult:
    rows: tuple[OrderRow, ...]
    drafts: list[Draft]
    config: ImportConfig
    database_note: str = ""  # 沒有帶入資料庫的值時的說明
    database_error: DatabaseError | None = None
    risks: Mapping[str, RiskLevel] = field(default_factory=dict)  # 資料庫存的風險等級（#238；沒連資料庫時空的）


@dataclass(frozen=True)
class OrderImportResult:
    """按一次「匯入資料庫」的結果：匯入、重算核對條件（#88）、重讀資料庫的值。

    重算和匯入分開兩個 transaction（ADR 0012）：重算失敗時匯入照樣保留，`recalc` 是 None、`recalc_error` 寫原因。
    """

    receipt: BatchReceipt
    recalc: CalcReceipt | None = None
    recalc_error: str = ""
    reload_error: str = ""  # 匯入成功但重讀資料庫狀態失敗
    risks: Mapping[str, RiskLevel] = field(default_factory=dict)  # 重讀的風險等級（#238；重讀失敗時空的）


@dataclass(frozen=True)
class Registered:
    """進度頁一次登記（#157）的結果：寫好的 TDCC 與寫不進去的 (TDCC, 原因)。"""

    done: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class EventChoices:
    """一檔可以提出異議的自動事件與還沒解除的異議（#157）。"""

    tdcc: str
    events: tuple[StoredEvent, ...]  # 還有效、來源比價的事件
    objections: tuple[Objection, ...]


@dataclass(frozen=True)
class Checked:
    """人工維護「檢查」的結果（#115）。"""

    products: tuple[MissingProduct, ...]
    config: ImportConfig


@dataclass(frozen=True)
class Opened:
    """人工維護打開的一檔（#115）與它的交易明細（#116）。"""

    draft: Draft
    config: ImportConfig
    trades: TradeRowsDraft | None = None


def _recalculate_after_import(conn: psycopg.Connection, paths: ConfigPaths) -> Recalc:
    """匯入之後重算；讀不到設定檔（假日表、上手編號對照、風險等級設定等）或重算失敗時不丟錯誤（匯入已經完成），
    說明放在結果裡。"""
    try:
        config = load_import_config(paths)
    except TermsError as exc:
        return Recalc(error=_first_line(exc))
    return _recalculate(conn, config)


def _recalculate(conn: psycopg.Connection, config: ImportConfig) -> Recalc:
    import psycopg

    try:
        return Recalc(recalculate(conn, config), config)
    except (CheckTermsError, psycopg.Error) as exc:
        return Recalc(error=_first_line(exc))


def _observe_after_write(
    conn: psycopg.Connection, calendar: TradingCalendar | None, revisit: Collection[str] = ()
) -> Observation:
    """寫好收盤價、舊商品或拆股登記之後比價全部（`revisit` 的已出場商品重新比）；
    讀不到假日表或比價失敗時不丟錯誤（寫入已經完成），說明放在結果裡。"""
    import psycopg

    if calendar is None:
        return Observation(error="讀不到交易所假日表，這次沒有比價")
    try:
        return Observation(observe_all(conn, calendar, revisit=revisit))
    except (LifecycleError, psycopg.Error) as exc:
        return Observation(error=_first_line(exc))
    except Exception as exc:  # 程式錯誤也不能讓已經寫好的資料跟著失敗
        return Observation(error=f"比價程式出錯（{type(exc).__name__}：{_first_line(exc)}）")


# ---------- 1 每日匯入 ----------


def run_daily_import(settings: ConnectionSettings, daily: Path, paths: ConfigPaths, *, prices: bool) -> DailyResult:
    """匯入每日更新（`prices` 為 False 時只匯交易明細），再重算核對條件。"""
    with connect(settings) as conn:
        receipt = import_daily(daily, conn, prices=prices)
        return DailyResult(receipt, _recalculate_after_import(conn, paths))


def run_price_csv(settings: ConnectionSettings, source: Path, paths: ConfigPaths) -> PriceCsvResult:
    """回補股價（#112）：匯入一個 MarketData CSV（同時寫收盤價，#153），再重算核對條件、比價（#155）；交易明細不動。"""
    try:
        calendar = load_calendar(paths.exchange_holidays)
    except CheckTermsError:
        calendar = None  # 收盤價這次不存；讀不到的原因由重算寫在結果裡
    with connect(settings) as conn:
        receipt = import_price_csv(source, conn, calendar)
        recalc = _recalculate_after_import(conn, paths)
        return PriceCsvResult(receipt, recalc, _observe_after_write(conn, calendar))


def import_close_history(settings: ConnectionSettings, source: Path, paths: ConfigPaths) -> HistoryResult:
    """匯入歷史收盤價（#153，維護選單），再比價（#155）：讀不到假日表、檔案格式不對或寫不出錯誤清單就整份不匯入。"""
    calendar = load_calendar(paths.exchange_holidays)
    with connect(settings) as conn:
        receipt = import_history(source, conn, calendar)
        return HistoryResult(receipt, _observe_after_write(conn, calendar))


def import_legacy_products(settings: ConnectionSettings, source: Path, paths: ConfigPaths) -> LegacyResult:
    """匯入舊商品的價格與比價日（#154，維護選單），再比價（#155）：讀不到參考條件表格式、檔案不對就整份不匯入。"""
    try:
        fmt = load_reference_format(paths.reference_format)
    except IngestionError as exc:
        raise LegacyBackfillError(f"{exc}，整份不匯入") from exc
    try:
        calendar = load_calendar(paths.exchange_holidays)
    except CheckTermsError:
        calendar = None  # 照樣匯入；比價寫「讀不到交易所假日表」
    with connect(settings) as conn:
        receipt = backfill_legacy(conn, source, fmt)
        return LegacyResult(receipt, _observe_after_write(conn, calendar))


def read_whitelist_import(settings: ConnectionSettings) -> WhitelistImport | None:
    """資料庫目前這一份可連結標的白名單（最近一次匯入，#273）；還沒匯入過時 None。"""
    with connect(settings) as conn:
        return current_import(conn)


def import_eln_whitelist(settings: ConnectionSettings, source: Path, paths: ConfigPaths) -> WhitelistResult:
    """匯入可連結標的白名單（#273，維護選單）：整份檢查完才取代資料庫的白名單（格式不對丟 `WhitelistError`、不寫入），
    再重算還沒鎖住的商品（風險等級的 ETF 清單跟著換）。"""
    whitelist = read_whitelist(source)
    with connect(settings) as conn:
        receipt = import_whitelist(conn, whitelist, source)
        return WhitelistResult(receipt, _recalculate_after_import(conn, paths))


def observe_products(settings: ConnectionSettings, paths: ConfigPaths) -> ObservationReceipt:
    """重新比價全部（#155，維護選單）：讀不到假日表時丟 `CheckTermsError`。"""
    calendar = load_calendar(paths.exchange_holidays)
    with connect(settings) as conn:
        return observe_all(conn, calendar)


def release_line(
    settings: ConnectionSettings, tdcc: str, differences: Sequence[Difference], config: ImportConfig
) -> CalcLine:
    """放行差異（#88）：記下放行人與時間，再重算那一檔（核對條件與風險等級）。"""
    with connect(settings) as conn:
        return release_differences(conn, tdcc, differences, config)


def _record_steps(settings: ConnectionSettings | None, records: Sequence[StepRecord]) -> Recorded:
    """清單產出之後寫步驟紀錄；沒有連線設定（只用 Excel 跑）時不寫，連不上或寫入失敗時原因放在結果裡。

    沒有要寫的（例如整批跳過）不連資料庫。
    """
    if settings is None:
        return Recorded(skipped=True)
    if not records:
        return Recorded()
    try:
        conn = connect(settings)
    except SchemaOutdatedError as exc:  # 不是登入問題，不用重問密碼
        return Recorded(error=_first_line(exc))
    except DatabaseError as exc:
        return Recorded(error=_first_line(exc), database_error=exc)
    with conn:
        return _record_with(conn, records)


def _record_with(conn: psycopg.Connection, records: Sequence[StepRecord]) -> Recorded:
    import psycopg

    try:
        return Recorded(write_records(conn, records))
    except psycopg.Error as exc:
        return Recorded(error=_first_line(exc))


# ---------- 2 起單資料 ----------


def read_order_sheet(settings: ConnectionSettings | None, text: str, sheet: Path, paths: ConfigPaths) -> ReadResult:
    """讀起單建檔；能連資料庫就帶入資料庫的值，沒有連線設定（`settings` 是 None）或連不上時照樣讀、寫說明。"""
    config = load_import_config(paths)
    rows = collect_orders(text, sheet, config.issuer_prefixes, config.calendar)
    if settings is None:
        return ReadResult(rows, _drafts(rows, config), config, "沒有登入資料庫，沒有帶入資料庫的值")
    try:
        with connect(settings) as conn:
            risks = risk_levels(conn, [row.tdcc for row in rows if row.found])
            return ReadResult(rows, _drafts(rows, config, conn), config, risks=risks)
    except DatabaseError as exc:
        note = f"資料庫沒有連上，沒有帶入資料庫的值（{exc}）"
        login = None if isinstance(exc, SchemaOutdatedError) else exc  # 版本不對不是登入問題
        return ReadResult(rows, _drafts(rows, config), config, note, login)


def _drafts(rows: Sequence[OrderRow], config: ImportConfig, conn: psycopg.Connection | None = None) -> list[Draft]:
    """找到的每一檔轉成確認資料；有連線時帶入資料庫的值與鎖住狀態。找不到的列沒有確認資料，畫面直接顯示讀取的問題。"""
    return [
        draft_from(row, None if conn is None else read(conn, row.tdcc), config, checked=conn is not None)
        for row in rows
        if row.found
    ]


def import_order_sheet(
    settings: ConnectionSettings, drafts: Sequence[Draft], config: ImportConfig
) -> OrderImportResult:
    """匯入確認過的起單資料（`write_batch`），再重算還沒鎖住的商品、重讀每檔資料庫的值與鎖住狀態。

    匯入失敗時丟 `TermsError`（或資料庫的錯誤），什麼都不寫；匯入之後的重算、重讀失敗不丟錯誤，
    匯入已經寫進資料庫，原因放在結果裡。
    """
    import psycopg

    with connect(settings) as conn:
        receipt = write_batch(conn, drafts, config)
        recalc = _recalculate(conn, config)
        try:
            for draft in drafts:
                draft.reload(read(conn, draft.tdcc))
            risks = risk_levels(conn, [draft.tdcc for draft in drafts])
        except psycopg.Error as exc:
            return OrderImportResult(receipt, recalc.receipt, recalc.error, _first_line(exc))
        return OrderImportResult(receipt, recalc.receipt, recalc.error, risks=risks)


# ---------- 3 準備、5 放回 ----------


def run_prepare(settings: ConnectionSettings | None, text: str, root: Path, out: Path) -> PrepareResult:
    """準備批次（產生批次資料夾與準備清單），再為已準備的每檔留步驟紀錄。"""
    receipt = prepare_batch(text, root, out)
    return PrepareResult(receipt, _record_steps(settings, prepared_records(receipt)))


def run_return(settings: ConnectionSettings | None, batch: Path, result: Path) -> ReturnResult:
    """放回商品資料夾（產生放回清單），再為放回的每檔留步驟紀錄。"""
    receipt = return_to_folders(batch, result)
    return ReturnResult(receipt, _record_steps(settings, returned_records(receipt)))


# ---------- 6 寄信草稿 ----------


def read_latest_import(settings: ConnectionSettings) -> ImportInfo | None:
    """建草稿前先讓作業人員確認用的是哪一次每日匯入。"""
    with connect(settings) as conn:
        return latest_import(conn)


def make_mail_drafts(
    settings: ConnectionSettings, text: str, root: Path, out: Path, *, template_path: Path | None
) -> DraftResult:
    """在 Outlook 建 TS／IIS 草稿（#29），結果清單存在 `out` 底下；再為建了草稿的每檔留步驟紀錄。"""
    with connect(settings) as conn:
        receipt = create_drafts(text, root, out, conn, template_path=template_path)
        return DraftResult(receipt, _record_with(conn, drafted_records(receipt)))


# ---------- 7 人工維護 ----------


def check_missing(settings: ConnectionSettings, paths: ConfigPaths) -> Checked:
    """列出還沒鎖住、缺資料的商品。"""
    config = load_import_config(paths)
    with connect(settings) as conn:
        return Checked(missing_products(conn, config), config)


def open_product(settings: ConnectionSettings, tdcc: str, paths: ConfigPaths) -> Opened:
    """讀一檔目前的商品條件與交易明細；兩邊都沒有這檔時丟 `TermsError`。"""
    config = load_import_config(paths)
    with connect(settings) as conn:
        stored = read(conn, tdcc)
        trades = load_trade_rows(conn, tdcc)
    if stored is None and not trades.rows:
        raise TermsError(f"{tdcc}：交易明細和商品條件都沒有這檔，不能在這裡新增")
    return Opened(draft_from(tdcc, stored, config), config, trades)


def save_product(
    settings: ConnectionSettings,
    draft: Draft,
    config: ImportConfig,
    *,
    trades: TradeRowsDraft | None,
    reason: str,
) -> Receipt:
    """人工維護存檔（已鎖住的照更正的規則），商品條件與交易明細一起。

    改了商品條件、這檔已出場時，結果另外寫出存續事件（#169）：已出場的不再比價，事件不會跟著改。
    """
    with connect(settings) as conn:
        receipt = write(conn, draft, config, trades=trades, reason=reason)
        if receipt.correction_id is None:
            return receipt
        latest = lifecycle_status(conn, [receipt.tdcc])[receipt.tdcc].latest
        if latest is None:
            return receipt
        when = latest.event_date or "沒有事件日"
        return replace(receipt, exited=" ".join(str(p) for p in (latest.event, latest.underlying, when) if p))


def export_product_terms(settings: ConnectionSettings, path: Path) -> ExportReceipt:
    """把資料庫目前的商品條件整張匯出成 Excel（#122）。"""
    with connect(settings) as conn:
        return export_products(conn, path)


# ---------- 8 進度 ----------


def load_progress_rows(
    settings: ConnectionSettings, paths: ConfigPaths, today: dt.date | None = None
) -> tuple[ProgressRow, ...]:
    """所有商品的作業進度（#156）；上手代號照上手編號對照，距今天數以今天（或 `today`）算。"""
    config = load_import_config(paths)
    with connect(settings) as conn:
        return load_progress(conn, config, today or dt.date.today())


def sent_before(settings: ConnectionSettings, tdccs: Sequence[str]) -> tuple[str, ...]:
    """登記已寄出前查（#157）：這些 TDCC 裡已經有「已寄出」步驟紀錄的，依傳入的順序。"""
    with connect(settings) as conn:
        return already_sent(conn, tdccs)


def mark_sent(settings: ConnectionSettings, tdccs: Sequence[str]) -> Registered:
    """登記已寄出（#157）：每檔寫一筆「已寄出」步驟紀錄（誰、何時），一檔一個 transaction；寫不進去只影響那一檔。"""
    with connect(settings) as conn:
        return _each(conn, tdccs, lambda tdcc: write_records(conn, [StepRecord(tdcc, SENT_STEP, "進度頁登記已寄出")]))


def find_sent_batch(settings: ConnectionSettings, paths: ConfigPaths, through: dt.date) -> SentBatch:
    """批次登記已寄出前（#186）重新讀進度：交易日在 `through` 以前（含）、目前是待寄信的商品與還沒核對完的檔數。"""
    return sent_batch(load_progress_rows(settings, paths), through)


def mark_sent_batch(settings: ConnectionSettings, batch: SentBatch) -> Registered:
    """批次登記已寄出（#186）：`batch` 的每檔寫一筆「已寄出」，一檔一個 transaction（#205）；寫的時候已經不是待寄信
    （已經有「已寄出」、確認期間被更正解鎖）的跳過，不重複寫、其他階段的不動。中途寫不進去的只影響那一檔，之前寫好的保留。"""
    result = f"進度頁批次登記已寄出（交易日 {batch.through} 以前）"
    with connect(settings) as conn:
        fresh = still_pending_mail(conn, batch.tdccs)  # 自己一個 transaction，讀完就結束
        return _each(conn, fresh, lambda tdcc: write_records(conn, [StepRecord(tdcc, SENT_STEP, result)]))


def register_redemptions(settings: ConnectionSettings, tdccs: Sequence[str], day: dt.date, note: str) -> Registered:
    """登記顧客贖回（#157）：每檔寫一筆來源「人工」的事件，一檔一個 transaction；已有存續事件的那一檔寫不進去並寫出原因。"""
    with connect(settings) as conn:
        return _each(conn, tdccs, lambda tdcc: record_redemption(conn, tdcc, day, note))


def event_choices(settings: ConnectionSettings, tdcc: str) -> EventChoices:
    """提出、解除異議前讀這檔的自動事件與還沒解除的異議（#157）。"""
    with connect(settings) as conn:
        events = tuple(e for e in current_events(conn, tdcc) if e.source == OBSERVATION)
        return EventChoices(tdcc, events, tuple(open_objections(conn, [tdcc]).get(tdcc, ())))


def object_to_event(settings: ConnectionSettings, event_id: int, note: str) -> int:
    """提出異議（#157）：異議不改事件；回傳異議編號。不能提出時丟 `LifecycleError`。"""
    with connect(settings) as conn:
        return raise_objection(conn, event_id, note)


def resolve_event_objection(settings: ConnectionSettings, objection_id: int) -> int:
    """解除異議（#157）：記下解除的人與時間；回傳解除的異議編號。"""
    with connect(settings) as conn:
        resolve_objection(conn, objection_id)
        return objection_id


# ---------- 9 出場清單 ----------


def load_exit_list(settings: ConnectionSettings, paths: ConfigPaths, day: dt.date | None) -> ExitList:
    """那一天（沒有時用收盤價表最新的收盤日）還有效的出場事件，來源不限（#171、#188）；上手代號照上手編號對照。"""
    config = load_import_config(paths)
    with connect(settings) as conn:
        return load_exits(conn, config, day)


# ---------- 信箱維護 ----------


def load_mailbox_rows(settings: ConnectionSettings) -> MailboxRows:
    """信箱維護（#158）：全部人員與別名。"""
    with connect(settings) as conn:
        return load_rows(conn)


def save_mailbox_person(settings: ConnectionSettings, before: Person | None, after: Person) -> MailboxRows:
    """新增（before 是 None）或修改一個人；寫不進去時丟 `MailboxError`。"""
    return _mailbox_change(settings, lambda conn: save_person(conn, before, after))


def delete_mailbox_person(settings: ConnectionSettings, before: Person) -> MailboxRows:
    """刪除一個人與他的別名。"""
    return _mailbox_change(settings, lambda conn: delete_person(conn, before))


def save_mailbox_alias(settings: ConnectionSettings, before: Alias | None, after: Alias) -> MailboxRows:
    """新增（before 是 None）或修改一個別名；寫不進去時丟 `MailboxError`。"""
    return _mailbox_change(settings, lambda conn: save_alias(conn, before, after))


def delete_mailbox_alias(settings: ConnectionSettings, before: Alias) -> MailboxRows:
    """刪除一個別名。"""
    return _mailbox_change(settings, lambda conn: delete_alias(conn, before))


def _mailbox_change(settings: ConnectionSettings, write: Callable[[psycopg.Connection], object]) -> MailboxRows:
    """寫一筆（自己一個 transaction），再重讀全部人員與別名。"""
    with connect(settings) as conn:
        write(conn)
        return load_rows(conn)


def load_rating_rows(settings: ConnectionSettings) -> RatingRows:
    """發行機構評等（#237）：全部機構與評等。"""
    with connect(settings) as conn:
        return load_ratings(conn)


def add_rated_entity(settings: ConnectionSettings, entity: Entity, first: Rating) -> RatingRows:
    """新增一個機構和它的第一筆評等；寫不進去時丟 `CreditRatingError`。"""
    return _rating_change(settings, lambda conn: add_entity(conn, entity, first))


def add_credit_rating(settings: ConnectionSettings, latest: Rating | None, after: Rating) -> RatingRows:
    """新增一筆評等（生效日要比 `latest` 晚）。"""
    return _rating_change(settings, lambda conn: add_rating(conn, latest, after))


def edit_credit_rating(settings: ConnectionSettings, before: Rating, after: Rating) -> RatingRows:
    """修改最新一筆評等。"""
    return _rating_change(settings, lambda conn: edit_latest(conn, before, after))


def delete_credit_rating(settings: ConnectionSettings, before: Rating) -> RatingRows:
    """刪除最新一筆評等（至少留一筆）。"""
    return _rating_change(settings, lambda conn: delete_latest(conn, before))


def _rating_change(settings: ConnectionSettings, write: Callable[[psycopg.Connection], object]) -> RatingRows:
    """寫一筆（自己一個 transaction），再重讀全部機構與評等。"""
    with connect(settings) as conn:
        write(conn)
        return load_ratings(conn)


def load_split_rows(settings: ConnectionSettings) -> tuple[Split, ...]:
    """拆股登記（#270）：全部登記。"""
    with connect(settings) as conn:
        return load_splits(conn)


def add_stock_split(settings: ConnectionSettings, split: Split, paths: ConfigPaths) -> SplitResult:
    """新增一筆拆股登記，再比價全部；寫不進去時丟 `StockSplitError`。"""
    return _split_change(settings, split, paths, add_split)


def delete_stock_split(settings: ConnectionSettings, split: Split, paths: ConfigPaths) -> SplitResult:
    """刪除一筆拆股登記，再比價全部。"""
    return _split_change(settings, split, paths, delete_split)


def _split_change(
    settings: ConnectionSettings,
    split: Split,
    paths: ConfigPaths,
    write: Callable[[psycopg.Connection, Split], object],
) -> SplitResult:
    """寫一筆（自己一個 transaction），比價全部（受影響、由比價判定出場的重新比），再重讀全部登記。"""
    try:
        calendar = load_calendar(paths.exchange_holidays)
    except CheckTermsError:
        calendar = None  # 登記照存；比價寫「讀不到交易所假日表」
    with connect(settings) as conn:
        write(conn, split)
        observation = _observe_after_write(conn, calendar, affected_exits(conn, split))
        adjusted = 0 if observation.receipt is None else adjusted_count(conn, split)  # 比價沒跑時基準日沒重算
        return SplitResult(load_splits(conn), observation, adjusted)


def _each(conn: psycopg.Connection, tdccs: Sequence[str], write: Callable[[str], object]) -> Registered:
    """一檔一個 transaction（每檔明寫 `conn.transaction()`）：寫不進去的只影響那一檔、寫好的照樣保留，原因放在結果裡。

    呼叫前不能有沒結束的 transaction，否則每檔只是其中的 savepoint、要等到連線關掉才一起 commit；有的話丟 `RuntimeError`。
    """
    import psycopg
    from psycopg.pq import TransactionStatus

    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError("一檔一個 transaction：呼叫前不能有沒結束的 transaction")
    done: list[str] = []
    failed: list[tuple[str, str]] = []
    for tdcc in tdccs:
        try:
            with conn.transaction():
                write(tdcc)
        except LifecycleError as exc:
            failed.append((tdcc, _first_line(exc)))
        except psycopg.Error as exc:
            failed.append((tdcc, f"資料庫錯誤：{_first_line(exc)}"))
        else:
            done.append(tdcc)
    return Registered(tuple(done), tuple(failed))


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0] if str(exc) else type(exc).__name__
