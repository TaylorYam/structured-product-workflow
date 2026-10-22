"""視窗按鈕背後的動作（#125）：每個作業員動作一個 function，吃連線設定與輸入、回傳結果。

連資料庫、transaction 的邊界與容錯都在這裡；視窗（`app.py`）只收輸入、在背景呼叫一個動作、顯示結果。
匯入成功、之後的重算失敗時匯入保留（ADR 0012）：重算的錯誤不丟出，原因放在結果裡。
寄信草稿分兩個動作（讀最近一次匯入 → 建草稿），中間的確認對話框留在視窗。
準備、放回、建草稿的步驟紀錄（#151）在清單產出之後另寫；寫入失敗不丟出（清單已經產出），原因放在結果裡。
收盤價（#153）要用交易所假日表判斷休市日：回補股價讀不到假日表時照樣匯股價、收盤價不存；
歷史收盤價讀不到假日表就整份不匯入。
比價（#155）在回補股價與歷史收盤價匯入之後跑；比價失敗時匯入保留，原因放在結果裡。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fcn_checker.check_config import ConfigPaths
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
from sp_workflow.daily_import import (
    ImportInfo,
    ImportReceipt,
    PriceCsvReceipt,
    import_daily,
    import_price_csv,
    latest_import,
)
from sp_workflow.database import ConnectionSettings, DatabaseError, connect, require_current_schema
from sp_workflow.legacy_backfill import BackfillReceipt, LegacyBackfillError, backfill_legacy
from sp_workflow.lifecycle import LifecycleError
from sp_workflow.mail_drafts import DraftReceipt, create_drafts
from sp_workflow.observation import ObservationReceipt, observe_all
from sp_workflow.order_import import (
    ImportConfig,
    MissingProduct,
    OrderDraft,
    OrderImportResult,
    drafts_from,
    import_orders,
    load_import_config,
    missing_products,
    open_maintenance,
    save_maintenance,
)
from sp_workflow.order_terms import OrderRow, collect_orders
from sp_workflow.preparation import PrepareReceipt, prepare_batch
from sp_workflow.product_export import ExportReceipt, export_products
from sp_workflow.product_write import CorrectionReceipt
from sp_workflow.progress import ProgressRow, load_progress
from sp_workflow.returning import ReturnReceipt, return_to_folders
from sp_workflow.step_records import (
    Recorded,
    StepRecord,
    drafted_records,
    prepared_records,
    returned_records,
    write_records,
)
from sp_workflow.trade_rows import TradeRowsDraft, load_trade_rows

if TYPE_CHECKING:
    import psycopg


@dataclass(frozen=True)
class Recalc:
    """匯入後重算核對條件（#88）的結果；重算失敗時只有 error（匯入已經寫進資料庫）。"""

    receipt: CalcReceipt | None = None
    calendar: TradingCalendar | None = None  # 放行後重算要用
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
    drafts: list[OrderDraft]
    config: ImportConfig
    database_note: str = ""  # 沒有帶入資料庫的值時的說明
    database_error: DatabaseError | None = None


@dataclass(frozen=True)
class Checked:
    """人工維護「檢查」的結果（#115）。"""

    products: tuple[MissingProduct, ...]
    config: ImportConfig


@dataclass(frozen=True)
class Opened:
    """人工維護打開的一檔（#115）與它的交易明細（#116）。"""

    draft: OrderDraft
    config: ImportConfig
    trades: TradeRowsDraft | None = None


def _recalculate_after_import(conn: psycopg.Connection, holidays: Path) -> Recalc:
    """匯入之後重算；讀不到假日表或重算失敗時不丟錯誤（匯入已經完成），說明放在結果裡。"""
    import psycopg

    try:
        calendar = load_calendar(holidays)
        return Recalc(recalculate(conn, calendar), calendar)
    except (CheckTermsError, DatabaseError, psycopg.Error) as exc:
        return Recalc(error=_first_line(exc))


def _observe_after_import(conn: psycopg.Connection, calendar: TradingCalendar | None) -> Observation:
    """匯入收盤價之後比價全部；讀不到假日表或比價失敗時不丟錯誤（匯入已經完成），說明放在結果裡。"""
    import psycopg

    if calendar is None:
        return Observation(error="讀不到交易所假日表，這次沒有比價")
    try:
        return Observation(observe_all(conn, calendar))
    except (LifecycleError, DatabaseError, psycopg.Error) as exc:
        return Observation(error=_first_line(exc))
    except Exception as exc:  # 程式錯誤也不能讓已經寫好的匯入跟著失敗
        return Observation(error=f"比價程式出錯（{type(exc).__name__}：{_first_line(exc)}）")


# ---------- 1 每日匯入 ----------


def run_daily_import(settings: ConnectionSettings, daily: Path, paths: ConfigPaths, *, prices: bool) -> DailyResult:
    """匯入每日更新（`prices` 為 False 時只匯交易明細），再重算核對條件。"""
    with connect(settings) as conn:
        receipt = import_daily(daily, conn, prices=prices)
        return DailyResult(receipt, _recalculate_after_import(conn, paths.exchange_holidays))


def run_price_csv(settings: ConnectionSettings, source: Path, paths: ConfigPaths) -> PriceCsvResult:
    """回補股價（#112）：匯入一個 MarketData CSV（同時寫收盤價，#153），再重算核對條件、比價（#155）；交易明細不動。"""
    try:
        calendar = load_calendar(paths.exchange_holidays)
    except CheckTermsError:
        calendar = None  # 收盤價這次不存；讀不到的原因由重算寫在結果裡
    with connect(settings) as conn:
        receipt = import_price_csv(source, conn, calendar)
        recalc = _recalculate_after_import(conn, paths.exchange_holidays)
        return PriceCsvResult(receipt, recalc, _observe_after_import(conn, calendar))


def import_close_history(settings: ConnectionSettings, source: Path, paths: ConfigPaths) -> HistoryResult:
    """匯入歷史收盤價（#153，維護選單），再比價（#155）：讀不到假日表、檔案格式不對或寫不出錯誤清單就整份不匯入。"""
    calendar = load_calendar(paths.exchange_holidays)
    with connect(settings) as conn:
        require_current_schema(conn)
        receipt = import_history(source, conn, calendar)
        return HistoryResult(receipt, _observe_after_import(conn, calendar))


def import_legacy_products(settings: ConnectionSettings, source: Path, paths: ConfigPaths) -> LegacyResult:
    """匯入舊商品的價格與比價日（#154，維護選單），再比價（#155）：讀不到參考條件表格式、檔案不對就整份不匯入。"""
    from fcn_checker.config import load_reference_format
    from fcn_checker.ingestion import IngestionError

    try:
        fmt = load_reference_format(paths.reference_format)
    except IngestionError as exc:
        raise LegacyBackfillError(f"{exc}，整份不匯入") from exc
    try:
        calendar = load_calendar(paths.exchange_holidays)
    except CheckTermsError:
        calendar = None  # 照樣匯入；比價寫「讀不到交易所假日表」
    with connect(settings) as conn:
        require_current_schema(conn)
        receipt = backfill_legacy(conn, source, fmt)
        return LegacyResult(receipt, _observe_after_import(conn, calendar))


def observe_products(settings: ConnectionSettings, paths: ConfigPaths) -> ObservationReceipt:
    """重新比價全部（#155，維護選單）：讀不到假日表時丟 `CheckTermsError`。"""
    calendar = load_calendar(paths.exchange_holidays)
    with connect(settings) as conn:
        require_current_schema(conn)
        return observe_all(conn, calendar)


def release_line(
    settings: ConnectionSettings, tdcc: str, differences: Sequence[Difference], calendar: TradingCalendar
) -> CalcLine:
    """放行差異（#88）：記下放行人與時間，再重算那一檔。"""
    with connect(settings) as conn:
        return release_differences(conn, tdcc, differences, calendar)


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
    except DatabaseError as exc:
        return Recorded(error=_first_line(exc), database_error=exc)
    with conn:
        return _record_with(conn, records)


def _record_with(conn: psycopg.Connection, records: Sequence[StepRecord]) -> Recorded:
    import psycopg

    try:
        require_current_schema(conn)
        return Recorded(write_records(conn, records))
    except (DatabaseError, psycopg.Error) as exc:
        return Recorded(error=_first_line(exc))


# ---------- 2 起單資料 ----------


def read_order_sheet(settings: ConnectionSettings | None, text: str, sheet: Path, paths: ConfigPaths) -> ReadResult:
    """讀起單建檔；能連資料庫就帶入資料庫的值，沒有連線設定（`settings` 是 None）或連不上時照樣讀、寫說明。"""
    config = load_import_config(paths)
    rows = collect_orders(text, sheet, config.issuer_prefixes, config.calendar)
    if settings is None:
        return ReadResult(rows, drafts_from(rows), config, "沒有登入資料庫，沒有帶入資料庫的值")
    try:
        with connect(settings) as conn:
            return ReadResult(rows, drafts_from(rows, conn), config)
    except DatabaseError as exc:
        note = f"資料庫沒有連上，沒有帶入資料庫的值（{exc}）"
        return ReadResult(rows, drafts_from(rows), config, note, exc)


def import_order_sheet(
    settings: ConnectionSettings, drafts: Sequence[OrderDraft], config: ImportConfig
) -> OrderImportResult:
    """匯入確認過的起單資料（匯入、重算、重讀的容錯在 `import_orders`）。"""
    with connect(settings) as conn:
        return import_orders(drafts, conn, config)


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
    """讀一檔目前的商品條件與交易明細。"""
    config = load_import_config(paths)
    with connect(settings) as conn:
        return Opened(open_maintenance(conn, tdcc, config), config, load_trade_rows(conn, tdcc))


def save_product(
    settings: ConnectionSettings,
    draft: OrderDraft,
    config: ImportConfig,
    *,
    trades: TradeRowsDraft | None,
    reason: str,
) -> CorrectionReceipt:
    """人工維護存檔（已鎖住的照更正的規則），商品條件與交易明細一起。"""
    with connect(settings) as conn:
        return save_maintenance(conn, draft, config, trades=trades, reason=reason)


def export_product_terms(settings: ConnectionSettings, path: Path) -> ExportReceipt:
    """把資料庫目前的 `workflow.product` 整張匯出成 Excel（#122）。"""
    with connect(settings) as conn:
        return export_products(conn, path)


# ---------- 8 進度 ----------


def load_progress_rows(
    settings: ConnectionSettings, paths: ConfigPaths, today: dt.date | None = None
) -> tuple[ProgressRow, ...]:
    """所有商品的作業進度（#156）；上手代號照上手編號對照，距今天數以今天（或 `today`）算。"""
    config = load_import_config(paths)
    with connect(settings) as conn:
        require_current_schema(conn)
        return load_progress(conn, config, today or dt.date.today())


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0] if str(exc) else type(exc).__name__
