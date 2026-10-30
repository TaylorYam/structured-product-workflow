"""標的曝險分析（#345、#352、#354、#356、#358、#360）：選定的標的大跌時，近期到期、連結它們的在庫商品目前跌破了沒有。

只呈現目前的狀態，不做估損、配息、淨損益或情境模擬等任何預估金額（#352）；Excel 只有「全部商品」一個分頁（#358）。

- 選標的照股票代號（彭博代碼的第一段，例 `GOOG`）：一個代號包含它所有交易所的代碼（`GOOG UW`、`GOOG UQ`），
  作業人員只打代號就好，分析算進全部代碼（`Choice.codes`）。
- 範圍：存續狀態在庫（沒有還有效的存續事件）、連結任一選定標的、最終比價日不晚於截止日的商品，商品種類不限
  （FCN、FCN_Express、Super Bull）。最終比價日早於分析日、還在庫的（例：等股價）是「已比價」，
  結果以最終比價日為準，不用最新收盤價判斷。
- 價格：每個標的用收盤價表最新的一筆，依拆股登記換回價格基準（同比價，`stock_splits.SplitBook.factor`）。
- 試算價（#356）：盤後大跌等不及收盤價時，作業人員可替選定標的填價格（`trial`，代碼 → 價格），
  取代最新收盤價、當作分析日的價格（拆股照分析日換算）；其他標的照舊用最新收盤價。試算價不存進資料庫，
  檔名加「_試算」。
- 分類（`assess`）：核對條件不是完成（同比價只比完成的）、缺 KI 型態、有 KI 卻沒有下限價、缺價格的無法計算；
  MKI 不比、人工確認。其他看最差標的（收盤價 ÷ 進場價最低）：收盤價 < 執行價，且沒有 KI、收盤價 < 下限價或比價已記
  AKI 觸及 → 會接股；< 執行價但還在下限價之上 → KI 未破下限；還在執行價之上、距執行價 10% 以內 → 接近執行價；
  其他是高於執行價。分類只用在視窗的匯出結果檔數。
- 全部商品（#354、#358）：一檔一列。狀態只標跌破KI（跌破下限價或比價已記 AKI 觸及）、跌破K，那一格填淺紅色，
  未跌破留空；已比價、無法計算照分類。最差表現用 100 為基數（100 + 表現 %，例 −26.1% → 73.9）。
  後面接交易明細的分公司1、FC1、面額1、分公司2…，依面額大到小；沒有交易明細的用庫存的分公司配置（FC 寫 `-`）。
  版面照作業人員的範本（#360）：表頭分基本資料、表現、交易明細三種底色，全部置中，沒有 KI 的 KI種類 留空。
- 只讀資料庫，不改任何資料。

`assess` 只判斷一檔、不碰資料庫；`load_choices`、`load_positions` 讀資料庫；`export_exposure` 寫 Excel。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from openpyxl.styles import Alignment, Color, PatternFill

from sp_workflow.check_terms import NO_BARRIER
from sp_workflow.file_tools import excel_value, save_export, table_book, text_cell
from sp_workflow.lifecycle import IN_STOCK, lifecycle_status
from sp_workflow.status import CheckTerms
from sp_workflow.stock_splits import SplitBook, load_splits

if TYPE_CHECKING:
    import psycopg

DELIVER = "會接股"
WATCH = "KI 未破下限"
NEAR = "接近執行價"
MANUAL = "MKI 人工確認"
SAFE = "高於執行價"
UNKNOWN = "無法計算"
FIXED = "已比價"  # 最終比價日早於分析日、還在庫（#352）
CATEGORIES = (DELIVER, WATCH, NEAR, MANUAL, SAFE, FIXED, UNKNOWN)
NEAR_BUFFER = Decimal("0.10")  # 接近執行價的門檻（距執行價，#347）
MKI = "MKI"
AKI_HIT = "AKI hit"  # 比價寫的 KI 欄：`AKI hit 日期 標的`（observation._ki_text）
NO_FC = "-"
MAX_LEGS = 5  # 一檔最多連結幾個標的
_UNDERLYING_COLUMNS = ", ".join(f"underlying_{n}" for n in range(1, MAX_LEGS + 1))  # workflow.product 的連結標的
KI_BROKEN = "跌破KI"  # 全部商品的狀態（#354）
K_BROKEN = "跌破K"
INTACT = ""  # 未跌破留空（#358）


@dataclass(frozen=True)
class Choice:
    """視窗上可選的一個股票代號：它在各交易所的代碼、連結任一代碼的在庫檔數，與連結最多那個代碼的最新收盤價。"""

    ticker: str
    codes: tuple[str, ...]  # 依連結檔數多到少
    products: int
    close_date: dt.date | None
    close: Decimal | None


def ticker_of(code: str) -> str:
    """股票代號：彭博代碼的第一段（`GOOG UW` → `GOOG`）。"""
    return code.split()[0] if code.split() else code


@dataclass(frozen=True)
class Leg:
    """一檔商品的一個標的：條件價格與換回價格基準的最新收盤價。"""

    seq: int
    code: str
    initial: Decimal | None
    strike: Decimal | None
    ki: Decimal | None
    close: Decimal | None  # 換回價格基準的收盤價或試算價

    @property
    def performance(self) -> Decimal | None:
        """收盤價 ÷ 進場價 − 1；缺價格時 None。"""
        if self.close is None or not self.initial:
            return None
        return self.close / self.initial - 1

    @property
    def to_strike(self) -> Decimal | None:
        """距執行價：收盤價 ÷ 執行價 − 1（負的是已低於執行價）；缺價格時 None。"""
        return None if self.close is None or not self.strike else self.close / self.strike - 1


@dataclass(frozen=True)
class Holding:
    """一筆交易明細（沒有交易明細時是一筆分公司配置）。"""

    branch: str
    fc: str
    amount: Decimal


@dataclass(frozen=True)
class Position:
    """一檔在庫商品的分析資料。"""

    tdcc: str
    product: str
    issuer: str
    currency: str
    final_date: dt.date | None
    barrier_type: str | None
    ki_text: str  # 比價狀態的 KI 欄
    terms_status: str | None  # 核對條件狀態；還沒算過是 None
    terms_reason: str | None
    legs: tuple[Leg, ...]
    holdings: tuple[Holding, ...]
    strike_pct: Decimal | None = None  # 執行價 (%)
    ki_pct: Decimal | None = None  # 下限價 (%)


@dataclass(frozen=True)
class Assessed:
    """一檔的分類與最差標的。"""

    position: Position
    category: str
    worst: Leg | None
    reason: str = ""

    @property
    def notional(self) -> Decimal:
        return sum((h.amount for h in self.position.holdings), Decimal(0))

    @property
    def status(self) -> str:
        """全部商品的狀態（#354、#358）：最差標的的價格跌破下限價（或比價已記 AKI 觸及）、跌破執行價，都沒有時空白；
        已比價與算不出的照分類。"""
        if self.category in (FIXED, UNKNOWN):
            return self.category
        w, p = self.worst, self.position
        if w is None or w.close is None or w.to_strike is None:
            return UNKNOWN
        if p.ki_text.startswith(AKI_HIT) or (p.barrier_type != NO_BARRIER and w.ki is not None and w.close < w.ki):
            return KI_BROKEN
        return K_BROKEN if w.to_strike < 0 else INTACT


def assess(position: Position, today: dt.date | None = None) -> Assessed:
    """依最差標的判斷一檔的分類（見 module 說明）；給 `today` 時，最終比價日早於它的是已比價。"""

    def unknown(reason: str) -> Assessed:
        return Assessed(position, UNKNOWN, None, reason)

    priced = all(leg.performance is not None and leg.strike is not None for leg in position.legs)
    worst = min(position.legs, key=lambda leg: (leg.performance, leg.seq)) if position.legs and priced else None
    final = position.final_date
    if today is not None and final is not None and final < today:
        return Assessed(position, FIXED, worst, f"{final} 已比價，結果以最終比價日為準，不用最新收盤價判斷")
    if position.terms_status != CheckTerms.DONE:
        status = position.terms_status or "還沒算"
        return unknown(f"核對條件{status}" + (f"：{position.terms_reason}" if position.terms_reason else ""))
    if position.barrier_type == MKI:
        return Assessed(position, MANUAL, worst, "MKI 每期觀察，系統不比，請人工確認")
    if position.barrier_type is None:
        return unknown("沒有 KI 型態")
    if worst is None:
        missing = [leg.code for leg in position.legs if leg.performance is None or leg.strike is None]
        return unknown(f"缺進場價、執行價或收盤價：{'、'.join(missing)}" if missing else "沒有標的價格")
    if position.barrier_type != NO_BARRIER and worst.ki is None:
        return unknown(f"{position.barrier_type} 沒有下限價：{worst.code}")
    to_strike = worst.to_strike
    assert to_strike is not None and worst.close is not None
    if to_strike >= 0:
        near = to_strike <= NEAR_BUFFER
        return Assessed(position, NEAR if near else SAFE, worst, "距執行價 10% 以內" if near else "")
    knocked = (
        position.barrier_type == NO_BARRIER
        or (worst.ki is not None and worst.close < worst.ki)
        or position.ki_text.startswith(AKI_HIT)
    )
    if not knocked:
        return Assessed(position, WATCH, worst, "低於執行價，但還在下限價之上")
    return Assessed(position, DELIVER, worst)


# ---------------------------------------------------------------- 讀資料庫


def load_choices(conn: psycopg.Connection) -> list[Choice]:
    """在庫商品連結的標的，照股票代號合併各交易所的代碼，依代號排序。"""
    rows = conn.execute(
        f"SELECT DISTINCT tdcc, code FROM workflow.product, unnest(ARRAY[{_UNDERLYING_COLUMNS}]) AS code"
        " WHERE code IS NOT NULL"
    ).fetchall()
    statuses = lifecycle_status(conn, [tdcc for tdcc, _ in rows])
    by_code: dict[str, set[str]] = defaultdict(set)
    for tdcc, code in rows:
        if statuses[tdcc].status == IN_STOCK:
            by_code[code].add(tdcc)
    latest = _latest_closes(conn, by_code)
    by_ticker: dict[str, list[str]] = defaultdict(list)
    for code in by_code:
        by_ticker[ticker_of(code)].append(code)
    choices = []
    for ticker in sorted(by_ticker):
        codes = tuple(sorted(by_ticker[ticker], key=lambda c: (-len(by_code[c]), c)))
        products = len(set().union(*(by_code[c] for c in codes)))
        main = next((c for c in codes if c in latest), None)
        choices.append(Choice(ticker, codes, products, *(latest[main] if main else (None, None))))
    return choices


def load_positions(
    conn: psycopg.Connection,
    codes: Iterable[str],
    until: dt.date,
    *,
    trial: Mapping[str, Decimal] | None = None,
    today: dt.date | None = None,
) -> list[Position]:
    """在庫、連結任一 `codes`、最終比價日不晚於 `until` 的商品，依最終比價日、TDCC 排序。

    `trial` 裡的代碼用試算價、當作 `today`（預設今天）的價格，其他用最新收盤價（#356）。
    """
    trial = trial or {}
    today = today or dt.date.today()
    wanted = sorted(set(codes))
    rows = conn.execute(
        "SELECT p.tdcc, p.product, p.issuer, p.currency, p.final_valuation_date, p.barrier_type, p.strike_date,"
        " s.ki, c.status, c.reason, p.strike_pct, p.ki_pct FROM workflow.product p"
        " LEFT JOIN workflow.pricing_status s ON s.tdcc = p.tdcc LEFT JOIN workflow.check_terms c ON c.tdcc = p.tdcc"
        f" WHERE p.final_valuation_date <= %s AND ARRAY[{_UNDERLYING_COLUMNS}] && %s::text[]"
        " ORDER BY p.final_valuation_date, p.tdcc",
        (until, wanted),
    ).fetchall()
    statuses = lifecycle_status(conn, [row[0] for row in rows])
    rows = [row for row in rows if statuses[row[0]].status == IN_STOCK]
    tdccs = [row[0] for row in rows]
    prices: dict[str, list[tuple]] = defaultdict(list)
    for tdcc, *values in conn.execute(
        "SELECT tdcc, seq, code, initial_price, strike_price, ki_price, price_basis_date"
        " FROM workflow.underlying_price WHERE tdcc = ANY(%s) ORDER BY tdcc, seq",
        (tdccs,),
    ):
        prices[tdcc].append(tuple(values))
    holdings = _holdings(conn, tdccs)
    latest = _latest_closes(conn, {code for values in prices.values() for _, code, *_ in values})
    book = SplitBook(load_splits(conn))
    positions = []
    for row in rows:
        tdcc, product, issuer, currency, final, barrier, strike_date, ki_text, terms_status, terms_reason = row[:10]
        legs = []
        for seq, code, initial, strike, ki, basis in prices.get(tdcc, ()):
            day, close = (today, trial[code]) if code in trial else latest.get(code, (None, None))
            base = basis or strike_date
            if close is not None and day is not None and base is not None:
                close = close * book.factor(code, base, day)
            legs.append(Leg(seq, code, initial, strike, ki, close))
        positions.append(
            Position(
                tdcc,
                product,
                issuer,
                currency,
                final,
                barrier,
                ki_text or "",
                terms_status,
                terms_reason,
                tuple(legs),
                tuple(holdings.get(tdcc, ())),
                *row[10:],
            )
        )
    return positions


def _holdings(conn: psycopg.Connection, tdccs: Sequence[str]) -> dict[str, list[Holding]]:
    """交易明細逐筆；沒有交易明細的商品用庫存的分公司配置。"""
    found: dict[str, list[Holding]] = defaultdict(list)
    for tdcc, branch, fc, amount in conn.execute(
        "SELECT tdcc, branch, fc_name, amount FROM workflow.trade_row WHERE tdcc = ANY(%s) ORDER BY tdcc, row_seq",
        (list(tdccs),),
    ):
        found[tdcc].append(Holding(branch, fc, amount))
    traded = set(found)
    for tdcc, branch, amount in conn.execute(
        "SELECT tdcc, branch, amount FROM workflow.branch_allocation WHERE tdcc = ANY(%s) ORDER BY tdcc, seq",
        (list(tdccs),),
    ):
        if tdcc not in traded:
            found[tdcc].append(Holding(branch, NO_FC, amount))
    return found


def _latest_closes(conn: psycopg.Connection, codes: Iterable[str]) -> dict[str, tuple[dt.date, Decimal]]:
    rows = conn.execute(
        "SELECT DISTINCT ON (code) code, close_date, close FROM workflow.close_price"
        " WHERE code = ANY(%s) ORDER BY code, close_date DESC",
        (sorted(set(codes)),),
    ).fetchall()
    return {code: (day, close) for code, day, close in rows}


# ---------------------------------------------------------------- 匯出

ALL = "全部商品"
# 表頭底色照作業人員的範本（#360）：主題色淡 60%，基本資料淺藍、表現淺綠、交易明細淺橙
BASIC_FILL = PatternFill("solid", fgColor=Color(theme=4, tint=0.5999938962981048))
PERFORMANCE_FILL = PatternFill("solid", fgColor=Color(theme=6, tint=0.5999938962981048))
HOLDING_FILL = PatternFill("solid", fgColor=Color(theme=9, tint=0.5999938962981048))
_COLUMNS = (  # 表頭、欄寬、表頭底色
    ("狀態", 8.7, BASIC_FILL),
    ("TDCC", 14.1, BASIC_FILL),
    ("剩餘天數", 13.3, BASIC_FILL),
    ("最終比價日", 15.9, BASIC_FILL),
    ("商品", 10.4, BASIC_FILL),
    ("最差表現標的", 18.4, PERFORMANCE_FILL),
    ("最差表現", 13.3, PERFORMANCE_FILL),
    ("K%", 7.4, PERFORMANCE_FILL),
    ("KI%", 8.3, PERFORMANCE_FILL),
    ("KI種類", 11, PERFORMANCE_FILL),
    ("面額", 11, BASIC_FILL),
    ("幣別", 8.6, BASIC_FILL),
    ("發行機構", 13.3, BASIC_FILL),
)
ALL_HEADERS = tuple(name for name, _, _ in _COLUMNS)
_HOLDING_COLUMNS = (("分公司", 12), ("FC", 7.9), ("面額", 11))  # 每組交易明細
FORMATS = {
    "最終比價日": "mm-dd-yy",
    "最差表現": "0.00",
    "K%": "0.0",
    "KI%": "0.0",
    "面額": "#,##0",
}  # 依表頭（去掉組號）
CENTER = Alignment(horizontal="center", vertical="center")
BROKEN_FILL = PatternFill("solid", fgColor="FFC7CE")  # 跌破K、跌破KI 的狀態格：淺紅色（Excel 內建的淺紅色填滿）


@dataclass(frozen=True)
class ExposureReceipt:
    path: Path
    count: int  # 列入的商品檔數
    counts: Mapping[str, int]  # 各分類檔數


def export_name(codes: Sequence[str], now: dt.datetime | None = None, *, trial: bool = False) -> str:
    """存檔視窗預設的檔名；用了試算價時加「_試算」（#356）。"""
    tickers = "_".join(dict.fromkeys(ticker_of(code) for code in codes))[:40]
    return f"標的曝險分析_{tickers}{'_試算' if trial else ''}_{now or dt.datetime.now():%Y%m%d_%H%M}.xlsx"


def export_exposure(
    conn: psycopg.Connection,
    codes: Sequence[str],
    until: dt.date,
    path: Path,
    *,
    today: dt.date | None = None,
    trial: Mapping[str, Decimal] | None = None,
) -> ExposureReceipt:
    """分析並寫成一份 Excel，只有「全部商品」一個分頁（#358）。

    `trial` 是選定代碼的試算價（#356）；不在 `codes` 裡的代碼不理會，只有選定標的能用試算價。
    """
    today = today or dt.date.today()
    trial = {code: price for code, price in (trial or {}).items() if code in codes}
    with conn.transaction():  # 每個 SELECT 看同一個時間點
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        positions = load_positions(conn, codes, until, trial=trial, today=today)
    results = [assess(p, today) for p in positions]
    counts = {category: sum(r.category == category for r in results) for category in CATEGORIES}
    slots = max((len(p.holdings) for p in positions), default=0) or 1  # 交易明細最多那檔有幾筆
    holdings = [(f"{name}{n}", width, HOLDING_FILL) for n in range(1, slots + 1) for name, width in _HOLDING_COLUMNS]
    columns = (*_COLUMNS, *holdings)
    book = table_book(
        ALL,
        [name for name, _, _ in columns],
        ([excel_value(value) for value in _row(r, today)] for r in results),
        widths=[width for _, width, _ in columns],
        auto_filter=True,
    )
    sheet = book.active
    for cell, (_, _, fill) in zip(sheet[1], columns, strict=True):
        cell.fill = fill
        cell.alignment = CENTER
    _style(sheet)
    save_export(book, path)
    return ExposureReceipt(path, len(results), counts)


def _row(r: Assessed, today: dt.date) -> tuple[object, ...]:
    """全部商品的一列：一檔一列，後面接交易明細的分公司、FC、面額，依面額大到小（#358）。"""
    p, w = r.position, r.worst
    holdings = sorted(p.holdings, key=lambda h: -h.amount)
    return (
        r.status or None,  # 未跌破留空
        p.tdcc,
        None if p.final_date is None else (p.final_date - today).days,
        p.final_date,
        p.product,
        w and w.code,
        w and _index(w.performance),
        p.strike_pct,
        p.ki_pct,
        None if p.barrier_type == NO_BARRIER else p.barrier_type,  # 沒有 KI 留空（#360）
        r.notional,
        p.currency,
        p.issuer,
        *(value for h in holdings for value in (h.branch, h.fc, h.amount)),
    )


def _index(performance: Decimal | None) -> Decimal | None:
    """表現換成 100 為基數：100 + 表現 %（−26.1% → 73.9）。"""
    return None if performance is None else 100 + performance * 100


def _style(sheet) -> None:
    """依表頭設數字與日期格式、置中；跌破K、跌破KI 只有狀態那一格填淺紅色。"""
    formats = {cell.column: FORMATS.get(str(cell.value).rstrip("0123456789")) for cell in sheet[1]}
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            text_cell(cell)
            if formats[cell.column] and cell.value is not None:
                cell.number_format = formats[cell.column]
            cell.alignment = CENTER
        if row[0].value in (KI_BROKEN, K_BROKEN):
            row[0].fill = BROKEN_FILL
