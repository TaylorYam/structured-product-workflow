"""每日匯入：把每日更新（`交易明細`＋`BBG_Data`）匯入最小 FCN 資料庫（#32、#87、ADR 0003、0008）。

對外是 `import_daily` 與回補股價用的 `import_price_csv`（#112）；兩者先用讀取端 `read_daily`／`read_price_csv`
把檔案讀成 `DailyFile`／`DailyPrices`（不碰資料庫，#484），寫入端只吃這兩個型別、不再回頭看工作表或欄名；交易明細的欄與解讀規則（`TRADE_FIELDS`、
`TRADE_REQUIRED`、`trade_value`、`is_cancel_note`）也給人工維護修改交易明細用（#116），兩邊存進去的值一樣。規則：
- 需要 `交易明細`、`BBG_Data` 兩張工作表，少一張就整份不匯入；`清單更新` 已停用，檔案裡有也不讀（#87）。
  「只匯交易明細」（`prices=False`，回補時用）不讀 `BBG_Data`、不動股價，沒有這張工作表也可以。
  商品條件改由「匯入起單資料」建立（#86），每日匯入不再寫商品條件、也不做分公司對帳。
- `交易明細` 第 1 列是欄名（第 2 列、前面有空白欄也可以），必須和交易清單月份工作表的欄名與順序完全相同；不同就整份不匯入。
  沒有 `交易明細` 時改讀唯一一張交易清單月份工作表（`YYYYMM`，欄名在 B2；多張就整份不匯入，#541），有股價的每日匯入也一樣。
  2026-11 起是新版面（#420：`FC1`、`FC2`、`新加坡`）；舊版面（`FC/PB`、`前台主管/投專`）照樣可以匯入，
  這份檔案沒有的欄存空白，「已存在／內容不同」只比這一版有的欄（已停用的前台主管/投專不比）。FC1、FC2 都比對信箱。
  只收 Product 為支援的商品種類（`fcn_checker.products.PRODUCT_RULES`：FCN、FCN_Express、Super Bull、BEN、DACN、
  FCN_Stepdown、Snowball、Snowball_Stepdown、FCN_Star、FCN_OneStar，#302、#310、#373、#367、#537、#538）的列，商品種類存設定的寫法；
  其他略過並計數，略過的 TDCC 與 Product 記在 `workflow.other_product`。
  以 TDCC 為單位、第一次寫入為準：資料庫已有的交易不重複寫入，內容不同只列出差異、不覆蓋。
- `BBG_Data`：C1 是取價日（要是日期），第 2 列 B～H 是欄名（要完全相同），第 4 列起每列一檔。
  代碼只去頭尾空白；代碼空白的列略過；價格格子空白就存空白，不是數字也存空白並列出來。
  同一個代碼出現多列：值都一樣算一筆，值不一樣記成衝突（價格留空），算價格時（#88）不能用。
  同一個取價日重新匯入時，整份取代那一天的股價；每次匯入都留紀錄（`workflow.price_import`）。
  有交易所假日表時，同一個 transaction 也把最新收盤價寫進 `workflow.close_price`（#280、ADR 0026）：
  收盤日是各代碼市場在取價日之前最近的交易日；CSV 或歷史收盤價已有的那一筆不動（見 `close_prices`）。
- 回補股價 CSV（`MarketData_YYYYMMDD.csv`，#112）：第 1 列欄名要完全相同，「取價日」是收盤那一天、整份同一天；
  存成取價日＋1 天，等同隔天早上用 BBG_Data 取到的價格。沒有 1日前收盤價；幣別、市值(百萬美元) 不存。
  代碼、重複代碼、不是數字、整天取代的處理同 BBG_Data；也留一筆 `workflow.import_run`。
  同一個 transaction 也把 CSV 那一天的收盤價寫進 `workflow.close_price`（#153、ADR 0020，見 `close_prices`）；
  讀不到交易所假日表時不能判斷休市日，收盤價這次不存（BBG_Data 也一樣）。
- 信箱由資料庫直接維護（#35、ADR 0004）：匯入不修改 `mailbox`，只用它比對 FC 名字。
- 結果另外列出交易明細的 FCN TDCC（含其他支援的商品種類；依第一次出現的順序、不重複，不是剛好 12 碼與整檔都取消的不列），
  給視窗帶到起單資料頁（#90）。
- 整次匯入在一個 transaction 內完成，任何錯誤都不留下部分寫入。錯誤清單（`每日匯入錯誤.xlsx`，每次重建全部歷史）
  在 transaction 的最後寫：寫不進去時丟 `file_tools.FileWriteError`、整次不匯入；寫好之後 commit 失敗就放回上一份（#207）。
- 格子的解讀在 `sheet_cells`，run 紀錄與錯誤清單的寫法在 `import_run`，和其他匯入共用（#207）。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import itertools
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, NoReturn

import openpyxl
from openpyxl.utils import get_column_letter

from fcn_checker.products import product_name
from sp_workflow import import_run, sheet_cells
from sp_workflow.close_prices import (
    CSV_SOURCE,
    DAILY_SOURCE,
    CloseSummary,
    closes_from_csv,
    closes_from_daily,
    write_closes,
)
from sp_workflow.database import OperatorError
from sp_workflow.file_tools import sha256_file
from sp_workflow.mailbox import Mailbox, is_pb, load_mailbox

if TYPE_CHECKING:
    import psycopg

    from fcn_checker.trading_calendar import TradingCalendar

TRADE_SHEET = "交易明細"
PRICE_SHEET = "BBG_Data"
# 沒有 `交易明細` 時改讀交易清單的月份工作表（#541）：名稱 YYYYMM，欄名在第 2 列、從 B 欄開始
_MONTH_SHEET = re.compile(r"\d{6}")
_LAST_HEADER_ROW = 2  # 欄名列最晚在第幾列

# 交易明細新版面（#420，2026-11 起）：FC/PB 改名 FC1、多了 FC2，前台主管/投專 換成 新加坡
TRADE_HEADERS = (
    "編號", "交易日期", "Product", "TDCC", "幣別", "金額", "FC1", "FC2", "分公司", "新加坡",
    "UF", "期初價格", "備註", "募集", "TS/IIS 核對", "核對TS/IIS人員", "起單人員",
)  # fmt: skip
# 舊版面（#420 之前）：照樣可以匯入，回補舊的交易清單用
LEGACY_TRADE_HEADERS = (
    "編號", "交易日期", "Product", "TDCC", "幣別", "金額", "FC/PB", "分公司", "前台主管/投專",
    "UF", "期初價格", "備註", "募集", "TS/IIS 核對", "核對TS/IIS人員", "起單人員",
)  # fmt: skip
# BBG_Data（ADR 0008）：C1 是取價日；第 2 列從 B 欄起是欄名；第 4 列起每列一檔
PRICE_DATE_ROW, PRICE_DATE_COLUMN = 1, 3
PRICE_HEADER_ROW, PRICE_FIRST_ROW = 2, 4
PRICE_FIRST_COLUMN = 2  # B 欄是代碼
PRICE_HEADERS = (
    "代碼(值)", "公司名稱", "最新收盤價(值)", "最新開盤價(值)", "1日前收盤價(值)", "200日波動率(值)",
    "市值(原幣/百萬)(值)",
)  # fmt: skip
# 回補股價 CSV（#112）：取價日是收盤那一天，存成下一天（同 BBG_Data 的取價日：內容是前一個交易日的價格）
PRICE_CSV_HEADERS = (
    "取價日", "代碼", "公司名稱", "收盤價", "開盤價", "200日波動率(%)", "市值(原幣/百萬)", "幣別", "市值(百萬美元)",
)  # fmt: skip
PRICE_CSV_DATE_SHIFT = dt.timedelta(days=1)


class Price(NamedTuple):
    """BBG_Data 一個代碼的值，依 `PRICE_HEADERS` 代碼之後的順序；欄名同 `workflow.daily_price`。"""

    company_name: str | None
    last_close: Decimal | None
    last_open: Decimal | None
    previous_close: Decimal | None
    volatility_200d: Decimal | None
    market_cap_millions: Decimal | None


# 結果的種類
NEW_TRADES = "新增交易"
UNCHANGED = "已存在"
DIFFERENT = "內容不同（未覆蓋）"
BLOCKED = "擋下"
NAME_UNMATCHED = "名字對不到信箱"
MAILBOX_EMPTY = "信箱尚未建立"
CANCELLED = "交易已取消"
PRICE_CONFLICT = "股價衝突"
PRICE_NOT_NUMBER = "股價不是數字"
IMPORT_ERROR_BOOK_NAME = "每日匯入錯誤.xlsx"
_SUCCESS_KINDS = frozenset((NEW_TRADES, UNCHANGED, CANCELLED))
# 清單更新停用前（#87）的成功結果「新增商品」「補上空白」：錯誤清單重建歷史時照樣不列
_RETIRED_SUCCESS_KINDS = frozenset(("新增商品", "補上空白"))

_FULL_TDCC = re.compile(rf"[0-9]{{{sheet_cells.TDCC_DIGITS}}}")

# 交易明細新版面的欄 → `workflow.trade_row` 的欄（Product 只收支援的商品種類、TDCC 是哪一檔，不在這裡）；
# 人工維護可以改這些欄（#116、#420）
TRADE_FIELDS = {
    "編號": "number", "交易日期": "trade_date", "幣別": "currency", "金額": "amount", "FC1": "fc_name",
    "FC2": "fc2_name", "分公司": "branch", "新加坡": "singapore", "UF": "uf", "期初價格": "initial_pricing",
    "備註": "note", "募集": "raised", "TS/IIS 核對": "ts_iis_checked", "核對TS/IIS人員": "checker",
    "起單人員": "originator",
}  # fmt: skip
# 舊版面的欄（#420）：沒有 FC2、新加坡，多了前台主管/投專
LEGACY_TRADE_FIELDS = {
    **{header: column for header, column in TRADE_FIELDS.items() if header not in ("FC1", "FC2", "新加坡")},
    "FC/PB": "fc_name", "前台主管/投專": "supervisor",
}  # fmt: skip
TRADE_REQUIRED = ("交易日期", "幣別", "金額", "FC1", "分公司")
# 不比對的欄（#420）：前台主管/投專已停用，新版匯入時空白；舊版檔回補時比它只會多出「內容不同」
_UNCOMPARED_COLUMNS = frozenset(("supervisor",))
# 報表上一列交易的 FC（#420）：FC2 是共同經手、金額不拆，有 FC2 時寫成「FC1／FC2」
FC_LABEL_SQL = "concat_ws('／', fc_name, fc2_name)"
_REQUIRED_COLUMNS = frozenset(TRADE_FIELDS[header] for header in TRADE_REQUIRED)
# 寫入資料庫的交易列用的欄：兩版的欄都寫，這份檔案沒有的欄寫空白
_TRADE_COLUMNS = ("product", *dict.fromkeys([*TRADE_FIELDS.values(), *LEGACY_TRADE_FIELDS.values()]))


class DailyImportError(OperatorError):
    """整份無法匯入（檔案或欄名有問題）；訊息是給作業人員看的中文。"""


@dataclass(frozen=True)
class ImportLine:
    tdcc: str
    kind: str
    detail: str = ""

    @property
    def problem(self) -> bool:
        return self.kind not in _SUCCESS_KINDS


@dataclass(frozen=True)
class PriceSummary:
    """這次匯入的股價（#87）。"""

    price_date: dt.date  # 取價日
    count: int  # 存了幾個代碼（含衝突的）
    replaced: int  # 取代了這個取價日原本的幾筆（第一次匯入這一天是 0）
    conflicts: tuple[str, ...]  # 值不一樣的重複代碼


@dataclass(frozen=True)
class ImportReceipt:
    run_id: int
    lines: tuple[ImportLine, ...]
    people: int
    skipped_trade_rows: int
    cancelled_trade_rows: int
    error_book_path: Path
    prices: PriceSummary | None  # 只匯交易明細時是 None
    fcn_trade_rows: int  # 交易明細裡 Product 為支援的商品種類的列數，含取消的
    fcn_tdccs: tuple[str, ...]  # 帶到起單資料頁的 TDCC（#90）
    closes: CloseSummary | None = None  # 收盤價（#280）；只匯交易明細或讀不到假日表時沒有存
    close_unknown: tuple[str, ...] = ()  # 交易所不在假日表、推不出收盤日、沒有存的代碼
    close_uncovered: tuple[str, ...] = ()  # 超出假日表涵蓋範圍、收盤價沒有存的代碼
    close_conflicts: tuple[str, ...] = ()  # 去掉類別字後才重複、值不同、收盤價沒有存的代碼

    def count(self, kind: str) -> int:
        return sum(line.kind == kind for line in self.lines)


@dataclass(frozen=True)
class PriceCsvReceipt:
    """回補股價 CSV 的結果（#112）。"""

    run_id: int
    close_date: dt.date  # CSV 的取價日（收盤那一天）；存進資料庫的取價日是 `prices.price_date`
    prices: PriceSummary
    lines: tuple[ImportLine, ...]  # 股價不是數字、股價衝突
    error_book_path: Path
    closes: CloseSummary | None = None  # 收盤價（#153）；讀不到假日表時沒有存
    closed: tuple[str, ...] = ()  # 那一天休市、收盤價沒有存的代碼
    close_uncovered: tuple[str, ...] = ()  # 那一天超出假日表涵蓋範圍、收盤價沒有存的代碼
    close_conflicts: tuple[str, ...] = ()  # 去掉類別字後才重複、值不同、收盤價沒有存的代碼


@dataclass(frozen=True)
class _SheetRow:
    number: int
    values: dict[str, Any]


@dataclass
class DailyPrices:
    """BBG_Data 或回補股價 CSV 讀出來的股價：值一樣的重複列只留一筆；值不一樣的代碼記成衝突（各組不同的值）。"""

    price_date: dt.date
    rows: dict[str, Price] = field(default_factory=dict)
    conflicts: dict[str, list[Price]] = field(default_factory=dict)
    row_counts: Counter[str] = field(default_factory=Counter)  # 每個代碼在檔案裡有幾列
    problems: list[ImportLine] = field(default_factory=list)  # 股價不是數字、股價衝突


@dataclass(frozen=True)
class TradeLayout:
    """交易明細的一種版面（#420）：表頭 → `workflow.trade_row` 的欄。"""

    headers: tuple[str, ...]
    fields: Mapping[str, str]

    @property
    def compared_columns(self) -> tuple[str, ...]:
        """「已存在／內容不同」比對的欄：這一版有的欄（舊版不比 FC2、新加坡），前台主管/投專兩版都不比。"""
        return ("product", *(column for column in self.fields.values() if column not in _UNCOMPARED_COLUMNS))


_LAYOUTS = (TradeLayout(TRADE_HEADERS, TRADE_FIELDS), TradeLayout(LEGACY_TRADE_HEADERS, LEGACY_TRADE_FIELDS))


@dataclass
class DailyFile:
    """`read_daily` 讀出的一份每日更新：交易明細依 TDCC 分好、擋下與取消的列已列出，股價在 `prices`。

    不連資料庫就能得到；「已存在／內容不同」、比對信箱與寫入都是寫入端（`import_daily`）的事。"""

    prices: DailyPrices | None  # 只匯交易明細時是 None
    layout: TradeLayout = _LAYOUTS[0]  # 交易明細是哪一版（#420）
    trades: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    blocked: list[ImportLine] = field(default_factory=list)
    cancelled_lines: list[ImportLine] = field(default_factory=list)
    other_products: dict[str, str] = field(default_factory=dict)  # 略過的不支援商品：TDCC → Product
    # 交易明細的 FCN（含 FCN_Express、Super Bull、BEN、DACN）TDCC（出現順序）→ 有沒有沒取消的列
    fcn_tdccs: dict[str, bool] = field(default_factory=dict)
    skipped_trade_rows: int = 0
    cancelled_trade_rows: int = 0
    fcn_trade_rows: int = 0


def import_daily(
    daily_book: Path, conn: psycopg.Connection, *, prices: bool = True, calendar: TradingCalendar | None = None
) -> ImportReceipt:
    """匯入一份每日更新；回傳每檔 TDCC 的結果。整份有問題時丟 `DailyImportError`、不寫入任何資料。

    `prices=False`：只匯交易明細，不讀 BBG_Data、不動股價（回補時股價改用 `import_price_csv`）。
    有 `calendar`（交易所假日表）時同時寫前一個交易日的收盤價（#280）；沒有時收盤價不存。
    """
    parsed = read_daily(daily_book, prices=prices)
    sha256 = sha256_file(daily_book)

    previous: bytes | None = None
    written = False
    try:
        with conn.transaction():
            mailbox = load_mailbox(conn)
            run_id = import_run.open_run(
                conn, "import_run", {"daily_file_name": daily_book.name, "daily_sha256": sha256}
            )
            lines = [*parsed.blocked, *parsed.cancelled_lines, *(parsed.prices.problems if parsed.prices else ())]
            lines += _write_trades(conn, parsed, run_id)
            _write_other_products(conn, parsed, run_id)
            summary = None if parsed.prices is None else _write_prices(conn, parsed.prices, run_id)
            closes = _DailyCloses()
            if parsed.prices is not None and calendar is not None:
                closes = _write_daily_closes(
                    conn, parsed.prices, calendar, file_name=daily_book.name, sha256=sha256, run_id=run_id
                )
            lines += _unmatched_names(parsed, mailbox)
            error_book_path = daily_book.parent / IMPORT_ERROR_BOOK_NAME
            receipt = ImportReceipt(
                run_id=run_id,
                lines=tuple(sorted(lines, key=lambda line: line.tdcc)),
                people=len(mailbox.people),
                skipped_trade_rows=parsed.skipped_trade_rows,
                cancelled_trade_rows=parsed.cancelled_trade_rows,
                error_book_path=error_book_path,
                prices=summary,
                fcn_trade_rows=parsed.fcn_trade_rows,
                fcn_tdccs=tuple(tdcc for tdcc, live in parsed.fcn_tdccs.items() if live),
                closes=closes.summary,
                close_unknown=closes.unknown,
                close_uncovered=closes.uncovered,
                close_conflicts=closes.conflicts,
            )
            import_run.finish_run(conn, "import_run", run_id, _summary(receipt))
            previous = _save_error_book(conn, error_book_path)
            written = True
    except BaseException:
        if written:
            _put_back(error_book_path, previous)
        raise
    return receipt


def import_price_csv(path: Path, conn: psycopg.Connection, calendar: TradingCalendar | None = None) -> PriceCsvReceipt:
    """回補股價（#112）：匯入一份 `MarketData_YYYYMMDD.csv`。整份有問題時丟 `DailyImportError`、不寫入任何資料。

    有 `calendar`（交易所假日表）時同時寫 CSV 那一天的收盤價（#153）；沒有時收盤價不存。"""
    close_date, prices = read_price_csv(path)
    sha256 = sha256_file(path)
    previous: bytes | None = None
    written = False
    try:
        with conn.transaction():
            run_id = import_run.open_run(conn, "import_run", {"daily_file_name": path.name, "daily_sha256": sha256})
            summary = _write_prices(conn, prices, run_id)
            closes, closed, uncovered, close_conflicts = None, (), (), ()
            if calendar is not None:
                found = closes_from_csv(
                    close_date,
                    {code: price.last_close for code, price in prices.rows.items()},
                    calendar,
                    prices.conflicts,
                )
                closed, uncovered, close_conflicts = found.closed, found.uncovered, found.conflicts
                details = {"closed": list(closed), "uncovered": list(uncovered), "conflicts": list(close_conflicts)}
                closes = write_closes(
                    conn,
                    found.closes,
                    source=CSV_SOURCE,
                    file_name=path.name,
                    sha256=sha256,
                    details=details,
                    run_id=run_id,
                )
            error_book_path = path.parent / IMPORT_ERROR_BOOK_NAME
            receipt = PriceCsvReceipt(
                run_id,
                close_date,
                summary,
                tuple(prices.problems),
                error_book_path,
                closes,
                closed,
                uncovered,
                close_conflicts,
            )
            record = {
                "counts": dict(Counter(line.kind for line in receipt.lines)),
                "close_date": close_date.isoformat(),
                "price_date": summary.price_date.isoformat(),
                "price_count": summary.count,
                "lines": [[line.tdcc, line.kind, line.detail] for line in receipt.lines],
            }
            import_run.finish_run(conn, "import_run", run_id, record)
            previous = _save_error_book(conn, error_book_path)
            written = True
    except BaseException:
        if written:
            _put_back(error_book_path, previous)
        raise
    return receipt


@dataclass(frozen=True)
class ImportInfo:
    """一次每日匯入的紀錄（寄信前確認資料是不是最新的）。"""

    run_id: int
    imported_at: dt.datetime
    file_name: str
    sha256: str


def latest_import(conn: psycopg.Connection) -> ImportInfo | None:
    """最近一次每日匯入；還沒匯入過時回傳 None。回補股價 CSV（summary 有 close_date）不算：寄信前要看的是交易明細。"""
    with conn.transaction():
        row = conn.execute(
            "SELECT run_id, imported_at, daily_file_name, daily_sha256 FROM workflow.import_run"
            " WHERE NOT summary ? 'close_date' ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
    return None if row is None else ImportInfo(*row)


# ---------- 每日更新 ----------


def read_daily(path: Path, *, prices: bool) -> DailyFile:
    """讀取端（#484）：讀一份每日更新，不連資料庫。`prices=False` 只讀交易明細（`DailyFile.prices` 是 None）。

    整份不能匯入時丟 `DailyImportError`：打不開、缺工作表、交易明細的欄名兩版都不同、BBG_Data 的取價日不是日期、
    欄名不對或沒有任何股價。個別列的問題（擋下、取消、股價不是數字、代碼衝突）不丟，記在回傳值裡。
    """
    wb = _open(path)
    try:
        trade_sheet = _trade_sheet(wb)
        if prices and PRICE_SHEET not in wb.sheetnames:
            raise DailyImportError(f"每日更新缺少工作表「{PRICE_SHEET}」")
        layout, trade_rows = _trade_rows(trade_sheet)
        parsed = DailyFile(_read_prices(wb[PRICE_SHEET]) if prices else None, layout)
    finally:
        wb.close()
    _parse_trades(trade_rows, parsed)
    return parsed


def _trade_sheet(wb: Any) -> Any:
    """交易明細的工作表（#541）：有 `交易明細` 就用它；沒有時用交易清單唯一一張月份工作表（`YYYYMM`）。
    月份工作表不只一張或都沒有時整份不匯入。"""
    if TRADE_SHEET in wb.sheetnames:
        return wb[TRADE_SHEET]
    months = [name for name in wb.sheetnames if _MONTH_SHEET.fullmatch(name.strip())]
    if len(months) == 1:
        return wb[months[0]]
    if months:
        raise DailyImportError(
            f"沒有工作表「{TRADE_SHEET}」，交易清單的月份工作表有 {len(months)} 張（{'、'.join(months)}），"
            "整份不匯入；請另存只有要匯入那個月的檔案"
        )
    raise DailyImportError(f"每日更新缺少工作表「{TRADE_SHEET}」（或交易清單的月份工作表，例如 202611）")


def _trade_rows(sheet: Any) -> tuple[TradeLayout, list[_SheetRow]]:
    """交易明細：欄名和新版或舊版（#420）的欄名、順序完全相同（後面多出的空白欄不算），
    回傳是哪一版與 (列號, {欄名: 值})；兩版都不同時照新版指出哪一欄不同、整份不匯入。
    欄名列是前 `_LAST_HEADER_ROW` 列裡第一個對得上某一版的列，前面的空白欄略過（交易清單月份工作表的欄名在 B2，#541）；
    都對不上時拿第一個有值的列指出哪一欄不同。"""
    rows = sheet.iter_rows(values_only=True)
    top = list(itertools.islice(rows, _LAST_HEADER_ROW))
    candidates = [_header_cells(row) for row in top] or [(0, [])]
    found = next(
        (
            (index, offset, layout)
            for index, (offset, header) in enumerate(candidates)
            for layout in _LAYOUTS
            if tuple(header) == layout.headers
        ),
        None,
    )
    if found is None:
        offset, header = next(((o, h) for o, h in candidates if h), candidates[0])
        _header_mismatch(sheet.title, header, TRADE_HEADERS, first_column=offset + 1)
    index, offset, layout = found
    width = len(layout.headers)
    result = []
    for number, row in enumerate(itertools.chain(top[index + 1 :], rows), index + 2):
        values = (tuple(row)[offset:] + (None,) * width)[:width]
        if any(sheet_cells.text(v) is not None for v in values):
            result.append(_SheetRow(number, dict(zip(layout.headers, values, strict=True))))
    return layout, result


def _header_cells(row: Sequence[Any]) -> tuple[int, list[str | None]]:
    """一列可能的欄名：(前面空白欄的數目, 去掉前後空白欄的欄名)。"""
    header = [sheet_cells.text(v) for v in row]
    offset = next((i for i, v in enumerate(header) if v is not None), 0)
    header = header[offset:]
    while header and header[-1] is None:
        header.pop()
    return offset, header


def _header_mismatch(
    title: str, header: Sequence[str | None], expected: Sequence[str], *, first_column: int
) -> NoReturn:
    """欄名和 `expected` 不同：丟 `DailyImportError`，指出第一個不同的欄（`header[0]` 在第 `first_column` 欄）。"""
    for i, want in enumerate(expected):
        got = header[i] if i < len(header) else None
        if got != want:
            where = openpyxl.utils.get_column_letter(i + first_column)
            raise DailyImportError(
                f"「{title}」的欄名和來源表不同，整份不匯入：{where} 欄應為「{want}」，實際是「{got or '空白'}」"
            )
    raise DailyImportError(f"「{title}」在「{expected[-1]}」之後還有其他欄，整份不匯入")


def _read_prices(sheet: Any) -> DailyPrices:
    """BBG_Data：取價日、欄名不對，或沒有任何股價，就整份不匯入（ADR 0008、#87）。"""
    width = PRICE_FIRST_COLUMN - 1 + len(PRICE_HEADERS)
    rows = [(tuple(row) + (None,) * width)[:width] for row in sheet.iter_rows(max_col=width, values_only=True)]
    blank = (None,) * width
    prices = DailyPrices(_price_date(rows[PRICE_DATE_ROW - 1] if len(rows) >= PRICE_DATE_ROW else blank))
    _check_price_headers(rows[PRICE_HEADER_ROW - 1] if len(rows) >= PRICE_HEADER_ROW else blank)
    for number, row in enumerate(rows[PRICE_FIRST_ROW - 1 :], PRICE_FIRST_ROW):
        _add_price(prices, number, row[PRICE_FIRST_COLUMN - 1 :], PRICE_SHEET, PRICE_HEADERS[2:])
    if not prices.rows and not prices.conflicts:
        raise DailyImportError(
            f"「{PRICE_SHEET}」沒有任何股價（第 {PRICE_FIRST_ROW} 列起都沒有代碼），整份不匯入；請確認取價是否完成"
        )
    _list_conflicts(prices)
    return prices


def _list_conflicts(prices: DailyPrices) -> None:
    prices.problems += [
        ImportLine(
            "", PRICE_CONFLICT, f"{code}：同一個代碼有 {prices.row_counts[code]} 列、值不同，價格沒有存，算價格時不能用"
        )
        for code in prices.conflicts
    ]


def read_price_csv(path: Path) -> tuple[dt.date, DailyPrices]:
    """讀取端（#112、#484）：讀一份回補股價 CSV，不連資料庫；回傳 (收盤那一天, 股價)，股價的取價日是下一天。

    整份不能匯入時丟 `DailyImportError`：打不開、不是 UTF-8、欄名不對、取價日不是日期或不是同一天、沒有任何股價。
    個別列的問題（不是數字、代碼衝突）不丟，記在 `DailyPrices.problems`。"""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DailyImportError(f"股價 CSV 不是 UTF-8 編碼，整份不匯入：{path}") from exc
    except OSError as exc:
        raise DailyImportError(f"打不開股價 CSV：{path}（{exc}）") from exc
    rows = list(csv.reader(io.StringIO(text, newline="")))  # 引號裡的換行留在同一格
    header = [sheet_cells.text(v) for v in (rows[0] if rows else [])]
    while header and header[-1] is None:
        header.pop()
    width = len(PRICE_CSV_HEADERS)
    for i, want in enumerate(PRICE_CSV_HEADERS):
        got = header[i] if i < len(header) else None
        if got != want:
            raise DailyImportError(
                f"股價 CSV 的欄名和範本不同，整份不匯入：第 {i + 1} 欄應為「{want}」，實際是「{got or '空白'}」"
            )
    if len(header) > width:
        raise DailyImportError(f"股價 CSV 在「{PRICE_CSV_HEADERS[-1]}」之後還有其他欄，整份不匯入")

    found: list[tuple[int, list[str]]] = []
    dates: dict[dt.date, int] = {}  # 取價日 → 第一次出現的列號
    for number, raw in enumerate(rows[1:], 2):
        row = (raw + [""] * width)[:width]
        if sheet_cells.text(row[1], dash_is_none=False) is None:
            continue  # 代碼空白的列略過
        try:
            close_date = sheet_cells.date(row[0])
        except ValueError:
            close_date = None
        if close_date is None:
            raise DailyImportError(f"股價 CSV 第 {number} 列的取價日「{row[0] or '空白'}」不是日期，整份不匯入")
        dates.setdefault(close_date, number)
        found.append((number, row))
    if not found:
        raise DailyImportError(f"股價 CSV 沒有任何股價（第 2 列起都沒有代碼），整份不匯入：{path}")
    if len(dates) > 1:
        shown = "、".join(f"{d:%Y-%m-%d}（第 {n} 列起）" for d, n in sorted(dates.items()))
        raise DailyImportError(f"股價 CSV 的取價日不只一天：{shown}，整份不匯入；一個 CSV 只放一天")

    close_date = next(iter(dates))
    prices = DailyPrices(close_date + PRICE_CSV_DATE_SHIFT)
    labels = (*PRICE_CSV_HEADERS[3:5], "1日前收盤價", *PRICE_CSV_HEADERS[5:7])
    for number, row in found:
        code, name, close, open_, volatility, market_cap = row[1:7]
        _add_price(prices, number, (code, name, close, open_, None, volatility, market_cap), path.name, labels)
    _list_conflicts(prices)
    return close_date, prices


def _price_date(row: Sequence[Any]) -> dt.date:
    value = row[PRICE_DATE_COLUMN - 1]
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    where = f"{get_column_letter(PRICE_DATE_COLUMN)}{PRICE_DATE_ROW}"
    shown = sheet_cells.text(value, dash_is_none=False) or "空白"
    raise DailyImportError(f"「{PRICE_SHEET}」的 {where} 應為取價日（日期），實際是「{shown}」，整份不匯入")


def _check_price_headers(row: Sequence[Any]) -> None:
    for i, want in enumerate(PRICE_HEADERS):
        got = sheet_cells.text(row[PRICE_FIRST_COLUMN - 1 + i])
        if got != want:
            where = f"{get_column_letter(PRICE_FIRST_COLUMN + i)}{PRICE_HEADER_ROW}"
            raise DailyImportError(
                f"「{PRICE_SHEET}」的欄名和範本不同，整份不匯入：{where} 應為「{want}」，實際是「{got or '空白'}」"
            )


def _add_price(prices: DailyPrices, number: int, values: Sequence[Any], source: str, labels: Sequence[str]) -> None:
    """一列股價（代碼、公司名稱，再依 `Price` 價格欄的順序）；代碼空白的列略過。
    同一個代碼再出現時，值一樣不重複算，值不一樣記成衝突。`source`、`labels` 是訊息裡的來源與價格欄名。"""
    code = sheet_cells.text(values[0], dash_is_none=False)
    if code is None:
        return
    prices.row_counts[code] += 1
    numbers = []
    for header, value in zip(labels, values[2:], strict=True):
        try:
            numbers.append(sheet_cells.decimal(value))
        except ValueError:
            prices.problems.append(
                ImportLine(
                    "", PRICE_NOT_NUMBER, f"{source}第 {number} 列 {code}：{header}「{value}」不是數字，存成空白"
                )
            )
            numbers.append(None)
    found = Price(sheet_cells.text(values[1], dash_is_none=False), *numbers)
    if code in prices.conflicts:
        if found not in prices.conflicts[code]:
            prices.conflicts[code].append(found)
    elif code not in prices.rows:
        prices.rows[code] = found
    elif prices.rows[code] != found:
        prices.conflicts[code] = [prices.rows.pop(code), found]


def _parse_trades(rows: Iterable[_SheetRow], parsed: DailyFile) -> None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    errors: dict[str, list[str]] = defaultdict(list)
    for sheet_row in rows:
        number, row = sheet_row.number, sheet_row.values
        product = product_name(sheet_cells.text(row["Product"]))
        if product is None:
            parsed.skipped_trade_rows += 1
            _remember_other(parsed, row["TDCC"], row["Product"])
            continue
        parsed.fcn_trade_rows += 1
        _remember_fcn(parsed, row)
        where = f"交易明細第 {number} 列"
        if is_cancel_note(row["備註"]):
            parsed.cancelled_trade_rows += 1
            parsed.cancelled_lines.append(
                ImportLine(
                    sheet_cells.tdcc_or_raw(row["TDCC"]),
                    CANCELLED,
                    f"{where}：備註為 Cancel，未匯入資料庫",
                )
            )
            continue
        try:
            tdcc = sheet_cells.tdcc(row["TDCC"])
        except ValueError as exc:
            parsed.blocked.append(ImportLine(sheet_cells.tdcc_or_raw(row["TDCC"]), BLOCKED, f"{where}：{exc}"))
            continue
        try:
            trade = _trade(row, product, parsed.layout)
            groups[tdcc].append(trade)
        except ValueError as exc:
            errors[tdcc].append(f"{where}：{exc}")
    for tdcc, trades in groups.items():
        if errors[tdcc]:
            parsed.blocked.append(ImportLine(tdcc, BLOCKED, "；".join(errors[tdcc]) + "（這檔的交易列都不匯入）"))
        else:
            parsed.trades[tdcc] = trades
    for tdcc in errors.keys() - groups.keys():
        parsed.blocked.append(ImportLine(tdcc, BLOCKED, "；".join(errors[tdcc])))


def _remember_fcn(parsed: DailyFile, row: Mapping[str, Any]) -> None:
    """記下這列的 FCN TDCC（#90）；同一檔只要有一列沒取消就帶。

    只收剛好 12 個半形數字（#93，同「3 準備」）：Excel 存成數字、前面的 0 掉了的不補 0，不帶。
    """
    tdcc = sheet_cells.text(row["TDCC"])
    if tdcc is None or not _FULL_TDCC.fullmatch(tdcc):
        return
    parsed.fcn_tdccs[tdcc] = parsed.fcn_tdccs.get(tdcc, False) or not is_cancel_note(row["備註"])


def _trade(row: Mapping[str, Any], product: str, layout: TradeLayout) -> dict[str, Any]:
    """一列交易 → `workflow.trade_row` 的欄（`_TRADE_COLUMNS`）；這一版沒有的欄是空白。"""
    trade: dict[str, Any] = dict.fromkeys(_TRADE_COLUMNS)
    trade["product"] = product
    for header, column in layout.fields.items():
        try:
            value = trade_value(header, row[header])
        except ValueError as exc:
            raise ValueError(f"{header}：{exc}") from exc
        if value is None and column in _REQUIRED_COLUMNS:
            raise ValueError(f"{header} 沒有值")
        trade[column] = value
    return trade


def trade_value(header: str, value: Any) -> Any:
    """交易明細的一格（Excel 的值或畫面打的字）→ 存進資料庫的值；每日匯入與人工維護（#116）共用，
    存進去的值才會和之後匯入的一樣。空白（含 `-`）回傳 None，必填由呼叫端擋；格式錯丟 ValueError（不含欄名）。"""
    return _TRADE_CONVERT.get(header, sheet_cells.text)(value)


# ---------- 寫入資料庫 ----------


class _DailyCloses(NamedTuple):
    """每日匯入寫的收盤價（#280）與沒存的代碼；沒有寫時 `summary` 是 None。"""

    summary: CloseSummary | None = None
    unknown: tuple[str, ...] = ()
    uncovered: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


def _write_daily_closes(
    conn: psycopg.Connection,
    prices: DailyPrices,
    calendar: TradingCalendar,
    *,
    file_name: str,
    sha256: str,
    run_id: int,
) -> _DailyCloses:
    """BBG_Data 的最新收盤價記到前一個交易日（#280）；CSV、歷史收盤價已有的不取代。"""
    found = closes_from_daily(
        prices.price_date, {code: price.last_close for code, price in prices.rows.items()}, calendar, prices.conflicts
    )
    details = {
        "price_date": prices.price_date.isoformat(),
        "unknown": list(found.unknown),
        "uncovered": list(found.uncovered),
        "conflicts": list(found.conflicts),
    }
    summary = write_closes(
        conn,
        found.closes,
        source=DAILY_SOURCE,
        file_name=file_name,
        sha256=sha256,
        details=details,
        run_id=run_id,
        keep_others=True,
    )
    return _DailyCloses(summary, found.unknown, found.uncovered, found.conflicts)


def _write_prices(conn: psycopg.Connection, prices: DailyPrices, run_id: int) -> PriceSummary:
    """股價依取價日存入：同一個取價日整天取代（#87）；衝突的代碼價格留空，各組不同的值記在 conflict_rows。"""
    count = len(prices.rows) + len(prices.conflicts)
    conn.execute(
        "INSERT INTO workflow.price_import (run_id, price_date, price_count, conflict_count) VALUES (%s, %s, %s, %s)",
        (run_id, prices.price_date, count, len(prices.conflicts)),
    )
    replaced = conn.execute("DELETE FROM workflow.daily_price WHERE price_date = %s", (prices.price_date,)).rowcount
    columns = ("price_date", "code", *Price._fields, "conflict_rows", "run_id")
    blank = (None,) * len(Price._fields)
    rows = [(prices.price_date, code, *price, None, run_id) for code, price in prices.rows.items()]
    rows += [
        (prices.price_date, code, *blank, json.dumps([_jsonable(p) for p in found], ensure_ascii=False), run_id)
        for code, found in prices.conflicts.items()
    ]
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO workflow.daily_price ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})", rows
        )
    return PriceSummary(prices.price_date, count, replaced, tuple(prices.conflicts))


def _jsonable(price: Price) -> dict[str, Any]:
    return {name: str(v) if isinstance(v, Decimal) else v for name, v in price._asdict().items()}


def _write_trades(conn: psycopg.Connection, parsed: DailyFile, run_id: int) -> list[ImportLine]:
    lines = []
    compared = parsed.layout.compared_columns
    for tdcc, trades in parsed.trades.items():
        stored = conn.execute(
            f"SELECT {', '.join(compared)} FROM workflow.trade_row WHERE tdcc = %s ORDER BY row_seq", (tdcc,)
        ).fetchall()
        if not stored:
            columns = ("tdcc", "row_seq", *_TRADE_COLUMNS, "first_run_id")
            with conn.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO workflow.trade_row ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
                    [(tdcc, seq, *(t[c] for c in _TRADE_COLUMNS), run_id) for seq, t in enumerate(trades, 1)],
                )
            lines.append(ImportLine(tdcc, NEW_TRADES, f"{len(trades)} 列"))
        elif len(stored) != len(trades) or any(
            (s != t[c]) for row, t in zip(stored, trades, strict=False) for s, c in zip(row, compared, strict=True)
        ):
            lines.append(
                ImportLine(tdcc, DIFFERENT, f"交易列和資料庫不同（資料庫 {len(stored)} 列、這次 {len(trades)} 列）")
            )
        else:
            lines.append(ImportLine(tdcc, UNCHANGED, "交易列"))
    return lines


def _remember_other(parsed: DailyFile, tdcc: Any, product: Any) -> None:
    try:
        parsed.other_products.setdefault(sheet_cells.tdcc(tdcc), sheet_cells.text(product) or "（空白）")
    except ValueError:
        pass  # TDCC 不完整的列只計數


def _write_other_products(conn: psycopg.Connection, parsed: DailyFile, run_id: int) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO workflow.other_product (tdcc, product, first_run_id) VALUES (%s, %s, %s)"
            " ON CONFLICT (tdcc) DO NOTHING",
            [(tdcc, product, run_id) for tdcc, product in parsed.other_products.items()],
        )


def _unmatched_names(parsed: DailyFile, mailbox: Mailbox) -> list[ImportLine]:
    if not mailbox.people:
        return [ImportLine("", MAILBOX_EMPTY, "mailbox.person 沒有任何人員，FC 名字無法比對")] if parsed.trades else []
    lines = []
    for tdcc, trades in parsed.trades.items():
        names = sorted(
            {
                name
                for t in trades
                for name in (t["fc_name"], t["fc2_name"])
                if name is not None and not is_pb(name) and mailbox.match(name) is None
            }
        )
        if names:
            lines.append(ImportLine(tdcc, NAME_UNMATCHED, "、".join(names)))
    return lines


def _summary(receipt: ImportReceipt) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for line in receipt.lines:
        counts[line.kind] += 1
    return {
        "counts": dict(counts),
        "people": receipt.people,
        "skipped_trade_rows": receipt.skipped_trade_rows,
        "price_date": None if receipt.prices is None else receipt.prices.price_date.isoformat(),
        "price_count": None if receipt.prices is None else receipt.prices.count,
        "lines": [[line.tdcc, line.kind, line.detail] for line in receipt.lines],
    }


def _error_rows(conn: psycopg.Connection) -> list[tuple[object, ...]]:
    """錯誤清單的列：重建全部匯入的歷史（成功的結果不列）。"""
    rows = []
    runs = conn.execute(
        "SELECT run_id, imported_at, daily_file_name, summary FROM workflow.import_run ORDER BY run_id"
    ).fetchall()
    for run_id, imported_at, file_name, summary in runs:
        for tdcc, kind, detail in summary.get("lines", []):
            if kind in _SUCCESS_KINDS or kind in _RETIRED_SUCCESS_KINDS:
                continue
            rows.append(
                (
                    imported_at.strftime("%Y-%m-%d %H:%M:%S%z"),
                    run_id,
                    file_name,
                    tdcc,
                    kind,
                    detail,
                )
            )

    return rows


def _save_error_book(conn: psycopg.Connection, path: Path) -> bytes | None:
    """每日匯入的錯誤清單（同一個檔、每次重建全部歷史）；寫不進去時丟 `FileWriteError`，整次不匯入。

    回傳寫之前那一份的內容（沒有或讀不到時 None），commit 失敗時交給 `_put_back` 放回。
    """
    try:
        previous = path.read_bytes()
    except OSError:
        previous = None
    import_run.error_book(
        path,
        "匯入錯誤",
        ("匯入時間", "批次", "來源檔案", "TDCC", "錯誤類型", "說明"),
        _error_rows(conn),
        widths=(22, 10, 30, 16, 24, 80),
        auto_filter=True,
    )
    return previous


def _put_back(path: Path, previous: bytes | None) -> None:
    """錯誤清單寫好之後 commit 失敗（#207）：放回上一次的清單（沒有就刪掉），和資料庫一致。"""
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(previous)


# ---------- 值的轉換 ----------


def _open(path: Path) -> Any:
    try:
        return openpyxl.load_workbook(path, read_only=True, data_only=True)
    except (OSError, ValueError, KeyError) as exc:
        raise DailyImportError(f"打不開 Excel：{path}（{exc}）") from exc


def is_cancel_note(value: Any) -> bool:
    """交易列的備註是 Cancel 或含「取消」：每日匯入不存進資料庫（人工維護也不能改成這樣，#116）。"""
    text = (sheet_cells.text(value, dash_is_none=False) or "").casefold()
    return text == "cancel" or "取消" in text


# 交易明細各欄的轉換（`trade_value`，格子的解讀在 `sheet_cells`）；沒列的是文字
_TRADE_CONVERT = {
    "編號": sheet_cells.whole_number,
    "交易日期": sheet_cells.date,
    "金額": sheet_cells.amount,
    "UF": sheet_cells.pct,
}
