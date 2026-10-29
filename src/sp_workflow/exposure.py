"""標的曝險分析（#345、#347）：選定的標的大跌時，近期到期、連結它們的在庫商品有哪些會接股、面額與估損多少。

- 選標的照股票代號（彭博代碼的第一段，例 `GOOG`）：一個代號包含它所有交易所的代碼（`GOOG UW`、`GOOG UQ`），
  作業人員只打代號就好，分析與依分公司、理專的加總都算進全部代碼（`Choice.codes`）。
- 範圍：存續狀態在庫（沒有還有效的存續事件）、連結任一選定標的、最終比價日不晚於截止日的商品，商品種類不限
  （FCN、FCN_Express、Super Bull）。已過最終比價日、還在庫的（例：等股價）一併列入。
- 價格：每個標的用收盤價表最新的一筆，依拆股登記換回價格基準（同比價，`stock_splits.SplitBook.factor`）；不做假設價格。
- 核對條件不是完成（同比價只比完成的）、缺 KI 型態、有 KI 卻沒有下限價、缺價格的商品無法計算；MKI 不比、人工確認。
- 其他看最差標的（收盤價 ÷ 進場價最低）：收盤價 < 執行價，且沒有 KI、收盤價 < 下限價或比價已記 AKI 觸及 → 會接股；
  < 執行價但還在下限價之上（EKI 或還沒觸及的 AKI）→ KI 未破下限（觀察）；還在執行價之上、距執行價不到 10%
  → 接近執行價；其他是高於執行價。每檔另寫距執行價、距下限價（最差標的收盤價 ÷ 執行價或下限價 − 1）、
  到最終比價日的剩餘天數與緩衝分組（已低於執行價、<3%、3–10%、>10%）。
- 選定標的／其他標的：最差標的是選定的代碼之一時，損失算「選定標的造成」，否則算「其他標的造成」（#347），
  總覽與依分公司、依理專分開列，避免把籃子裡別的股票造成的損失算到選定標的頭上。
- 金額照交易明細逐筆（分公司、理專、金額），沒有交易明細的用庫存的分公司配置（理專寫 `-`）。
  估損 = 金額 ×（1 − 最差標的收盤價 ÷ 執行價）。到期配息估計 = 金額 × Coupon p.a. × 天期月數 ÷ 12（算到到期的全部配息，
  不看實際入帳；Super Bull 沒有配息算 0；沒有 Coupon 或天期的空白），會接股的扣配息後淨損益 = 到期配息估計 − 估損。
  USD 以收盤價表最新的匯率換算；查不到匯率的 USD 欄空白、不算進 USD 合計。
- 圖表（#347）：總覽右邊放各分類面額（選定標的／其他標的）、緩衝分組、會接股的最差標的三張圖與它們的資料表；
  依分公司、依理專右邊放會接股估損與面額組成的橫條圖（排前面的 `CHART_ROWS` 名）。圖是 Excel 原生圖表，資料改了跟著變。
- 只讀資料庫，不改任何資料。

`assess` 只判斷一檔、不碰資料庫；`load_choices`、`load_positions` 讀資料庫；`export_exposure` 寫 Excel。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from fcn_checker.products import SUPER_BULL
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
CATEGORIES = (DELIVER, WATCH, NEAR, MANUAL, SAFE, UNKNOWN)
NOTIFY = (DELIVER, WATCH, NEAR, MANUAL)  # 通知明細列的分類
NEAR_BUFFER = Decimal("0.10")  # 還在執行價之上、距執行價不到這個比例的是接近執行價（#347）
TIGHT_BUFFER = Decimal("0.03")
BELOW_STRIKE = "已低於執行價"
BUFFER_GROUPS = (BELOW_STRIKE, "<3%", "3–10%", ">10%")
MKI = "MKI"
AKI_HIT = "AKI hit"  # 比價寫的 KI 欄：`AKI hit 日期 標的`（observation._ki_text）
NO_FC = "-"
_UNDERLYING_COLUMNS = ", ".join(f"underlying_{n}" for n in range(1, 6))  # workflow.product 的連結標的


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
    close: Decimal | None
    close_date: dt.date | None

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

    @property
    def to_ki(self) -> Decimal | None:
        """距下限價：收盤價 ÷ 下限價 − 1；沒有下限價或缺價格時 None。"""
        return None if self.close is None or not self.ki else self.close / self.ki - 1


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
    usd_rate: Decimal | None  # 1 單位幣別換幾 USD
    strike_pct: Decimal | None = None  # 執行價 (%)
    ki_pct: Decimal | None = None  # 下限價 (%)
    coupon_pct: Decimal | None = None  # Coupon p.a. (%)；Super Bull 存的是出場紅利，不是配息
    tenor_months: int | None = None

    @property
    def coupon_ratio(self) -> Decimal | None:
        """到期的全部配息占面額的比例：Coupon p.a. × 天期月數 ÷ 12；Super Bull 是 0；缺資料時 None。"""
        if self.product == SUPER_BULL:
            return Decimal(0)
        if self.coupon_pct is None or self.tenor_months is None:
            return None
        return self.coupon_pct / 100 * self.tenor_months / 12


@dataclass(frozen=True)
class Assessed:
    """一檔的分類、最差標的與估損比例。"""

    position: Position
    category: str
    worst: Leg | None
    loss_ratio: Decimal  # 估損占面額的比例；不會接股時是 0
    reason: str = ""

    @property
    def notional(self) -> Decimal:
        return sum((h.amount for h in self.position.holdings), Decimal(0))

    @property
    def net_ratio(self) -> Decimal | None:
        """會接股時扣配息後的淨損益占面額的比例（到期配息 − 估損）；不會接股或缺配息資料時 None。"""
        coupon = self.position.coupon_ratio
        return None if self.category != DELIVER or coupon is None else coupon - self.loss_ratio

    @property
    def buffer_group(self) -> str | None:
        """依最差標的距執行價分組；算不出最差標的時 None。"""
        gap = self.worst.to_strike if self.worst is not None else None
        if gap is None:
            return None
        if gap < 0:
            return BELOW_STRIKE
        return BUFFER_GROUPS[1] if gap < TIGHT_BUFFER else BUFFER_GROUPS[2] if gap < NEAR_BUFFER else BUFFER_GROUPS[3]


def assess(position: Position) -> Assessed:
    """依最差標的判斷一檔的分類與估損比例（見 module 說明）。"""

    def unknown(reason: str) -> Assessed:
        return Assessed(position, UNKNOWN, None, Decimal(0), reason)

    if position.terms_status != CheckTerms.DONE:
        status = position.terms_status or "還沒算"
        return unknown(f"核對條件{status}" + (f"：{position.terms_reason}" if position.terms_reason else ""))
    priced = all(leg.performance is not None and leg.strike is not None for leg in position.legs)
    worst = min(position.legs, key=lambda leg: (leg.performance, leg.seq)) if position.legs and priced else None
    if position.barrier_type == MKI:
        return Assessed(position, MANUAL, worst, Decimal(0), "MKI 每期觀察，系統不比，請人工確認")
    if position.barrier_type is None:
        return unknown("沒有 KI 型態")
    if worst is None:
        missing = [leg.code for leg in position.legs if leg.performance is None or leg.strike is None]
        return unknown(f"缺進場價、執行價或收盤價：{'、'.join(missing)}" if missing else "沒有標的價格")
    if position.barrier_type != NO_BARRIER and worst.ki is None:
        return unknown(f"{position.barrier_type} 沒有下限價：{worst.code}")
    shortfall = _shortfall(worst)
    if shortfall <= 0:
        near = -shortfall < NEAR_BUFFER  # 收盤價 ÷ 執行價 − 1 = −shortfall
        return Assessed(position, NEAR if near else SAFE, worst, Decimal(0), "距執行價不到 10%" if near else "")
    assert worst.close is not None
    knocked = (
        position.barrier_type == NO_BARRIER
        or (worst.ki is not None and worst.close < worst.ki)
        or position.ki_text.startswith(AKI_HIT)
    )
    if not knocked:
        return Assessed(position, WATCH, worst, Decimal(0), "低於執行價，但還在下限價之上")
    return Assessed(position, DELIVER, worst, shortfall)


def _shortfall(leg: Leg) -> Decimal:
    """1 − 收盤價 ÷ 執行價：接股時估損占面額的比例；收盤價不低於執行價時 ≤ 0。"""
    assert leg.close is not None and leg.strike is not None
    return 1 - leg.close / leg.strike


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


def load_positions(conn: psycopg.Connection, codes: Iterable[str], until: dt.date) -> list[Position]:
    """在庫、連結任一 `codes`、最終比價日不晚於 `until` 的商品，依最終比價日、TDCC 排序。"""
    wanted = sorted(set(codes))
    rows = conn.execute(
        "SELECT p.tdcc, p.product, p.issuer, p.currency, p.final_valuation_date, p.barrier_type, p.strike_date,"
        " s.ki, c.status, c.reason, p.strike_pct, p.ki_pct, p.coupon_pct, p.tenor_months FROM workflow.product p"
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
    currencies = {row[3] for row in rows}
    fx = _latest_closes(conn, _fx_codes(currencies))
    rates = {ccy: _usd_rate(ccy, fx) for ccy in currencies}
    book = SplitBook(load_splits(conn))
    positions = []
    for row in rows:
        tdcc, product, issuer, currency, final, barrier, strike_date, ki_text, terms_status, terms_reason = row[:10]
        legs = []
        for seq, code, initial, strike, ki, basis in prices.get(tdcc, ()):
            day, close = latest.get(code, (None, None))
            base = basis or strike_date
            if close is not None and day is not None and base is not None:
                close = close * book.factor(code, base, day)
            legs.append(Leg(seq, code, initial, strike, ki, close, day))
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
                rates[currency],
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


def _fx_codes(currencies: Iterable[str]) -> set[str]:
    return {code for ccy in currencies if ccy != "USD" for code in (f"{ccy}USD", f"USD{ccy}")}


def _usd_rate(currency: str, closes: Mapping[str, tuple[dt.date, Decimal]]) -> Decimal | None:
    """1 單位幣別換幾 USD：收盤價表的 `XXXUSD` 直接用，只有 `USDXXX` 時取倒數；都沒有時 None。"""
    if currency == "USD":
        return Decimal(1)
    if f"{currency}USD" in closes:
        return closes[f"{currency}USD"][1]
    if f"USD{currency}" in closes:
        return 1 / closes[f"USD{currency}"][1]
    return None


# ---------------------------------------------------------------- 匯出


@dataclass(frozen=True)
class ExposureReceipt:
    path: Path
    count: int  # 列入的商品檔數
    counts: Mapping[str, int]  # 各分類檔數


def export_name(codes: Sequence[str], now: dt.datetime | None = None) -> str:
    """存檔視窗預設的檔名。"""
    tickers = "_".join(dict.fromkeys(ticker_of(code) for code in codes))[:40]
    return f"標的曝險分析_{tickers}_{now or dt.datetime.now():%Y%m%d_%H%M}.xlsx"


def export_exposure(
    conn: psycopg.Connection, codes: Sequence[str], until: dt.date, path: Path, *, today: dt.date | None = None
) -> ExposureReceipt:
    """分析並寫成一份 Excel：總覽、依分公司、依理專、通知明細、全部商品。"""
    with conn.transaction():  # 每個 SELECT 看同一個時間點
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        positions = load_positions(conn, codes, until)
        chosen = _latest_closes(conn, codes)
    results = [assess(p) for p in positions]
    report = _Report(results, frozenset(codes), today or dt.date.today())
    counts = {category: sum(r.category == category for r in results) for category in CATEGORIES}
    book = table_book(OVERVIEW, ("項目", "值"), _overview(codes, chosen, until, report))
    _style(book.active, widths=(30, 70))
    _overview_charts(book.active, report)
    for title, headers, rows, widths in (
        (BY_BRANCH, BRANCH_HEADERS, _rollup(report, lambda h: h.branch, lambda h: h.fc), ROLLUP_WIDTHS),
        (BY_FC, FC_HEADERS, _rollup(report, lambda h: h.fc, lambda h: h.branch), ROLLUP_WIDTHS),
        (DETAIL, DETAIL_HEADERS, _detail(report), DETAIL_WIDTHS),
        (ALL, ALL_HEADERS, [_all_row(report, r) for r in results], ALL_WIDTHS),
    ):
        sheet = book.create_sheet(title)
        sheet.append(list(headers))
        for row in rows:
            sheet.append([excel_value(value) for value in row])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        _style(sheet, widths=widths)
        if title in (BY_BRANCH, BY_FC):
            _rollup_charts(sheet, title, rows)
    save_export(book, path)
    return ExposureReceipt(path, len(results), counts)


@dataclass(frozen=True)
class _Report:
    """匯出用：分析結果、選定的代碼與分析日。"""

    results: Sequence[Assessed]
    chosen: frozenset[str]
    today: dt.date

    def caused_by_chosen(self, r: Assessed) -> bool:
        return r.worst is not None and r.worst.code in self.chosen

    def days_left(self, r: Assessed) -> int | None:
        final = r.position.final_date
        return None if final is None else (final - self.today).days


OVERVIEW = "總覽"
BY_BRANCH = "依分公司"
BY_FC = "依理專"
DETAIL = "通知明細"
ALL = "全部商品"
CHOSEN = "選定標的造成"
OTHERS = "其他標的造成"
_ROLLUP = (
    "會接股檔數",
    "會接股面額(USD)",
    "估損(USD)",
    f"估損：{CHOSEN}(USD)",
    f"估損：{OTHERS}(USD)",
    "扣配息後淨損益(USD)",
    "KI 未破下限檔數",
    "KI 未破下限面額(USD)",
    "接近執行價檔數",
    "接近執行價面額(USD)",
    "MKI 檔數",
    "連結檔數",
    "連結面額(USD)",
)
BRANCH_HEADERS = ("分公司", *_ROLLUP, "理專")
FC_HEADERS = ("理專", *_ROLLUP, "服務的分公司")
ROLLUP_WIDTHS = (12, 12, 18, 14, 20, 20, 20, 16, 20, 14, 20, 10, 10, 16, 40)
_POSITION = (
    "最終比價日",
    "剩餘天數",
    "分類",
    "緩衝分組",
)
_WORST = (
    "KI",
    "最差標的",
    "最差是選定標的",
    "最差表現",
    "執行價(%)",
    "下限價(%)",
    "距執行價",
    "距下限價",
    "最差標的收盤價",
    "執行價",
    "下限價",
)
DETAIL_HEADERS = (
    *_POSITION,
    "分公司",
    "理專",
    "TDCC",
    "商品",
    "發行機構",
    "幣別",
    *_WORST,
    "金額",
    "估損",
    "到期配息估計",
    "扣配息後淨損益",
    "估損(USD)",
    "扣配息後淨損益(USD)",
    "說明",
)
DETAIL_WIDTHS = (
    12,
    9,
    12,
    12,
    10,
    12,
    15,
    12,
    10,
    6,
    24,
    12,
    14,
    10,
    10,
    10,
    10,
    10,
    14,
    10,
    10,
    14,
    12,
    14,
    16,
    12,
    18,
    30,
)
ALL_HEADERS = (
    *_POSITION,
    "TDCC",
    "商品",
    "發行機構",
    "幣別",
    *_WORST,
    "面額",
    "面額(USD)",
    "估損",
    "到期配息估計",
    "扣配息後淨損益",
    "估損(USD)",
    "扣配息後淨損益(USD)",
    "各標的表現",
    "說明",
)
ALL_WIDTHS = (
    12,
    9,
    12,
    12,
    15,
    12,
    10,
    6,
    24,
    12,
    14,
    10,
    10,
    10,
    10,
    10,
    14,
    10,
    10,
    14,
    14,
    12,
    14,
    16,
    12,
    18,
    50,
    30,
)


@dataclass
class _Sum:
    """一群商品的檔數與 USD 合計；查不到匯率的商品算檔數、不算金額。"""

    tdccs: set[str] = field(default_factory=set)
    notional: Decimal = Decimal(0)
    loss: Decimal = Decimal(0)
    net: Decimal = Decimal(0)
    missing_rate: bool = False
    missing_coupon: bool = False

    def add(self, r: Assessed, amount: Decimal) -> None:
        self.tdccs.add(r.position.tdcc)
        rate = r.position.usd_rate
        if rate is None:
            self.missing_rate = True
            return
        self.notional += amount * rate
        self.loss += amount * r.loss_ratio * rate
        if r.category == DELIVER:
            if r.net_ratio is None:
                self.missing_coupon = True
            else:
                self.net += amount * r.net_ratio * rate

    def text(self, *, net: bool) -> str:
        notes = "；部分幣別查不到匯率，未計入" if self.missing_rate else ""
        if net and self.missing_coupon:
            notes += "；部分商品沒有配息資料，淨損益未計入"
        money = f"面額 USD {self.notional:,.0f}，估損 USD {self.loss:,.0f}"
        if net:
            money += f"，扣配息後淨損益 USD {self.net:,.0f}"
        return f"{len(self.tdccs)} 檔，{money}{notes}"


def _overview(
    codes: Sequence[str], chosen: Mapping[str, tuple[dt.date, Decimal]], until: dt.date, report: _Report
) -> list[tuple[object, ...]]:
    results = report.results
    rows: list[tuple[object, ...]] = [("分析日", report.today), ("最終比價日截止", until)]
    for code in codes:
        day, close = chosen.get(code, (None, None))
        rows.append((f"選定標的 {code}", f"最新收盤 {close}（{day}）" if close is not None else "收盤價表沒有這個代碼"))
    rows.append(("列入商品", f"{len(results)} 檔（在庫、連結選定標的、最終比價日不晚於截止日）"))
    for category in CATEGORIES:
        sel = [r for r in results if r.category == category]
        if category in (DELIVER, WATCH, NEAR):  # 分開最差標的是不是選定標的（#347）
            for label, wanted in ((CHOSEN, True), (OTHERS, False)):
                total = _Sum()
                for r in sel:
                    if report.caused_by_chosen(r) == wanted:
                        total.add(r, r.notional)
                rows.append((f"{category}｜{label}", total.text(net=category == DELIVER)))
            continue
        total = _Sum()
        for r in sel:
            total.add(r, r.notional)
        rows.append((category, total.text(net=False)))
    rows.append(("說明", "會接股：最差標的收盤價 < 執行價，且沒有 KI、收盤價 < 下限價或已觸及 AKI。"))
    rows.append(("", "選定標的造成：最差標的是選定的代碼；其他標的造成：籃子裡別的股票最差，損失不是選定標的造成的。"))
    rows.append(
        ("", "接近執行價：還在執行價之上、距執行價不到 10%；距執行價、距下限價 = 最差標的收盤價 ÷ 執行價或下限價 − 1。")
    )
    rows.append(
        (
            "",
            "估損 = 金額 ×（1 − 最差標的收盤價 ÷ 執行價）；到期配息估計 = 金額 × Coupon p.a. × 天期 ÷ 12（Super Bull 為 0）。",
        )
    )
    rows.append(("", "扣配息後淨損益 = 到期配息估計 − 估損，正的是連同配息仍賺；配息是估計，不看實際入帳。"))
    rows.append(("", "USD 以收盤價表最新匯率換算；查不到匯率的幣別，USD 欄空白、不算進各表的 USD 合計。"))
    return rows


def _usd(amount: Decimal | None, rate: Decimal | None) -> Decimal | None:
    return None if amount is None or rate is None else amount * rate


def _rollup(
    report: _Report, key: Callable[[Holding], str], other: Callable[[Holding], str]
) -> list[tuple[object, ...]]:
    """依 `key`（分公司或理專）加總、`other` 是最後一欄列出的另一邊；一檔分給同一個人好幾筆時只算一檔，依估損排序。"""
    sums: dict[str, dict[str, _Sum]] = defaultdict(lambda: defaultdict(_Sum))
    others: dict[str, set[str]] = defaultdict(set)
    for r in report.results:
        cause = CHOSEN if report.caused_by_chosen(r) else OTHERS
        for h in r.position.holdings:
            group = sums[key(h)]
            others[key(h)].add(other(h))
            group["all"].add(r, h.amount)
            group[r.category].add(r, h.amount)
            if r.category == DELIVER:
                group[cause].add(r, h.amount)
    rows = []
    for name, group in sums.items():
        deliver = group[DELIVER]
        rows.append(
            (
                name,
                len(deliver.tdccs),
                deliver.notional,
                deliver.loss,
                group[CHOSEN].loss,
                group[OTHERS].loss,
                deliver.net,
                len(group[WATCH].tdccs),
                group[WATCH].notional,
                len(group[NEAR].tdccs),
                group[NEAR].notional,
                len(group[MANUAL].tdccs),
                len(group["all"].tdccs),
                group["all"].notional,
                "、".join(sorted(others[name])),
            )
        )
    rows.sort(key=lambda row: (-row[3], -row[2], -row[8], -row[10], str(row[0])))
    return rows


def _worst_cells(report: _Report, r: Assessed) -> tuple[object, ...]:
    """KI 到下限價那幾欄（`_WORST`）。"""
    p, w = r.position, r.worst
    return (
        p.ki_text,
        w and w.code,
        None if w is None else ("是" if report.caused_by_chosen(r) else "否"),
        w and w.performance,
        _ratio(p.strike_pct),
        _ratio(p.ki_pct),
        w and w.to_strike,
        w and w.to_ki,
        w and w.close,
        w and w.strike,
        w and w.ki,
    )


def _money_cells(r: Assessed, amount: Decimal) -> tuple[object, ...]:
    """估損、到期配息估計、扣配息後淨損益與它們的 USD。"""
    coupon = r.position.coupon_ratio
    net = r.net_ratio
    loss = amount * r.loss_ratio
    net_amount = None if net is None else amount * net
    rate = r.position.usd_rate
    return loss, None if coupon is None else amount * coupon, net_amount, _usd(loss, rate), _usd(net_amount, rate)


def _detail(report: _Report) -> list[tuple[object, ...]]:
    rows = []
    for r in report.results:
        if r.category not in NOTIFY:
            continue
        p = r.position
        for h in p.holdings:
            loss, coupon, net, loss_usd, net_usd = _money_cells(r, h.amount)
            rows.append(
                (
                    p.final_date,
                    report.days_left(r),
                    r.category,
                    r.buffer_group,
                    h.branch,
                    h.fc,
                    p.tdcc,
                    p.product,
                    p.issuer,
                    p.currency,
                    *_worst_cells(report, r),
                    h.amount,
                    loss,
                    coupon,
                    net,
                    loss_usd,
                    net_usd,
                    r.reason,
                )
            )
    return rows


def _all_row(report: _Report, r: Assessed) -> tuple[object, ...]:
    p = r.position
    legs = " / ".join(f"{leg.code} {'-' if leg.performance is None else f'{leg.performance:+.1%}'}" for leg in p.legs)
    loss, coupon, net, loss_usd, net_usd = _money_cells(r, r.notional)
    return (
        p.final_date,
        report.days_left(r),
        r.category,
        r.buffer_group,
        p.tdcc,
        p.product,
        p.issuer,
        p.currency,
        *_worst_cells(report, r),
        r.notional,
        _usd(r.notional, p.usd_rate),
        loss,
        coupon,
        net,
        loss_usd,
        net_usd,
        legs,
        r.reason,
    )


def _ratio(pct: Decimal | None) -> Decimal | None:
    """資料庫存的百分點（80）→ 比例（0.8），Excel 用百分比格式顯示。"""
    return None if pct is None else pct / 100


MONEY_WORDS = ("面額", "金額", "估損", "配息", "淨損益")  # 表頭有這些字的欄是金額，顯示到整數、千分位
PERCENT_HEADERS = ("最差表現", "執行價(%)", "下限價(%)", "距執行價", "距下限價")
WIDEST = 70  # 欄寬上限（說明、各標的表現較長）


def _style(sheet, *, widths: Sequence[float]) -> None:
    headers = {cell.column: str(cell.value) for cell in sheet[1]}
    money = {col for col, name in headers.items() if any(word in name for word in MONEY_WORDS)}
    percent = {col for col, name in headers.items() if name in PERCENT_HEADERS}
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            text_cell(cell)
            if isinstance(cell.value, dt.date):
                cell.number_format = "yyyy/m/d"
            elif isinstance(cell.value, int | float | Decimal) and not isinstance(cell.value, bool):
                if cell.column in percent:
                    cell.number_format = "0.0%"
                elif cell.column in money:
                    cell.number_format = "#,##0"
    for i, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(i)].width = min(width, WIDEST)


# ---------------------------------------------------------------- 圖表（#347）

CHART_ROWS = 15  # 圖只畫排前面的幾名，太多條看不清楚
_CHART_COLUMN = 4  # 總覽的圖表資料從 D 欄開始（A、B 是文字）
_CHART_ANCHOR = "H"  # 圖放在資料表右邊


def _overview_charts(sheet, report: _Report) -> None:
    """總覽右邊：各分類面額、緩衝分組、會接股的最差標的，三張圖與資料表。"""
    results = report.results
    by_category = []
    for category in (DELIVER, WATCH, NEAR):
        split = {True: Decimal(0), False: Decimal(0)}
        for r in results:
            if r.category == category and r.position.usd_rate is not None:
                split[report.caused_by_chosen(r)] += r.notional * r.position.usd_rate
        by_category.append((category, split[True], split[False]))
    by_buffer = []
    for group in BUFFER_GROUPS:
        sel = [r for r in results if r.buffer_group == group]
        usd = sum((r.notional * r.position.usd_rate for r in sel if r.position.usd_rate is not None), Decimal(0))
        by_buffer.append((group, len(sel), usd))
    worst: dict[str, Decimal] = defaultdict(Decimal)
    for r in results:
        if r.category == DELIVER and r.worst is not None and r.position.usd_rate is not None:
            worst[r.worst.code] += r.notional * r.loss_ratio * r.position.usd_rate
    by_worst = sorted(worst.items(), key=lambda item: (-item[1], item[0]))[:CHART_ROWS] or [("（沒有會接股）", 0)]

    row = 1
    row = _chart_table(
        sheet, row, ("分類", f"面額：{CHOSEN}(USD)", f"面額：{OTHERS}(USD)"), by_category,
        _bar("各分類面額（USD）", stacked=True), anchor=1,
    )  # fmt: skip
    row = _chart_table(
        sheet, row + 2, ("緩衝分組（最差標的距執行價）", "檔數", "面額(USD)"), by_buffer,
        _bar("距執行價分組（面額 USD）"), anchor=18, series=(2,),
    )  # fmt: skip
    _chart_table(
        sheet, row + 2, ("會接股的最差標的", "估損(USD)"), by_worst,
        _bar("會接股估損：最差標的（USD）", horizontal=True), anchor=35,
    )  # fmt: skip
    for col, width in zip("DEFG", (28, 24, 24, 4), strict=True):
        sheet.column_dimensions[col].width = width


def _chart_table(
    sheet,
    top: int,
    headers: Sequence[str],
    rows: Sequence[Sequence[object]],
    chart: BarChart,
    *,
    anchor: int,
    series: Sequence[int] = (),
) -> int:
    """在 `top` 列、D 欄起寫一張小資料表，畫成 `chart` 放在 H 欄 `anchor` 列；回傳資料表最後一列。

    `series` 是要畫的欄（`headers` 的位置，0 是分類）；沒給就畫分類以外的每一欄。
    """
    first = _CHART_COLUMN
    for i, header in enumerate(headers):
        cell = sheet.cell(row=top, column=first + i, value=header)
        cell.font = Font(bold=True)
    for r, values in enumerate(rows, start=top + 1):
        for i, value in enumerate(values):
            cell = sheet.cell(row=r, column=first + i, value=excel_value(value))
            if isinstance(value, Decimal | int | float) and "(USD)" in headers[i]:
                cell.number_format = "#,##0"
    last = top + len(rows)
    for i in series or range(1, len(headers)):
        col = first + i
        chart.add_data(Reference(sheet, min_col=col, min_row=top, max_row=last), titles_from_data=True)
    chart.set_categories(Reference(sheet, min_col=first, min_row=top + 1, max_row=last))
    sheet.add_chart(chart, f"{_CHART_ANCHOR}{anchor}")
    return last


def _rollup_charts(sheet, title: str, rows: Sequence[Sequence[object]]) -> None:
    """依分公司、依理專右邊：會接股估損（選定標的／其他標的）與面額組成，各畫排前面的 `CHART_ROWS` 名。

    `rows` 是 `_rollup` 的列（已依估損排序）；估損圖只畫有估損的，沒有就不畫。
    """
    headers = [str(cell.value) for cell in sheet[1]]
    losses = sum(1 for row in rows[:CHART_ROWS] if row[headers.index("估損(USD)")])
    who = "分公司" if title == BY_BRANCH else "理專"
    charts = (
        (f"會接股估損（USD，前 {losses} 名{who}）", losses, (f"估損：{CHOSEN}(USD)", f"估損：{OTHERS}(USD)")),
        (f"面額組成（USD，前 {min(len(rows), CHART_ROWS)} 名）", min(len(rows), CHART_ROWS), _COMPOSITION),
    )
    anchor = get_column_letter(len(headers) + 2)
    top = 1
    for name, count, columns in charts:
        if not count:
            continue
        chart = _bar(name, stacked=True, horizontal=True)
        for column in columns:
            col = headers.index(column) + 1
            chart.add_data(Reference(sheet, min_col=col, min_row=1, max_row=1 + count), titles_from_data=True)
        chart.set_categories(Reference(sheet, min_col=1, min_row=2, max_row=1 + count))
        chart.height = max(7.5, 0.6 * count + 3)
        sheet.add_chart(chart, f"{anchor}{top}")
        top += int(chart.height * 2) + 2  # 一列約 0.5 公分


_COMPOSITION = ("會接股面額(USD)", "KI 未破下限面額(USD)", "接近執行價面額(USD)")


def _bar(title: str, *, stacked: bool = False, horizontal: bool = False) -> BarChart:
    chart = BarChart()
    chart.type = "bar" if horizontal else "col"
    chart.title = title
    chart.width = 18
    chart.height = 7.5
    if stacked:
        chart.grouping = "stacked"
        chart.overlap = 100
    if horizontal:
        chart.y_axis.majorGridlines = None
        chart.x_axis.scaling.orientation = "maxMin"  # 第一名在最上面
    chart.y_axis.numFmt = "#,##0"
    return chart
