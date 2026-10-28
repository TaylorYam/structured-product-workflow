"""商品條件（#124、#203）：起單資料匯入、已鎖住更正、人工維護的「草稿 → 檢查 → 寫入」都在這裡。

除了這裡，`workflow.product` 沒有別的地方寫入；核對寫回（`check_source.write_back`，ADR 0013）也呼叫這裡的
`backfill`／`lock_checked`。對外：
- `read_many`：資料庫裡這幾檔（或全部）商品的條件、核對條件、標的價格、比價日、分公司配置與鎖住狀態（`Stored`），
  「一檔商品的條件怎麼讀」只有這裡知道（#204），caller 自己挑要的欄；`read` 是只讀一檔的寫法。`lock=True` 時
  鎖住這幾檔的商品條件列，寫入、核對寫回與重算核對條件共用同一把鎖。
  `locked`：已鎖住了沒（`locked_at` 有值：TS 核對通過，或 migration 0006 之前就在資料庫）。
- `draft_from`：起單建檔讀到的一列（#86、#97、ADR 0009、0010），或人工維護打開的 TDCC（#115、ADR 0017）→
  可以編輯的 `Draft`。`Draft.mode`（`Mode`）由草稿的確認狀態（`Draft.review`，`status.OrderReview`）推出，決定
  檢查與寫入走哪一套規則：起單資料匯入、已鎖住更正（#55、ADR 0014）、人工維護未鎖住、人工維護新建。
- `problems`：畫面標紅的問題 {欄位: 問題}；擋寫入的是同一套（人工維護空著的必填欄只標紅、不擋）。
- `write_batch`：起單資料整批匯入（`BatchReceipt`），整次一個 transaction，每次與每檔都在 `workflow.order_import`
  留紀錄；重算核對條件、重讀資料庫的值由呼叫端另外做（ADR 0012）。
- `write`：更正與人工維護存一檔（`Receipt`），自己一個 transaction；這檔的交易明細（`trade_rows`，#116、ADR 0018）
  一起存，商品條件、交易明細各在 `workflow.term_correction`、`workflow.trade_row_correction` 留紀錄。
- `backfill`／`lock_checked`：核對寫回寫一格並記 `workflow.backfill_log`、核對通過鎖住這檔。
- `create_backfilled`：回補既有的 FCN 以外商品（#303，`product_backfill`）建立一檔鎖住的商品條件，來源「歷史回補」。
- `missing_products`：人工維護「檢查」列出的商品；`load_import_config`／`issuer_code`：設定與上手代號。
不能讀設定、不能存檔時丟 `TermsError`（訊息是給作業人員看的中文）。

寫入每一檔照同樣的順序：鎖住這檔的商品條件列（與交易明細列）、確認打開之後沒變、依 `Mode` 檢查、組資料庫欄位
（上手不改、幣別沒改面額不動）、INSERT 或 UPDATE 改了的欄、重算核對條件、留紀錄、畫面換成資料庫存的值。
已鎖住的商品不能匯入，要改走更正；migration 0006 之後、清單更新停用（#87）之前由清單更新建立的商品沒有鎖，
起單資料匯入可以接手（條件以起單資料為準，ISIN 與分公司配置保留）。承作金額以交易明細為準，匯入不寫 `order_amount`（#97）。
規則式、不猜值：單位面額依幣別預設值（審查標準 `[denomination]`），發行機構寫法依參考條件表 `[issuer_values]`。
畫面的欄 ↔ 資料庫的欄的轉換（KO／KI 代碼、UF 反轉、面額預設）在 `product_record`（#126）。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import datetime as dt
import getpass
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum
from functools import cached_property
from typing import TYPE_CHECKING, Any, Protocol

from sp_workflow import import_run
from sp_workflow.check_terms import CalcLine, UnderlyingPrice, recalculate, recalculate_corrected
from sp_workflow.database import OperatorError, require_in_transaction
from sp_workflow.order_terms import TDCC_FIELD, OrderRow, describe, unknown_issuer
from sp_workflow.product_record import (
    CHOICES,
    COLUMN_FIELDS,
    DERIVED_COLUMNS,
    FIELDS,
    KINDS,
    PRICE_COLUMNS,
    PRICING_FIELD,
    PRODUCT_COLUMNS,
    PRODUCT_RULES,
    REQUIRED,
    STRIKE_FIELD,
    SUPPORTED_PRODUCTS,
    TRADE_FIELD,
    UNDERLYINGS,
    FieldKind,
    columns_of,
    fields_of,
    missing_fields,
    product_name,
    same_value,
    to_json,
)
from sp_workflow.status import ImportResult, OrderReview, ProductSource
from sp_workflow.trade_rows import (
    CHECK_TERM_FIELDS,
    TradeEdit,
    TradeRowsDraft,
    read_rows,
    trade_problems,
    unmatched_names,
    write_trade_rows,
)

if TYPE_CHECKING:
    from pathlib import Path

    import psycopg

    from fcn_checker.check_config import ConfigPaths
    from fcn_checker.trading_calendar import TradingCalendar

_IMPORTED = frozenset((ImportResult.NEW, ImportResult.OVERWRITTEN, ImportResult.SAME))
_FIXED_IN_CORRECTION = frozenset(("Issue date",))  # TS 回填的欄，不在更正範圍

_TYPES: dict[FieldKind, tuple[type, ...]] = {
    FieldKind.TEXT: (str,),
    FieldKind.CHOICE: (str,),
    FieldKind.PERCENT: (Decimal, int),
    FieldKind.INT: (int,),
    FieldKind.DATE: (dt.date,),
}

_NON_CALL_FIELD = "Guaranteed Periods (m)"
_CONFLICT = "起單建檔裡這檔有好幾列、這欄不同，要填正確的值（正確是空白的話請改起單建檔後重新讀取）"
# 人工維護可以空白的必填欄（#115）：資料庫的商品種類不可空白（ADR 0016）
_BLANK_ALLOWED = frozenset(REQUIRED) - {"Product"}
_ALREADY_CREATED = "打開之後資料庫已經有這檔的商品條件，請重新載入"
_MISSING_NOTE = "缺，可以先存、之後再補"  # 還沒鎖住的商品空著的必填欄：畫面標紅但不擋存檔


class Mode(Enum):
    """這次寫入走哪一種規則（由 `Draft.mode` 推出）。"""

    IMPORT = "起單資料匯入"  # 還沒鎖住的商品條件以起單資料為準；已鎖住的不寫
    CORRECT = "已鎖住更正"  # 維持鎖住；上手、發行日不改
    MAINTAIN = "人工維護未鎖住"  # 必填欄可以空白；上手不改
    CREATE = "人工維護新建"  # 只有交易明細的 TDCC 新建商品條件；必填欄可以空白

    @property
    def allow_blank(self) -> bool:
        return self in (Mode.MAINTAIN, Mode.CREATE)


# 人工維護打開時這檔的狀態 → 存檔的規則；其他（起單建檔讀到的列）是起單資料匯入
_MAINTENANCE_MODES = {
    OrderReview.LOCKED: Mode.CORRECT,  # 已鎖住的存檔照更正（ADR 0014）
    OrderReview.UNLOCKED: Mode.MAINTAIN,
    OrderReview.TRADES_ONLY: Mode.CREATE,
}


class TermsError(OperatorError):
    """設定檔有問題、這檔不能更正或存檔（不在資料庫、鎖住狀態不對、檢查沒過、打開後被改過）；訊息是給作業人員看的中文。"""


# ---------- 設定 ----------


@dataclass(frozen=True)
class ImportConfig:
    denominations: Mapping[str, Decimal]  # 幣別 → 單位面額預設值
    issuer_names: Mapping[str, str]  # 上手代號 → 發行機構的寫法（同清單更新）
    issuer_prefixes: Mapping[str, str]  # TDCC 前三碼 → 上手代號（上手編號對照）
    calendar: TradingCalendar  # 交易所假日表（明日開盤價的 Strike date，#101）
    index_underlyings: frozenset[str] = frozenset()  # 風險等級的指數清單（設定檔的寫法；比對規則在 `risk_level`，#238）
    etf_underlyings: frozenset[str] = frozenset()  # 補充 ETF：白名單沒有的，和白名單的 ETF 合併（同上，#252、#273）
    fallback_etf_underlyings: frozenset[str] = frozenset()  # 還沒匯入白名單時用的 ETF 清單（#273）


def load_import_config(paths: ConfigPaths) -> ImportConfig:
    """從審查標準、參考條件表格式、上手編號對照、交易所假日表與風險等級設定檔讀面額預設值、發行機構寫法、上手編號、
    假日與指數、ETF 清單。核對條件與風險等級的重算（`check_terms.recalculate`）也用這一份。"""
    from fcn_checker.config import (
        load_exchange_holidays,
        load_issuer_prefixes,
        load_reference_format,
        load_review_standard,
    )
    from fcn_checker.ingestion import IngestionError

    try:
        review = load_review_standard(paths.review_standard)
        reference = load_reference_format(paths.reference_format)
        prefixes = load_issuer_prefixes(paths.issuer_prefixes)
        calendar = load_exchange_holidays(paths.exchange_holidays)
        indices, etfs, fallback_etfs = load_risk_underlyings(paths.risk_level)
    except IngestionError as exc:
        raise TermsError(str(exc)) from exc
    return ImportConfig(
        {currency: Decimal(amount) for currency, amount in review.denomination.items()},
        dict(reference.issuer_values),
        prefixes,
        calendar,
        indices,
        etfs,
        fallback_etfs,
    )


RISK_LISTS = ("index_underlyings", "etf_underlyings", "fallback_etf_underlyings")


def load_risk_underlyings(path: Path) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """風險等級的（指數清單, 補充 ETF, 還沒匯入白名單時用的 ETF）（`config/risk_level.toml`，#238、#252、#273）：
    彭博代碼，只去頭尾空白；比對規則在 `risk_level.underlying_key`。缺檔或格式錯誤時丟 `fcn_checker.ingestion.IngestionError`。
    """
    from fcn_checker.config import load_toml
    from fcn_checker.ingestion import IngestionError

    d = load_toml(path, "風險等級")
    found = []
    for key in RISK_LISTS:
        codes = d.get(key)
        if not isinstance(codes, list) or not all(isinstance(c, str) and c.strip() for c in codes):
            raise IngestionError("config_invalid", f"風險等級設定檔的 {key} 要是彭博代碼的清單（可以空白）")
        found.append(frozenset(c.strip() for c in codes))
    indices, etfs, fallback_etfs = found
    return indices, etfs, fallback_etfs


class IssuerLookup(Protocol):
    """`issuer_code` 用到的兩張對照；`ImportConfig` 就是一份（核對讀資料庫另組一份，`check_source`）。"""

    @property
    def issuer_prefixes(self) -> Mapping[str, str]: ...  # TDCC 前三碼 → 上手代號

    @property
    def issuer_names(self) -> Mapping[str, str]: ...  # 上手代號 → 發行機構的寫法


def issuer_code(tdcc: str, issuer: str, config: IssuerLookup) -> str:
    """上手代號：TDCC 前三碼；對照沒有時用資料庫的發行機構寫法反查（上手不改，只用來檢查與顯示）。

    風險等級（`risk_level`）與核對讀資料庫的評等欄（`check_source`）都用這一個。
    """
    code = config.issuer_prefixes.get(tdcc[:3], "")
    if code in config.issuer_names:
        return code
    return next((c for c, name in config.issuer_names.items() if name == issuer), code)


# ---------- 資料庫裡的這檔 ----------


@dataclass(frozen=True)
class CheckTermsState:
    """一檔商品上一次算的核對條件（`workflow.check_terms`）。"""

    status: str
    reason: str  # 沒有原因時空字串
    price_date: dt.date | None  # 用的股價檔（取價日）；VWAP 或還在等股價時 None
    calculated_at: dt.datetime


@dataclass(frozen=True)
class Stored:
    """資料庫裡的一檔商品（`read_many`）：商品條件的欄與畫面的欄、來源、鎖住，和它的核對條件、標的價格、比價日、
    分公司配置；caller 自己挑要的。"""

    tdcc: str
    columns: dict[str, object]  # `PRODUCT_COLUMNS` → 值
    source: str
    isin: str | None
    locked_at: dt.datetime | None
    locked_reason: str  # 已鎖住時給作業人員看的原因；空字串＝還沒鎖住（判斷用 `locked`）
    check: CheckTermsState | None  # 還沒算過核對條件時 None
    prices: tuple[UnderlyingPrice, ...]  # 各標的的四個價格，依標的序號；沒有時空的
    autocall: dict[int, dt.date | None] | None  # 第幾個比價日 → 日期；沒回填過時 None
    branches: tuple[tuple[str, Decimal], ...]  # 分公司配置（分公司, 承作金額），依序；沒有時空的

    @cached_property
    def fields(self) -> dict[str, object]:
        """畫面的欄 → 值（`product_record.fields_of`）；用到才換算（進度頁一次讀全部商品，不需要）。"""
        return fields_of(self.columns)


def read_many(conn: psycopg.Connection, tdccs: Sequence[str] | None = None, *, lock: bool = False) -> dict[str, Stored]:
    """資料庫裡這幾檔（`tdccs` 是 None 時全部）商品的條件，依 TDCC 排；資料庫沒有的檔不在結果裡（#204）。

    各表在同一個 transaction 裡讀。`lock`：依 TDCC 的順序鎖住這幾檔的商品條件列（`FOR UPDATE`）再讀，在呼叫端的
    transaction 裡執行，鎖到它結束。寫入商品條件、核對寫回與重算核對條件都先這樣讀：同時處理同一檔時，後到的
    等先到的寫完，讀到的是寫完的狀態。
    """
    if lock:
        require_in_transaction(conn)
    # 進度頁每次讀全部商品（#204 量過）：整批 fetchall、各表分開讀、關聯表不在資料庫排序（文字排序慢），在這裡排
    where, params = ("", ()) if tdccs is None else (" WHERE tdcc = ANY(%s)", (list(tdccs),))
    with conn.transaction():
        rows = conn.execute(
            f"SELECT tdcc, {', '.join(PRODUCT_COLUMNS)}, source, isin, locked_at FROM workflow.product{where}"
            " ORDER BY tdcc" + (" FOR UPDATE" if lock else ""),  # 依 TDCC 的順序鎖，同時鎖多檔時不會互等
            params,
        ).fetchall()
        checks = {
            tdcc: CheckTermsState(status, reason or "", price_date, calculated_at)
            for tdcc, status, reason, price_date, calculated_at in conn.execute(
                f"SELECT tdcc, status, reason, price_date, calculated_at FROM workflow.check_terms{where}", params
            ).fetchall()
        }
        prices: dict[str, list[UnderlyingPrice]] = {}
        for tdcc, *values in sorted(
            conn.execute(
                "SELECT tdcc, seq, code, initial_price, strike_price, ki_price, ko_price FROM workflow.underlying_price"
                + where,
                params,
            ).fetchall()
        ):
            prices.setdefault(tdcc, []).append(UnderlyingPrice(*values))
        autocall: dict[str, dict[int, dt.date | None]] = {}
        for tdcc, slot, value in conn.execute(
            f"SELECT tdcc, slot, value FROM workflow.autocall_date{where}", params
        ).fetchall():
            autocall.setdefault(tdcc, {})[slot] = value
        branches: dict[str, list[tuple[str, Decimal]]] = {}
        for tdcc, _, branch, amount in sorted(
            conn.execute(f"SELECT tdcc, seq, branch, amount FROM workflow.branch_allocation{where}", params).fetchall()
        ):
            branches.setdefault(tdcc, []).append((branch, amount))
    n = len(PRODUCT_COLUMNS)
    found = {}
    for tdcc, *row in rows:
        columns = dict(zip(PRODUCT_COLUMNS, row[:n], strict=True))
        source, isin, locked_at = row[n:]
        found[tdcc] = Stored(
            tdcc=tdcc,
            columns=columns,
            source=source,
            isin=isin,
            locked_at=locked_at,
            locked_reason=_locked_reason(source, locked_at),
            check=checks.get(tdcc),
            prices=tuple(prices.get(tdcc, ())),
            autocall=autocall.get(tdcc),
            branches=tuple(branches.get(tdcc, ())),
        )
    return found


def read(conn: psycopg.Connection, tdcc: str, *, lock: bool = False) -> Stored | None:
    """資料庫裡這檔目前的條件與鎖住狀態（`read_many` 只讀一檔）；沒有這檔時 None。`lock` 同 `read_many`。"""
    return read_many(conn, [tdcc], lock=lock).get(tdcc)


def _locked_reason(source: str, locked_at: dt.datetime | None) -> str:
    if locked_at is None:
        return ""
    if source == ProductSource.INVENTORY:
        return "以前清單更新匯入的商品，條件已鎖住，要改請到「7 人工維護」"
    if source == ProductSource.BACKFILL:
        return "歷史回補的商品，條件已鎖住，要改請到「7 人工維護」"
    return "已核對通過，條件已鎖住，要改請到「7 人工維護」"


def locked(stored: Stored | None) -> bool:
    """這檔已鎖住（`workflow.product.locked_at` 有值）；資料庫沒有這檔時 False。

    只用 SQL 篩選的地方（例：`check_terms.recalculate` 的 `locked_at IS NULL`）照同一個定義。
    """
    return stored is not None and bool(stored.locked_reason)


def _has_trade_rows(conn: psycopg.Connection, tdcc: str) -> bool:
    return conn.execute("SELECT 1 FROM workflow.trade_row WHERE tdcc = %s LIMIT 1", (tdcc,)).fetchone() is not None


# ---------- 草稿 ----------


@dataclass
class Draft:
    """一檔商品的確認資料。`values` 是畫面上目前的值（畫面直接改這份）；`original` 是讀到的值（起單建檔，或打開時資料庫的值）。"""

    row: OrderRow
    original: Mapping[str, object]
    values: dict[str, object]
    stored: Mapping[str, object] | None = None  # 打開時資料庫的值（同一套欄名）；資料庫沒有這檔時 None
    locked_reason: str = ""  # 起單資料：已鎖住的原因；空字串＝可以改
    database_checked: bool = False  # 打開時有沒有連資料庫看過（沒看過就不能覆蓋資料庫裡的這檔）
    input_errors: dict[str, str] = field(default_factory=dict)  # 畫面上寫錯、還沒解讀成值的欄 → 說明

    @property
    def tdcc(self) -> str:
        return self.row.tdcc

    @property
    def issuer(self) -> str:
        return self.row.issuer

    @property
    def notes(self) -> Mapping[str, str]:
        """讀起單建檔時有問題、要標紅的欄 → 說明；整檔的問題在 TDCC。"""
        return self.row.notes

    @property
    def review(self) -> OrderReview:
        """這檔的確認狀態：起單建檔讀到的（已讀取、待確認），或人工維護打開時的（已鎖住、未鎖住、只有交易明細）。"""
        return self.row.status

    @property
    def mode(self) -> Mode:
        """檢查與寫入走哪一種規則（依 `review`）。"""
        return _MAINTENANCE_MODES.get(self.review, Mode.IMPORT)

    @property
    def correcting(self) -> bool:
        """更正與人工維護（#55）：空白會寫進資料庫，所以清空、補上的欄也算不同。"""
        return self.mode is not Mode.IMPORT

    @property
    def fixed(self) -> frozenset[str]:
        """不能改的欄：更正時 TS 回填的欄。"""
        return _FIXED_IN_CORRECTION if self.mode is Mode.CORRECT else frozenset()

    def differences(self) -> dict[str, object]:
        """兩邊都有值但不同的欄 → 資料庫的值（畫面標黃）；更正時清空、補上的欄也算。"""
        if self.stored is None or self.locked_reason:
            return {}
        return {
            name: stored
            for name, stored in self.stored.items()
            if (self.correcting or (stored is not None and self.values.get(name) is not None))
            and not same_value(self.values.get(name), stored)
        }

    def set_value(self, name: str, value: object, *, follow: bool = True) -> list[str]:
        """畫面改了一欄，回傳跟著改的其他欄。

        改 Trade date 時，Strike date 原本等於舊的 Trade date（一般情況），或因為 Trade date 空白才空著，
        就跟著改；明日開盤價算出的、或作業人員自己填的 Strike date 不動。`follow` 是 False（畫面上 Strike date
        還有沒解讀的輸入）時也不動。
        """
        followed = []
        if name == TRADE_FIELD and follow:
            strike = self.values.get(STRIKE_FIELD)
            same = strike is not None and same_value(strike, self.values.get(TRADE_FIELD))
            if same or (strike is None and STRIKE_FIELD not in self.row.must_fill):
                self.values[STRIKE_FIELD] = value
                followed.append(STRIKE_FIELD)
        self.values[name] = value
        return followed

    def edits(self) -> list[tuple[str, object, object]]:
        """人工修改：(欄位, 起單建檔的值, 改成)。資料庫帶入的值也算（確認後的值和起單建檔不同的都列）。"""
        return _field_edits(self.original, self.values)

    def import_review(self, config: ImportConfig) -> tuple[OrderReview, str, bool]:
        """匯入前這檔的 (狀態, 原因, 是否要處理)：待確認、請確認、可匯入、已鎖住。"""
        if self.locked_reason:
            return OrderReview.LOCKED, self.locked_reason, False
        found = problems(self, config)
        if found:
            return OrderReview.BLOCKED, describe(found), True
        if self.row.notes:
            return OrderReview.TO_CHECK, describe(self.row.notes), False
        return OrderReview.READY, "", False

    def reload(self, stored: Stored | None) -> None:
        """換成重讀的資料庫值與鎖住狀態（例如匯入後）；`values` 不動。"""
        self.stored = None if stored is None else stored.fields
        self.locked_reason = stored.locked_reason if stored is not None else ""
        self.database_checked = True


def draft_from(
    row: OrderRow | str,
    stored: Stored | None,
    config: ImportConfig,
    *,
    checked: bool = True,
    trade_product: str | None = None,
) -> Draft:
    """讀到的一檔 → 可以編輯的確認資料。`stored` 是 `read` 讀到的資料庫這檔（沒有時 None）。

    `row` 是起單建檔讀到的列（找到的才有草稿，呼叫端先看 `OrderRow.found`）：資料庫已有、還沒鎖住的商品，
    起單建檔空白的欄帶入資料庫的值；好幾列不同、或起單建檔算不出來的欄（`OrderRow.must_fill`）不帶入，一定要
    作業人員填。`checked` 是 False 時表示沒連資料庫看過（`stored` 是 None），匯入時不覆蓋資料庫裡的這檔。

    `row` 是 TDCC 時是人工維護打開這檔：資料庫有商品條件的帶出目前的值（已鎖住的照更正的規則）；沒有的是
    只有交易明細的 TDCC，從空白開始（商品種類是交易明細的 Product `trade_product`，#302；上手依上手編號對照）。
    """
    if isinstance(row, str):
        return _maintenance_draft(row, stored, config, trade_product)
    original = {name: row.values.get(name) for name in FIELDS}
    fields = None if stored is None else stored.fields
    reason = "" if stored is None else stored.locked_reason
    values = dict(original)
    if fields is not None and not reason:
        for name, value in fields.items():
            if values.get(name) is None and value is not None and name not in row.must_fill:
                values[name] = value
    return Draft(row, original, values, fields, reason, database_checked=checked)


def _maintenance_draft(tdcc: str, stored: Stored | None, config: ImportConfig, trade_product: str | None) -> Draft:
    if stored is not None:
        fields = stored.fields
        review = OrderReview.LOCKED if locked(stored) else OrderReview.UNLOCKED
        row = OrderRow(tdcc, review, issuer_code(tdcc, stored.columns["issuer"], config), dict(fields))
        return Draft(row, dict(fields), dict(fields), dict(fields), database_checked=True)
    values: dict[str, object] = {**dict.fromkeys(FIELDS), "Product": trade_product}
    row = OrderRow(tdcc, OrderReview.TRADES_ONLY, config.issuer_prefixes.get(tdcc[:3], ""), dict(values))
    return Draft(row, dict(values), dict(values), dict(values), database_checked=True)


# ---------- 檢查 ----------


def problems(draft: Draft, config: ImportConfig) -> dict[str, str]:
    """畫面標紅的問題 {欄位: 問題}（整檔的問題放在 TDCC）；畫面上寫錯的欄也算。

    起單資料：空的就可以匯入；已鎖住的只有鎖住的原因。更正：同匯入的規則，空白也擋。人工維護未鎖住、新建：
    擋存檔的問題，加上空著的必填欄（只標紅、不擋存檔）。資料庫已有的商品上手不改，上手的問題不擋。
    """
    mode = draft.mode
    if mode is Mode.IMPORT and draft.locked_reason:
        return {TDCC_FIELD: draft.locked_reason}
    found = _blocking(draft, config, mode)
    if not mode.allow_blank:
        return found
    blank = [name for name in REQUIRED if draft.values.get(name) is None and name not in found]
    return {**found, **dict.fromkeys(blank, _MISSING_NOTE)}


def _blocking(
    draft: Draft, config: ImportConfig, mode: Mode, values: Mapping[str, object] | None = None
) -> dict[str, str]:
    """擋寫入的問題 {欄位: 問題}；`values` 預設是畫面上的值。

    人工維護的必填欄可以空白；資料庫已有的商品上手不改，上手的問題不擋。
    """
    found = _value_problems(draft.values if values is None else values, draft, config, allow_blank=mode.allow_blank)
    found.update(draft.input_errors)
    if mode in (Mode.CORRECT, Mode.MAINTAIN):
        found.pop(TDCC_FIELD, None)  # 只有上手的問題放在 TDCC
    return found


def _value_problems(
    values: Mapping[str, object], draft: Draft, config: ImportConfig, *, allow_blank: bool = False
) -> dict[str, str]:
    """`allow_blank`（人工維護，#115）：必填欄（商品種類除外）與有 KI 時的 KI(%) 可以空白，只檢查有填的欄。"""
    problems: dict[str, str] = {}
    if not draft.issuer:
        problems[TDCC_FIELD] = unknown_issuer(draft.tdcc)
    elif draft.issuer not in config.issuer_names:
        problems[TDCC_FIELD] = f"上手 {draft.issuer} 在參考條件表設定沒有發行機構寫法"
    for name in FIELDS:
        value = values.get(name)
        if value is None:
            if name in draft.row.conflicts:
                problems[name] = _CONFLICT
            elif name in draft.row.unresolved:
                problems[name] = "起單建檔算不出來，要填"
            elif name in REQUIRED and not (allow_blank and name in _BLANK_ALLOWED):
                problems[name] = "必填"
            continue
        if not isinstance(value, _TYPES[KINDS[name]]) or isinstance(value, bool):
            problems[name] = f"要填{KINDS[name].value}"
        elif KINDS[name] is FieldKind.CHOICE and value not in CHOICES[name]:
            problems[name] = f"要選 {'／'.join(CHOICES[name])}"

    def filled(name: str) -> bool:
        return name not in problems and values.get(name) is not None

    product = product_name(values.get("Product")) if filled("Product") else None
    if filled("Product") and product is None:
        problems["Product"] = f"目前只做 {SUPPORTED_PRODUCTS}"
    if filled("Currency") and values["Currency"] not in config.denominations:
        problems["Currency"] = "這個幣別沒有單位面額預設值"
    if product is not None and filled(_NON_CALL_FIELD):  # Non-Call 依商品種類（#302）；商品種類不支援時不看
        if problem := PRODUCT_RULES[product].non_call_problem(values[_NON_CALL_FIELD]):
            problems[_NON_CALL_FIELD] = problem
    if filled("Tenor (m)") and values["Tenor (m)"] < 1:
        problems["Tenor (m)"] = "至少 1"
    if filled(TRADE_FIELD) and filled(STRIKE_FIELD) and values[STRIKE_FIELD] < values[TRADE_FIELD]:
        problems[STRIKE_FIELD] = "不能早於 Trade date"
    underlyings = [values.get(name) is not None for name in UNDERLYINGS]
    if any(later and not earlier for earlier, later in zip(underlyings, underlyings[1:], strict=False)):
        gap = UNDERLYINGS[underlyings.index(False)]
        problems.setdefault(gap, "標的要從 BBG Code 1 依序填，中間不能空")
    if filled("Barrier Type") and "KI Barrier (%)" not in problems:
        ki = values.get("KI Barrier (%)")
        if values["Barrier Type"] == "NONE" and ki is not None:
            problems["KI Barrier (%)"] = "Barrier Type 是 NONE 時要空白"
        elif values["Barrier Type"] != "NONE" and ki is None:
            # 人工維護也擋：KI(%) 不在必填欄，空著存進去「檢查」找不到、寄信會寫成「-」
            hint = "；還不確定就把 Barrier Type 也先空著" if allow_blank else ""
            problems["KI Barrier (%)"] = f"有 KI 時必填{hint}"
    return problems


def _raise_problems(prefix: str, problems: Mapping[str, str], trades: TradeRowsDraft | None) -> None:
    """商品條件或交易明細有擋存檔的問題時，兩邊一起寫出來、整次不寫入。"""
    found = [describe(problems)] if problems else []
    if trades is not None:
        found += [f"交易明細第 {seq} 列 {name}：{text}" for (seq, name), text in trade_problems(trades).items()]
    if found:
        raise TermsError(f"{prefix}：{'；'.join(found)}")


# ---------- 寫入的結果 ----------


@dataclass(frozen=True)
class ImportLine:
    tdcc: str
    result: ImportResult
    detail: str = ""

    @property
    def imported(self) -> bool:
        return self.result in _IMPORTED


@dataclass(frozen=True)
class BatchReceipt:
    """起單資料整批匯入的結果：`workflow.order_import` 的那一筆與每檔的結果。"""

    import_id: int
    lines: tuple[ImportLine, ...]

    def count(self, result: ImportResult) -> int:
        return sum(line.result == result for line in self.lines)

    def result_of(self, tdcc: str) -> ImportResult | None:
        return next((line.result for line in self.lines if line.tdcc == tdcc), None)


@dataclass(frozen=True)
class _Outcome:
    """起單資料匯入一檔的結果，和 `workflow.order_import_line` 那一列要記的值。"""

    line: ImportLine
    confirmed: Mapping[str, object] | None = None  # 確認後的值（畫面的欄）
    previous: Mapping[str, object] | None = None  # 覆蓋前資料庫的欄位
    edits: Sequence[tuple[str, object, object]] = ()


@dataclass(frozen=True)
class Receipt:
    """更正或人工維護存檔的結果；`correction_id` 是 `workflow.term_correction` 的那一筆，
    `trade_correction_id` 是 `workflow.trade_row_correction` 的那一筆（#116）。"""

    tdcc: str
    correction_id: int | None  # 沒有改商品條件時 None（沒有寫入）
    changes: tuple[tuple[str, object, object], ...] = ()  # (欄位或 UL_n_價格, 更正前, 更正後)
    calc: CalcLine | None = None  # 重算後的核對條件；沒有重算（沒有核對條件、沒改到影響核對條件的欄）是 None
    locked: bool = True  # 存檔時已鎖住（更正）；False 是還沒鎖住的商品（人工維護，#115）
    created: bool = False  # 只有交易明細的 TDCC 新建了商品條件
    missing: tuple[str, ...] = ()  # 存檔後還空著的必填欄（只有還沒鎖住的商品會有）
    trade_correction_id: int | None = None  # 沒有改交易明細時 None
    trade_changes: tuple[TradeEdit, ...] = ()  # (row_seq, 欄, 存檔前, 存檔後)
    unmatched: tuple[str, ...] = ()  # 改了的 FC 名字裡對不到信箱的
    exited: str = ""  # 改了商品條件、這檔已出場時的存續事件（#169）：比價不會重算，事件不會跟著改

    @property
    def saved(self) -> bool:
        """有寫入（商品條件或交易明細）。"""
        return self.correction_id is not None or self.trade_correction_id is not None


# ---------- 起單資料匯入 ----------


def write_batch(
    conn: psycopg.Connection, drafts: Sequence[Draft], config: ImportConfig, *, user: str | None = None
) -> BatchReceipt:
    """把通過檢查的商品寫進資料庫；沒通過的不匯入並寫原因。整次在同一個 transaction 裡，出錯就全部不寫。

    匯入成功的檔，`values` 會補上匯入時保留的資料庫值（畫面跟資料庫一致）。
    `order_import_line.source_mail`（#40 時是 Order 原信檔名）寫起單建檔的檔名與列號。
    """
    with conn.transaction():
        import_id = import_run.open_run(conn, "order_import", {"imported_by": user or getpass.getuser()})
        lines = []
        seen: set[str] = set()
        for seq, draft in enumerate(drafts, 1):
            if draft.tdcc in seen:
                outcome = _Outcome(ImportLine(draft.tdcc, ImportResult.SKIPPED, "同一批出現兩次"))
            else:
                outcome = _import_one(conn, draft, config, import_id)
            seen.add(draft.tdcc)
            lines.append(outcome.line)
            conn.execute(
                "INSERT INTO workflow.order_import_line"
                " (import_id, seq, tdcc, result, detail, source_mail, confirmed, previous, edits)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)",
                (
                    import_id,
                    seq,
                    draft.tdcc,
                    outcome.line.result,
                    outcome.line.detail or None,
                    _source_of(draft.row),
                    to_json(outcome.confirmed),
                    to_json(outcome.previous),
                    to_json([list(edit) for edit in outcome.edits] or None),
                ),
            )
        receipt = BatchReceipt(import_id, tuple(lines))
        counts = {result: receipt.count(result) for result in ImportResult}
        import_run.finish_run(conn, "order_import", import_id, {"counts": counts})
    return receipt


def _source_of(row: OrderRow) -> str | None:
    if row.source is None:
        return None
    return f"{row.source.name} 第 {'、'.join(map(str, row.lines))} 列"


def _import_one(conn: psycopg.Connection, draft: Draft, config: ImportConfig, import_id: int) -> _Outcome:
    """起單資料匯入一檔：通過檢查、還沒鎖住的寫進資料庫（條件以起單資料為準，ISIN 與分公司配置保留）。

    在 `write_batch` 整批的 transaction 裡執行；`workflow.order_import_line` 由 `write_batch` 寫。匯入成功時
    `draft.values` 補上匯入時保留的資料庫值（畫面跟資料庫一致）。承作金額以交易明細為準，不寫 `order_amount`（#97）。
    """
    tdcc = draft.tdcc
    stored, reason = _guard(conn, draft, None, Mode.IMPORT)  # 匯入當下重新看一次，不用畫面打開時的狀態
    previous = None if stored is None else stored.columns
    if reason:
        result = ImportResult.LOCKED if locked(stored) else ImportResult.SKIPPED
        return _Outcome(ImportLine(tdcc, result, reason), previous=previous)
    values = _keep_database_values(draft, stored)
    edits = _field_edits(draft.original, values)
    found = _blocking(draft, config, Mode.IMPORT, values)
    if found:
        return _Outcome(ImportLine(tdcc, ImportResult.SKIPPED, describe(found)), values, previous, edits)
    product = _product_for(values, draft, stored, config, Mode.IMPORT)
    changed = _changed(draft, values, stored, product, Mode.IMPORT)
    if stored is not None and not changed and stored.source == ProductSource.ORDER:
        line = ImportLine(tdcc, ImportResult.SAME)
    elif not _write(
        conn, tdcc, product, changed, stored, {"source": ProductSource.ORDER, "order_import_id": import_id}
    ):
        line = ImportLine(tdcc, ImportResult.SKIPPED, "讀取之後資料庫已經有這檔的商品條件，請重新讀取後再確認")
        return _Outcome(line, values, previous, edits)
    elif stored is None:
        line = ImportLine(tdcc, ImportResult.NEW)
    else:
        names = list(dict.fromkeys(COLUMN_FIELDS.get(c) or DERIVED_COLUMNS[c] for c in changed))
        detail = "改了：" + "、".join(names) if names else ""
        if stored.source != ProductSource.ORDER:
            detail = "；".join(filter(None, (f"{stored.source}建立的商品改由起單資料匯入接手", detail)))
        line = ImportLine(tdcc, ImportResult.OVERWRITTEN, detail)
    draft.values.update(values)
    return _Outcome(line, values, previous, edits)


def _keep_database_values(draft: Draft, stored: Stored | None) -> dict[str, object]:
    """起單資料匯入：空白的欄不蓋掉資料庫的值，除非作業人員在畫面上看過那個值、刻意清空（打開時資料庫就有）。

    好幾列不同、或起單建檔算不出來的欄（`OrderRow.must_fill`）一定要作業人員填，不用資料庫的值補。
    """
    values = dict(draft.values)
    if stored is None:
        return values
    seen = draft.stored or {}
    for name, value in stored.fields.items():
        if (
            values.get(name) is None
            and value is not None
            and seen.get(name) is None
            and name not in draft.row.must_fill
        ):
            values[name] = value
    return values


# ---------- 更正與人工維護 ----------


def write(
    conn: psycopg.Connection,
    draft: Draft,
    config: ImportConfig,
    *,
    trades: TradeRowsDraft | None = None,
    reason: str = "",
    user: str | None = None,
) -> Receipt:
    """更正（已鎖住，`Mode.CORRECT`）或人工維護（`Mode.MAINTAIN`／`Mode.CREATE`）存一檔；整次在同一個 transaction 裡。

    更正：維持鎖住、核對結果不動，有核對條件的重算價格；照起單資料匯入的規則檢查，上手、發行日不改。人工維護：
    必填欄可以空白（格式錯的擋），寫入後重算核對條件；只有交易明細的新建商品條件（來源「人工維護」）。單位面額只有
    幣別改了時換成新幣別的預設值。`trades` 是這檔的交易明細（#116）：改了的格子一起寫入，在
    `workflow.trade_row_correction` 另外留一筆；更正時改到核對條件會用到的欄（分公司配置、交叉比對）條件沒改也重算。
    商品條件改了在 `workflow.term_correction` 留一筆。沒改任何欄時不寫入；打開之後這檔被改過、鎖住狀態不對、
    建立了商品條件或交易明細沒了，丟 `TermsError`、不寫入。寫入後 `draft`、`trades` 換成資料庫存的值（同重新打開）。
    起單資料的草稿整批匯入，用 `write_batch`。
    """
    mode = draft.mode
    if mode is Mode.IMPORT:
        raise ValueError("起單資料的草稿用 write_batch 整批匯入")
    tdcc, values = draft.tdcc, draft.values
    locked_product = mode is Mode.CORRECT  # 已鎖住的商品：存檔照更正（ADR 0014）
    with conn.transaction():
        stored, refused = _guard(conn, draft, trades, mode)
        if refused:
            raise TermsError(f"{tdcc}：{refused}")
        found = _blocking(draft, config, mode)
        if locked_product:
            assert stored is not None
            for name in _FIXED_IN_CORRECTION:
                if not same_value(values.get(name), stored.fields.get(name)):
                    found[name] = "TS 回填的欄位，不能更正"
        _raise_problems(f"{tdcc} {'沒有更正' if locked_product else '沒有存檔'}", found, trades)
        product = _product_for(values, draft, stored, config, mode)
        changed = _changed(draft, values, stored, product, mode)
        trade_changes = [] if trades is None else write_trade_rows(conn, trades)
        if not changed and not trade_changes:
            return Receipt(tdcc, None, locked=locked_product)
        source = {"source": ProductSource.MANUAL} if mode is Mode.CREATE else {}
        if changed and not _write(conn, tdcc, product, changed, stored, source):
            raise TermsError(f"{tdcc}：{_ALREADY_CREATED}")  # 別人同時新增了這檔：還不存在的列鎖不到
        exists = stored is not None or bool(changed)  # 只改了交易明細、還沒有商品條件的不重算
        calc, price_changes = None, ()
        if locked_product:
            assert stored is not None
            affected = any(name in CHECK_TERM_FIELDS for _, name, _, _ in trade_changes)
            recalc = recalculate_corrected(conn, config, tdcc, stored.columns, trades_changed=affected)
            calc, price_changes = (None, ()) if recalc is None else recalc
        elif exists:
            calc = recalculate(conn, config, tdccs=[tdcc]).lines[0]
        changes = (_changes(values, stored, product, changed) if changed else []) + list(price_changes)
        user = user or getpass.getuser()
        correction_id = None
        if changed:
            previous = None if stored is None else stored.columns
            correction_id = _record_terms(
                conn, tdcc, changes, previous, locked=locked_product, reason=reason, user=user
            )
        trade_id = None
        if trades is not None and trade_changes:
            prices = () if changed else price_changes  # 商品條件也改了時，價格記在商品條件的紀錄
            trade_id = _record_trades(
                conn, trades, trade_changes, prices, locked=locked_product, reason=reason, user=user
            )
        saved = read(conn, tdcc) if exists else None
        saved_trades = None if trades is None else read_rows(conn, tdcc)
    if saved is not None:
        # 存檔後當成重新打開：畫面與打開時的值都換成資料庫存的值（百分比存成小數 6 位），
        # 同一檔接著改、再存時才不會誤判成打開之後有別人改過
        draft.values, draft.original, draft.stored = dict(saved.fields), dict(saved.fields), dict(saved.fields)
        if not locked_product:
            draft.row = replace(draft.row, status=OrderReview.UNLOCKED)
    if trades is not None and saved_trades is not None:
        trades.reopen(saved_trades)
    return Receipt(
        tdcc,
        correction_id,
        tuple(changes),
        calc,
        locked=locked_product,
        created=stored is None and bool(changed),
        missing=() if locked_product else tuple(missing_fields(product)) if exists else REQUIRED,
        **_trade_receipt(trades, trade_changes, trade_id),
    )


def _guard(
    conn: psycopg.Connection, draft: Draft, trades: TradeRowsDraft | None, mode: Mode
) -> tuple[Stored | None, str]:
    """鎖住這檔的商品條件與交易明細列，回傳 (現況, 不能寫入的原因)；原因是空字串就可以寫。

    起單資料匯入：已鎖住的不寫；讀取時沒連資料庫的不覆蓋。更正與人工維護：鎖住狀態和打開時不同、
    打開之後商品條件或交易明細被改過、只有交易明細的已經建了商品條件或交易明細沒了，都不寫。
    """
    stored = read(conn, draft.tdcc, lock=True)
    reason = _stale(conn, draft, stored, mode)
    if not reason and trades is not None:
        opened = {row.seq: row.stored for row in trades.rows}
        if read_rows(conn, trades.tdcc, lock=True) != opened:
            reason = "資料庫裡這檔的交易明細在打開之後改過了，請重新載入"
    return stored, reason


def _stale(conn: psycopg.Connection, draft: Draft, stored: Stored | None, mode: Mode) -> str:
    if mode is Mode.IMPORT:
        if locked(stored):
            assert stored is not None
            return stored.locked_reason
        if stored is not None and not draft.database_checked:
            return "讀取時沒有連上資料庫，看不到資料庫裡這檔的值；請重新讀取後再確認"
        return ""
    if mode is Mode.CREATE:
        if stored is not None:
            return _ALREADY_CREATED
        return "" if _has_trade_rows(conn, draft.tdcc) else "交易明細已經沒有這檔，不能新增商品條件"
    if stored is None and mode is Mode.MAINTAIN:
        return "資料庫已經沒有這檔商品，請重新載入"
    if mode is Mode.CORRECT:
        if stored is None:
            return "資料庫沒有這檔商品"
        if not locked(stored):
            return "還沒鎖住（還沒核對通過），不用更正；請重新載入"
    assert stored is not None
    if mode is Mode.MAINTAIN and locked(stored):
        return "打開之後已經鎖住了（核對通過），請重新載入"
    seen = draft.stored or {}
    if any(not same_value(stored.fields[name], seen.get(name)) for name in stored.fields):
        again = "再更正" if mode is Mode.CORRECT else ""
        return f"資料庫裡這檔的條件在打開之後改過了，請重新載入{again}"
    return ""


# ---------- 組欄位、寫入 ----------


def _product_for(
    values: Mapping[str, Any], draft: Draft, stored: Stored | None, config: ImportConfig, mode: Mode
) -> dict[str, object]:
    """畫面的值 → 要寫的資料庫商品欄位。資料庫已有的商品，更正與人工維護時上手不改、幣別沒改時面額不動
    （可能是放行的 TS 值）；更正時發行日不改。起單資料匯入以起單資料為準，上手與面額都照設定換算。"""
    product = columns_of(values, config.issuer_names.get(draft.issuer), config.denominations)
    if stored is None or mode is Mode.IMPORT:
        return product
    product["issuer"] = stored.columns["issuer"]
    if same_value(values.get("Currency"), stored.fields["Currency"]):
        product["denomination"] = stored.columns["denomination"]
    if mode is Mode.CORRECT:
        product["issue_date"] = stored.columns["issue_date"]
    return product


def _changed(
    draft: Draft,
    values: Mapping[str, object],
    stored: Stored | None,
    product: Mapping[str, object],
    mode: Mode,
) -> list[str]:
    """和資料庫不同、要寫的欄（資料庫沒有這檔時和全空白比）。"""
    before = dict.fromkeys(PRODUCT_COLUMNS) if stored is None else stored.columns
    changed = [c for c in PRODUCT_COLUMNS if not same_value(before[c], product[c])]
    if mode is Mode.CREATE and not _field_edits(draft.stored or {}, values):
        return []  # 新建時商品種類、上手一定有值，要看畫面有沒有改
    return changed


def _write(
    conn: psycopg.Connection,
    tdcc: str,
    product: Mapping[str, object],
    changed: Sequence[str],
    stored: Stored | None,
    extra: Mapping[str, object],
) -> bool:
    """資料庫沒有這檔時新增（全部欄位與 `extra`），有的只更新改了的欄與 `extra`；回傳有沒有寫入。

    新增時別人同時新增了這檔（還不存在的列 `FOR UPDATE` 鎖不到）不寫入、回傳 False。
    """
    if stored is None:
        columns = ("tdcc", *PRODUCT_COLUMNS, *extra)  # 存續狀態看存續事件（ADR 0019）
        inserted = conn.execute(
            f"INSERT INTO workflow.product ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})"
            " ON CONFLICT (tdcc) DO NOTHING RETURNING tdcc",
            (tdcc, *(product[c] for c in PRODUCT_COLUMNS), *extra.values()),
        ).fetchone()
        return inserted is not None
    updates = {**{c: product[c] for c in changed}, **extra}
    if updates:
        assignments = ", ".join(f"{c} = %s" for c in updates)
        conn.execute(f"UPDATE workflow.product SET {assignments} WHERE tdcc = %s", (*updates.values(), tdcc))
    return True


# ---------- 紀錄 ----------


def _changes(
    values: Mapping[str, object],
    stored: Stored | None,
    product: Mapping[str, object],
    changed: Sequence[str],
) -> list[tuple[str, object, object]]:
    """存檔紀錄的 [(欄位, 存檔前, 存檔後)]：畫面的欄，加上換算的代碼欄、上手與單位面額
    （畫面上對應的欄沒改時另外記，例：舊資料的代碼和現在的換算不同、幣別換了面額跟著換）。"""
    before_fields = dict.fromkeys(FIELDS) if stored is None else stored.fields
    before_columns = dict.fromkeys(PRODUCT_COLUMNS) if stored is None else stored.columns
    changes = _field_edits(before_fields, values)
    edited = {name for name, _, _ in changes}
    for column in changed:
        source = DERIVED_COLUMNS.get(column)
        if source is not None and source not in edited:
            item = source if source not in FIELDS else column
            changes.append((item, before_columns.get(column), product[column]))
    return changes


def _record_terms(
    conn: psycopg.Connection,
    tdcc: str,
    changes: Sequence[tuple[str, object, object]],
    previous: Mapping[str, object] | None,
    *,
    locked: bool,
    reason: str,
    user: str,
) -> int:
    """商品條件的紀錄（`workflow.term_correction`）：改了的欄與價格、存檔前的商品欄位（新建時空白）。"""
    return conn.execute(
        "INSERT INTO workflow.term_correction (tdcc, corrected_by, reason, changes, previous, locked)"
        " VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s) RETURNING correction_id",
        (tdcc, user, reason.strip() or None, to_json([list(change) for change in changes]), to_json(previous), locked),
    ).fetchone()[0]


def _record_trades(
    conn: psycopg.Connection,
    trades: TradeRowsDraft,
    changes: Sequence[TradeEdit],
    price_changes: Sequence[tuple[str, object, object]],
    *,
    locked: bool,
    reason: str,
    user: str,
) -> int:
    """交易明細的紀錄（`workflow.trade_row_correction`，#116）：改了的格子、存檔前的列；只改交易明細時重算後
    改變的價格也記在這裡。"""
    return conn.execute(
        "INSERT INTO workflow.trade_row_correction (tdcc, corrected_by, reason, changes, previous, price_changes, locked)"
        " VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s) RETURNING correction_id",
        (
            trades.tdcc,
            user,
            reason.strip() or None,
            to_json([list(change) for change in changes]),
            to_json(trades.previous()),
            to_json([list(change) for change in price_changes] or None),
            locked,
        ),
    ).fetchone()[0]


def _trade_receipt(trades: TradeRowsDraft | None, changes: Sequence[TradeEdit], trade_id: int | None) -> dict[str, Any]:
    """收據裡交易明細的部分：紀錄、改了的格子、改了的 FC 名字裡對不到信箱的；沒改交易明細時都是空的。"""
    if trades is None or trade_id is None:
        return {}
    return {
        "trade_correction_id": trade_id,
        "trade_changes": tuple(changes),
        "unmatched": unmatched_names(trades.mailbox, changes),
    }


def _field_edits(original: Mapping[str, object], values: Mapping[str, object]) -> list[tuple[str, object, object]]:
    """不同的畫面欄：[(欄位, 原本, 現在)]，依 `FIELDS` 的順序。"""
    return [
        (name, original.get(name), values.get(name))
        for name in FIELDS
        if not same_value(original.get(name), values.get(name))
    ]


# ---------- 核對寫回（ADR 0013） ----------


def backfill(
    conn: psycopg.Connection, tdcc: str, column: str, value: object, *, item: str, result_file: str, user: str
) -> bool:
    """核對寫回一格：`column` 是資料庫的欄（`isin`、`issue_date`、`denomination`、`autocall_date_<期>`、
    `underlying_<序>_<價格>`）。值有改變時在 `workflow.backfill_log` 記原值（`item` 是參考條件表的欄名），
    回傳有沒有改；不認得的欄、沒有這個標的不寫。在呼叫端的 transaction 裡執行，這檔先用 `read(…, lock=True)` 鎖住。
    """
    require_in_transaction(conn)
    if column in ("isin", "issue_date", "denomination"):
        (old,) = conn.execute(f"SELECT {column} FROM workflow.product WHERE tdcc = %s", (tdcc,)).fetchone()
        conn.execute(f"UPDATE workflow.product SET {column} = %s WHERE tdcc = %s", (value, tdcc))
    elif column.startswith("autocall_date_"):
        slot = int(column.rsplit("_", 1)[1])
        found = conn.execute(
            "SELECT value FROM workflow.autocall_date WHERE tdcc = %s AND slot = %s", (tdcc, slot)
        ).fetchone()
        old = None if found is None else found[0]
        conn.execute(
            "INSERT INTO workflow.autocall_date (tdcc, slot, value) VALUES (%s, %s, %s)"
            " ON CONFLICT (tdcc, slot) DO UPDATE SET value = EXCLUDED.value",
            (tdcc, slot, value),
        )
        if found is None and value is None:
            return False  # 不需要填的期別：記下來就好，不算改值
    elif column.startswith("underlying_") and column.endswith("_price"):
        _, i, *rest = column.split("_")
        price = "_".join(rest)
        if price not in PRICE_COLUMNS.values():  # 欄名放進 SQL 前只接受這四個
            return False
        found = conn.execute(
            f"SELECT {price} FROM workflow.underlying_price WHERE tdcc = %s AND seq = %s", (tdcc, int(i))
        ).fetchone()
        if found is None:
            return False  # 沒有這個標的（空值寫法的格子）
        old = found[0]
        conn.execute(
            f"UPDATE workflow.underlying_price SET {price} = %s WHERE tdcc = %s AND seq = %s", (value, tdcc, int(i))
        )
    else:
        return False
    if same_value(old, value):
        return False
    conn.execute(
        "INSERT INTO workflow.backfill_log (tdcc, item, old_value, new_value, result_file, written_by)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (tdcc, item, _text(old), _text(value), result_file, user),
    )
    return True


def create_backfilled(
    conn: psycopg.Connection,
    tdcc: str,
    values: Mapping[str, object],
    config: ImportConfig,
    *,
    isin: str | None,
    denomination: Decimal | None,
) -> dict[str, str]:
    """回補既有的 FCN 以外商品（#303）：資料庫還沒有的一檔，照起單資料匯入的檢查建立商品條件並鎖住，來源「歷史回補」。

    `values` 是畫面的欄 → 值；期初價格可以空白（舊表有空白的，之後在人工維護補）。ISIN 與單位面額用表上的值
    （面額空白時照幣別預設值）。回傳擋下的問題 {欄位: 問題}，空的表示寫好了。在呼叫端的 transaction 裡執行。
    """
    require_in_transaction(conn)
    row = OrderRow(tdcc, OrderReview.READ, config.issuer_prefixes.get(tdcc[:3], ""), dict(values))
    draft = draft_from(row, None, config)
    found = _blocking(draft, config, Mode.IMPORT)
    if values.get(PRICING_FIELD) is None:
        found.pop(PRICING_FIELD, None)
    if found:
        return found
    product = _product_for(draft.values, draft, None, config, Mode.IMPORT)
    if denomination is not None:
        product["denomination"] = denomination
    if not _write(conn, tdcc, product, PRODUCT_COLUMNS, None, {"source": ProductSource.BACKFILL, "isin": isin}):
        return {TDCC_FIELD: "資料庫已經有這檔的商品條件"}
    lock_checked(conn, tdcc)
    return {}


def lock_checked(conn: psycopg.Connection, tdcc: str) -> None:
    """核對通過，鎖住這檔的商品條件（`locked_at`）；在呼叫端的 transaction 裡執行。"""
    require_in_transaction(conn)
    conn.execute("UPDATE workflow.product SET locked_at = now() WHERE tdcc = %s", (tdcc,))


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


# ---------- 人工維護「檢查」（#115） ----------


@dataclass(frozen=True)
class MissingProduct:
    """「檢查」列出的一檔：還沒鎖住而且缺必填欄的商品，或只有交易明細的 TDCC。"""

    tdcc: str
    issuer: str  # 上手代號
    status: OrderReview  # 未鎖住／只有交易明細
    values: Mapping[str, object]  # 畫面的欄 → 資料庫的值（只有交易明細時都是空白）
    missing: tuple[str, ...]  # 缺的必填欄，依 `REQUIRED` 的順序


def missing_products(conn: psycopg.Connection, config: ImportConfig) -> tuple[MissingProduct, ...]:
    """還沒鎖住、缺必填欄的商品，和只有交易明細（還沒有商品條件）的 TDCC，依 TDCC 排。

    缺資料以起單資料的必填欄（`product_record.REQUIRED`）為準，ISIN、發行日這類由 TS 回填的不算；
    只有交易明細的，必填欄全部算缺。
    """
    found = []
    with conn.transaction():
        for tdcc, *row in conn.execute(
            # 還沒鎖住（同 `locked`）
            f"SELECT tdcc, {', '.join(PRODUCT_COLUMNS)} FROM workflow.product WHERE locked_at IS NULL"
        ):
            columns = dict(zip(PRODUCT_COLUMNS, row, strict=True))
            missing = tuple(missing_fields(columns))
            if missing:
                issuer = issuer_code(tdcc, columns["issuer"], config)
                found.append(MissingProduct(tdcc, issuer, OrderReview.UNLOCKED, fields_of(columns), missing))
        for (tdcc,) in conn.execute(
            "SELECT DISTINCT tdcc FROM workflow.trade_row t"
            " WHERE NOT EXISTS (SELECT 1 FROM workflow.product p WHERE p.tdcc = t.tdcc)"
        ):
            issuer = config.issuer_prefixes.get(tdcc[:3], "")
            found.append(MissingProduct(tdcc, issuer, OrderReview.TRADES_ONLY, dict.fromkeys(FIELDS), REQUIRED))
    return tuple(sorted(found, key=lambda product: product.tdcc))
