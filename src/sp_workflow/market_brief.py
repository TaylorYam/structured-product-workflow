"""盤勢速報（#467、#469、#471、#473）：美股大跌隔天給主管看的全局曝險 Excel——總覽、大跌標的、重災區，與幾個大標的的
「執行價 × 最終比價日」熱圖。作業人員把每個分頁的表格複製到 PPT，一頁一張投影片。

- 範圍：全部在庫且存續的商品（同 `exposure.load_positions`，不設最終比價日截止），用收盤價表最新的收盤價；
  最差標的照 `exposure.assess`。只呈現目前狀態（檔數、面額、距離），不放任何預估金額（#352）。
- 只看執行價（#471）：一檔的狀態只看最差標的收盤價距執行價（`Assessed.to_strike`）——低於是跌破K、
  0～10% 是距K 10% 內、10% 以上是高於K 10% 以上；KI（AKI 觸及、低於下限價）不另外分類，MKI 也照執行價判斷。
  判斷不判斷照 `Assessed.strike_judged`（#482）：已比價、核對條件未完成、缺進場價／執行價／收盤價的不判斷；
  缺 KI 型態或下限價（`assess` 歸無法計算）不影響。
- 面額：交易明細（沒有時用庫存的分公司配置）合計，依商品幣別用最新的 `XXXUSD` 匯率折成 USD（收盤價表與每日價格
  取日期新的那筆），單位 M（百萬）。缺匯率的幣別不計入面額、列在 `Brief.missing_fx` 給視窗提示；檔數照算。
- 總覽（#469、#471）：只列高於K 10% 以上、距K 10% 內、跌破K 三列，跌破K 在最下面；
  面額佔比的分母仍是全部在庫面額（已比價、無法計算不列，但算在分母裡）。
- 標的：照股票代號合併各交易所的代碼（同 `exposure.Choice`）。昨收用連結最多那個代碼最新的收盤價；
  大跌標的的近 1／2／3 個月表現（#469）= 昨收 ÷ 往回 N 個月那天（或之前最近一筆，最多再往前 `STALE_DAYS` 天）
  的收盤價 − 1，中間有拆股時舊價格先換到目前的價格基準（`SplitBook.factor`）。
  一個標的的「跌破K」看商品裡這個標的本身（不是最差標的）：收盤價低於它的執行價。
- 重災區（#473）：全部有判斷的商品依最差標的距執行價（+10% 以上、+10%～0 與總覽同一個判斷，再往下每 10% 一組）
  × 距最終比價日分組，合計檔數對得上總覽三列加總；超過 180 天的都併進「6–12個月」。
- 熱圖（「市價格 × 最終比價日」，#471）：連結這個標的、最終比價日不早於今天的商品，依這個標的的執行價換算成的市價格分區間（拆股換到目前的價格基準：
  執行價 × 原始收盤價 ÷ 換回價格基準的收盤價），橫的是最終比價月份；昨收所在的區間在昨收處切開，昨收線以上
  是執行價高於昨收（已跌破K）。
- 版面與字型照 `slide_table` 的投影片版面（#483：A1 標題、A3 起一塊連續的表格、微軟正黑體＋Calibri、關格線）；
  這裡只決定欄、列與報表自己的上色。旁邊不放東西、不用條件式格式（顏色是固定底色），欄寬與列數控制在一張 16:9
  投影片內；複製表格貼到 PPT 不用再調整。
- 只讀資料庫，不改任何資料。

`plan_bands`、`month_columns` 等只算版面、不碰資料庫；`load_brief` 讀資料庫；`export_brief` 寫 Excel。
"""

from __future__ import annotations

import bisect
import calendar
import datetime as dt
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from openpyxl.styles import Border, PatternFill, Side

from sp_workflow.close_prices import latest_closes
from sp_workflow.exposure import (
    K_BROKEN,
    NEAR_BUFFER,
    Assessed,
    assess,
    load_positions,
    ticker_of,
)
from sp_workflow.file_tools import save_export
from sp_workflow.slide_table import BOLD_FONT, BROKEN_FILL, Column, font, slide_book, write_slide
from sp_workflow.stock_splits import SplitBook, load_splits
from sp_workflow.underlying_pricing import Leg

if TYPE_CHECKING:
    import psycopg

USD = "USD"
BLANK_CURRENCY = "（幣別空白）"
MILLION = Decimal(1_000_000)
TOP_HEATMAPS = 5  # 預設畫熱圖的標的數（連結面額前幾名）
TOP_TICKERS = 10  # 大跌標的列幾個
TRAILING_MONTHS = (1, 2, 3)  # 大跌標的的近幾個月表現（#469）
STALE_DAYS = 14  # 往回 N 個月那天之前最多再找幾天的收盤價
NO_TRAILING = (None,) * len(TRAILING_MONTHS)  # 沒有收盤價的標的
MAX_MONTHS = 13  # 熱圖最多幾個月份欄，超過的併入「之後」
BANDS_EACH_SIDE = 7  # 熱圖昨收上下各幾個整數區間，再外面併成「以上／以下」

# 總覽的列
NEAR_ROW = "距K 10% 內"
SAFE_ROW = "高於K 10% 以上"
OVERVIEW_SHOWN = (SAFE_ROW, NEAR_ROW, K_BROKEN)  # 總覽列出的列，跌破K 在最下面（#469、#471）

# 重災區的列（#473）：還沒跌破的兩列跟總覽同一個判斷，跌破的再依最差標的距執行價（負的是低於）下界（含）分組
SAFE_BUCKET = "+10% 以上"
NEAR_BUCKET = "+10%～0"
DISTANCE_BUCKETS = (
    (Decimal("-0.10"), "0～-10%"),
    (Decimal("-0.20"), "-10～-20%"),
    (Decimal("-0.30"), "-20～-30%"),
    (None, "-30% 以下"),
)
HOTSPOT_ROWS = (SAFE_BUCKET, NEAR_BUCKET, *(label for _, label in DISTANCE_BUCKETS))
# 重災區：距最終比價日幾天以內 → 欄名；超過 180 天的都併進最後一欄（#473 拿掉「12個月以上」）
DUE_BUCKETS = ((30, "1個月內"), (90, "1–3個月"), (180, "3–6個月"), (None, "6–12個月"))


# ---------------------------------------------------------------- 資料


@dataclass(frozen=True)
class Exposure:
    """一檔在庫商品的判斷結果與折 USD 面額（缺匯率時 None）。"""

    result: Assessed
    usd: Decimal | None

    @property
    def amount(self) -> Decimal:
        return self.usd or Decimal(0)


@dataclass(frozen=True)
class Quote:
    """一個代碼最新兩筆收盤價，與近 `TRAILING_MONTHS` 個月的表現（拆股已換算；缺歷史收盤價時 None）。"""

    day: dt.date
    close: Decimal
    previous: Decimal | None = None
    trailing: tuple[Decimal | None, ...] = ()

    @property
    def change(self) -> Decimal | None:
        """漲跌：最新 ÷ 前一筆 − 1。"""
        return None if not self.previous else self.close / self.previous - 1


@dataclass
class TickerRow:
    """一個股票代號（合併各交易所代碼）的連結狀況；面額折 USD。"""

    ticker: str
    codes: tuple[str, ...]  # 依連結檔數多到少
    quote: Quote | None  # 連結最多、有收盤價的代碼
    products: int = 0
    notional: Decimal = Decimal(0)
    k_count: int = 0  # 這個標的本身低於執行價
    k_notional: Decimal = Decimal(0)


@dataclass(frozen=True)
class Brief:
    """一次讀出的盤勢資料。`rows` 依連結面額大到小（同面額依代號）。"""

    today: dt.date
    exposures: tuple[Exposure, ...]
    rows: tuple[TickerRow, ...]
    raw_closes: Mapping[str, Decimal]  # 代碼 → 最新原始收盤價（熱圖把執行價換到目前價格基準用）
    missing_fx: frozenset[str] = frozenset()

    @property
    def close_day(self) -> dt.date | None:
        days = [r.quote.day for r in self.rows if r.quote is not None]
        return max(days) if days else None

    def default_heatmaps(self) -> list[str]:
        return [r.ticker for r in self.rows[:TOP_HEATMAPS]]


# ---------------------------------------------------------------- 讀資料庫


def load_brief(conn: psycopg.Connection, today: dt.date | None = None) -> Brief:
    """全部在庫且存續的商品：判斷、折 USD 面額，與每個標的的連結狀況。每個 SELECT 看同一個時間點。"""
    today = today or dt.date.today()
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        positions = load_positions(conn, None, dt.date.max, today=today)
        codes = {leg.code for p in positions for leg in p.legs}
        quotes = _with_trailing(conn, _quotes(conn, codes), SplitBook(load_splits(conn)))
        currencies = {p.currency or BLANK_CURRENCY for p in positions} - {USD}
        rates = _usd_rates(conn, currencies - {BLANK_CURRENCY})
    missing = frozenset(c for c in currencies if c not in rates)
    exposures = []
    for p in positions:
        result = assess(p, today)
        rate = Decimal(1) if p.currency == USD else rates.get(p.currency or BLANK_CURRENCY)
        exposures.append(Exposure(result, None if rate is None else result.notional * rate))
    return Brief(
        today,
        tuple(exposures),
        tuple(ticker_rows(exposures, quotes)),
        {code: q.close for code, q in quotes.items()},
        missing,
    )


def _quotes(conn: psycopg.Connection, codes: Iterable[str]) -> dict[str, Quote]:
    """每個代碼最新的收盤價（同曝險分析，`latest_closes`），加上它前一筆（視窗的漲跌% 用）。"""
    latest = latest_closes(conn, codes)
    previous = dict(
        conn.execute(
            "SELECT DISTINCT ON (c.code) c.code, c.close FROM workflow.close_price AS c"
            " JOIN unnest(%s::text[], %s::date[]) AS latest (code, day) ON c.code = latest.code"
            " AND c.close_date < latest.day ORDER BY c.code, c.close_date DESC",
            (list(latest), [day for day, _ in latest.values()]),
        ).fetchall()
    )
    return {code: Quote(day, close, previous.get(code)) for code, (day, close) in latest.items()}


def months_before(day: dt.date, n: int) -> dt.date:
    """往回 n 個月的同一天；那個月沒有這一天時取月底（例：5/31 往回 3 個月 → 2/28）。"""
    index = day.year * 12 + day.month - 1 - n
    year, month = divmod(index, 12)
    month += 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def trailing_returns(
    code: str, quote: Quote, history: Sequence[tuple[dt.date, Decimal]], book: SplitBook
) -> tuple[Decimal | None, ...]:
    """近 `TRAILING_MONTHS` 個月的表現：昨收 ÷ 往回 N 個月那天（或之前 `STALE_DAYS` 天內最近一筆）的收盤價 − 1。
    舊收盤價先換到昨收那天的價格基準（拆股）。`history` 依日期排序。"""
    days = [day for day, _ in history]
    found: list[Decimal | None] = []
    for n in TRAILING_MONTHS:
        target = months_before(quote.day, n)
        i = bisect.bisect_right(days, target) - 1
        if i < 0 or (target - days[i]).days > STALE_DAYS or not history[i][1]:
            found.append(None)
            continue
        day, close = history[i]
        found.append(quote.close / (close * book.factor(code, quote.day, day)) - 1)
    return tuple(found)


def _with_trailing(conn: psycopg.Connection, quotes: dict[str, Quote], book: SplitBook) -> dict[str, Quote]:
    if not quotes:
        return quotes
    since = min(months_before(q.day, max(TRAILING_MONTHS)) for q in quotes.values()) - dt.timedelta(days=STALE_DAYS)
    history: dict[str, list[tuple[dt.date, Decimal]]] = defaultdict(list)
    for code, day, close in conn.execute(
        "SELECT code, close_date, close FROM workflow.close_price WHERE code = ANY(%s) AND close_date >= %s"
        " ORDER BY code, close_date",
        (sorted(quotes), since),
    ):
        history[code].append((day, close))
    return {code: replace(q, trailing=trailing_returns(code, q, history[code], book)) for code, q in quotes.items()}


def _usd_rates(conn: psycopg.Connection, currencies: Iterable[str]) -> dict[str, Decimal]:
    """幣別 → 1 單位折幾 USD：`XXXUSD` 在收盤價表或每日價格日期最新的一筆。"""
    pairs = {f"{c}USD": c for c in currencies}
    if not pairs:
        return {}
    rows = conn.execute(
        "SELECT DISTINCT ON (code) code, day, rate FROM ("
        " SELECT code, close_date AS day, close AS rate FROM workflow.close_price WHERE code = ANY(%s)"
        " UNION ALL SELECT code, price_date, last_close FROM workflow.daily_price"
        " WHERE code = ANY(%s) AND last_close IS NOT NULL) AS fx"
        " WHERE rate > 0 ORDER BY code, day DESC",
        (list(pairs), list(pairs)),
    ).fetchall()
    return {pairs[code]: rate for code, _, rate in rows}


# ---------------------------------------------------------------- 彙整


def ticker_rows(exposures: Sequence[Exposure], quotes: Mapping[str, Quote]) -> list[TickerRow]:
    """每個股票代號的連結檔數、面額，與這個標的本身跌破K 的檔數、面額；依面額大到小。"""
    by_code: dict[str, int] = defaultdict(int)
    for e in exposures:
        for code in {leg.code for leg in e.result.position.legs}:
            by_code[code] += 1
    codes_of: dict[str, list[str]] = defaultdict(list)
    for code in by_code:
        codes_of[ticker_of(code)].append(code)
    rows: dict[str, TickerRow] = {}
    for ticker, codes in codes_of.items():
        ordered = tuple(sorted(codes, key=lambda c: (-by_code[c], c)))
        main = next((c for c in ordered if c in quotes), None)
        rows[ticker] = TickerRow(ticker, ordered, quotes.get(main) if main else None)
    for e in exposures:
        position = e.result.position
        for ticker in dict.fromkeys(ticker_of(leg.code) for leg in position.legs):
            row = rows[ticker]
            row.products += 1
            row.notional += e.amount
            legs = [leg for leg in position.legs if ticker_of(leg.code) == ticker]
            if below_strike(e, legs):
                row.k_count += 1
                row.k_notional += e.amount
    return sorted(rows.values(), key=lambda r: (-r.notional, r.ticker))


def below_strike(e: Exposure, legs: Iterable[Leg]) -> bool:
    """只看執行價判斷的商品裡，`legs`（某個標的本身的那幾個）有一個收盤價低於它的執行價。"""
    return e.result.strike_judged and any(leg.below_strike for leg in legs)


def overview_row(e: Exposure) -> str | None:
    """總覽的哪一列：只看最差標的距執行價（#471）；不判斷的不列（None）。"""
    distance = e.result.to_strike
    if distance is None:
        return None
    if distance < 0:
        return K_BROKEN
    return NEAR_ROW if distance <= NEAR_BUFFER else SAFE_ROW


def broken(exposures: Iterable[Exposure]) -> list[Exposure]:
    """重災區：最差標的目前低於執行價的商品（#471 只看 K）。"""
    return [e for e in exposures if (distance := e.result.to_strike) is not None and distance < 0]


def distance_bucket(e: Exposure) -> str | None:
    """重災區的哪一列：依最差標的距執行價分組（#473 也列還沒跌破的）；不判斷的不列（None）。"""
    row = overview_row(e)
    if row is None:
        return None
    if row != K_BROKEN:
        return SAFE_BUCKET if row == SAFE_ROW else NEAR_BUCKET
    to_strike = e.result.to_strike
    assert to_strike is not None
    for low, label in DISTANCE_BUCKETS:
        if low is None or to_strike >= low:
            return label
    raise AssertionError("最後一個區間沒有下界")


def due_bucket(final: dt.date | None, today: dt.date) -> str:
    days = None if final is None else (final - today).days
    for limit, label in DUE_BUCKETS:
        if limit is None or (days is not None and days <= limit):
            return label
    raise AssertionError("最後一個區間沒有上限")


# ---------------------------------------------------------------- 熱圖版面


@dataclass(frozen=True)
class Band:
    """執行價區間 [low, high)；最上面一格 high 是 None（以上）、最下面一格 low 是 None（以下）。"""

    low: Decimal | None
    high: Decimal | None

    def holds(self, price: Decimal) -> bool:
        return (self.low is None or price >= self.low) and (self.high is None or price < self.high)

    def label(self) -> str:
        if self.high is None:
            return f"{_edge(self.low)} 以上"  # type: ignore[arg-type]
        if self.low is None:
            return f"{_edge(self.high)} 以下"
        return f"{_edge(self.low)}-{_edge(self.high)}"


def _edge(value: Decimal) -> str:
    """區間邊界：整數不寫小數，昨收切開的那個邊界與小於 1 的寬度寫兩位小數。"""
    return f"{value:,.0f}" if value == value.to_integral_value() else f"{value:,.2f}"


NICE_STEPS = (Decimal(1), Decimal(2), Decimal("2.5"), Decimal(5))


def nice_step(price: Decimal) -> Decimal:
    """熱圖的區間寬度：約昨收的 6%，取 1、2、2.5、5 乘 10 的次方裡最接近的。"""
    target = price * Decimal("0.06")
    if target <= 0:
        return Decimal(1)
    power = Decimal(10) ** math.floor(math.log10(target))
    candidates = [n * power for n in NICE_STEPS] + [10 * power]
    return min(candidates, key=lambda s: (abs(s - target), s))


def plan_bands(strikes: Sequence[Decimal], close: Decimal | None) -> tuple[list[Band], Decimal | None]:
    """熱圖的列，高價在上；回傳 (區間, 昨收線畫在哪個價位)。

    以昨收為中心、整數寬度的區間，上下各最多 `BANDS_EACH_SIDE` 格，再外面併成「以上／以下」；
    昨收落在區間中間時把那格在昨收處切成兩格，線畫在兩格之間。沒有昨收時以執行價的中位數為中心、不畫線。
    """
    if not strikes:
        return [], None
    center = close if close is not None else sorted(strikes)[len(strikes) // 2]
    step = nice_step(center)
    base = (center / step).to_integral_value(rounding=ROUND_FLOOR) * step  # 中心所在那格的下界
    low_needed = min(strikes)
    high_needed = max(strikes)
    below = 0
    while below < BANDS_EACH_SIDE and base - below * step > low_needed:
        below += 1
    above = 0
    while above < BANDS_EACH_SIDE and base + (above + 1) * step <= high_needed:
        above += 1
    lowest = base - below * step
    highest = base + (above + 1) * step
    edges = [lowest + i * step for i in range(below + above + 2)]
    if close is not None and close not in edges:  # 中心是昨收，一定落在 [lowest, highest) 裡
        edges = sorted([*edges, close])
    bands = [Band(a, b) for a, b in zip(edges, edges[1:], strict=False)]
    if low_needed < lowest:
        bands.insert(0, Band(None, lowest))
    if high_needed >= highest:
        bands.append(Band(highest, None))
    return list(reversed(bands)), close


def month_columns(months: Iterable[tuple[int, int]]) -> tuple[list[tuple[int, int]], bool]:
    """熱圖的月份欄：最早到最晚連續的月份，最多 `MAX_MONTHS` 欄；超過時前 `MAX_MONTHS - 1` 個月加「之後」一欄。"""
    found = sorted(set(months))
    if not found:
        return [], False
    (y, m), last = found[0], found[-1]
    columns = []
    while (y, m) <= last:
        columns.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    if len(columns) <= MAX_MONTHS:
        return columns, False
    return columns[: MAX_MONTHS - 1], True


@dataclass(frozen=True)
class Heatmap:
    row: TickerRow
    bands: list[Band]
    line: Decimal | None  # 昨收線的價位（在這價位之上的區間是執行價高於昨收）
    months: list[tuple[int, int]]
    later: bool  # 有「之後」欄
    cells: dict[tuple[int, int], Decimal] = field(default_factory=dict)  # (列, 欄) → 面額 USD
    products: int = 0  # 畫進熱圖的檔數與面額（標題用，跟格子加總一致）
    notional: Decimal = Decimal(0)
    k_count: int = 0  # 其中這個標的本身低於執行價
    k_notional: Decimal = Decimal(0)


def heatmap(brief: Brief, ticker: str) -> Heatmap:
    """一個標的的熱圖：連結它、最終比價日不早於今天的商品，依它的執行價 × 最終比價月份加總面額。

    執行價換成跟昨收同一個尺度（`_strike_on`）：拆股換到目前的價格基準；同一個代號在別的交易所掛牌、
    價格尺度不同（例：不同幣別）時，照那個代碼自己距執行價的比例換到昨收那個代碼的價格。
    """
    row = next(r for r in brief.rows if r.ticker == ticker)
    close = row.quote.close if row.quote else None
    entries: list[tuple[Decimal, tuple[int, int], Exposure, Leg]] = []
    for e in brief.exposures:
        p = e.result.position
        if p.final_date is None or p.final_date < brief.today:
            continue
        leg = next((x for x in p.legs if ticker_of(x.code) == ticker and x.strike is not None), None)
        if leg is not None:  # 一檔同一個標的只算一次
            strike = _strike_on(leg, close, brief.raw_closes.get(leg.code))
            entries.append((strike, (p.final_date.year, p.final_date.month), e, leg))
    bands, line = plan_bands([strike for strike, *_ in entries], close)
    months, later = month_columns(month for _, month, *_ in entries)
    cells: dict[tuple[int, int], Decimal] = defaultdict(Decimal)
    k_count, k_notional = 0, Decimal(0)
    for strike, month, e, leg in entries:
        r = next(i for i, band in enumerate(bands) if band.holds(strike))
        c = months.index(month) if month in months else len(months)
        cells[(r, c)] += e.amount
        if below_strike(e, [leg]):
            k_count += 1
            k_notional += e.amount
    notional = sum((e.amount for _, _, e, _ in entries), Decimal(0))
    return Heatmap(row, bands, line, months, later, dict(cells), len(entries), notional, k_count, k_notional)


def _strike_on(leg: Leg, close: Decimal | None, raw: Decimal | None) -> Decimal:
    """執行價換到昨收的尺度：執行價 × 昨收 ÷ 這個代碼換回價格基準的收盤價（同代碼時就是拆股換算，ADR 0025）。
    沒有昨收時照這個代碼自己的原始收盤價換；都沒有收盤價時用資料庫的執行價。"""
    assert leg.strike is not None
    scale = close if close is not None else raw
    if scale is None or not leg.close:
        return leg.strike
    return leg.strike * scale / leg.close


# ---------------------------------------------------------------- 匯出

DOWN_FONT = font(color="C00000")  # 下跌、跌破：深紅字
TOTAL_FILL = PatternFill("solid", fgColor="DDEBF7")  # 合計列淺藍
THICK = Side(style="thick", color="000000")  # 熱圖的昨收線
MILLIONS = '[=0]"-";[<0.05]"<0.1M";0.0"M"'  # 面額（百萬）：0 寫 -，很小寫 <0.1M
COUNT = '[=0]"-";#,##0'
PERCENT = "+0.0%;-0.0%;0.0%"
SHARE = "0.0%"
PRICE = "#,##0.00"
HEAT_STOPS = (
    (0.0, (0xFF, 0xF8, 0xEC)),
    (0.45, (0xFC, 0xD5, 0x8E)),
    (0.75, (0xF4, 0xA2, 0x7A)),
    (1.0, (0xE0, 0x5A, 0x50)),
)


def heat_fill(value: Decimal, top: Decimal) -> PatternFill | None:
    """熱圖的底色：面額 ÷ 最大面額開根號（大的少、小的多，開根號拉開層次）在淺米 → 橘 → 紅之間內插；0 不填。"""
    if value <= 0 or top <= 0:
        return None
    t = math.sqrt(float(value / top))
    for (t0, c0), (t1, c1) in zip(HEAT_STOPS, HEAT_STOPS[1:], strict=False):
        if t <= t1:
            k = 0 if t1 == t0 else (t - t0) / (t1 - t0)
            rgb = "".join(f"{round(a + (b - a) * k):02X}" for a, b in zip(c0, c1, strict=True))
            return PatternFill("solid", fgColor=rgb)
    return PatternFill("solid", fgColor="".join(f"{v:02X}" for v in HEAT_STOPS[-1][1]))


@dataclass(frozen=True)
class BriefReceipt:
    path: Path
    products: int  # 在庫檔數
    broken: int  # 跌破K 檔數
    heatmaps: tuple[str, ...]
    missing_fx: frozenset[str]


def brief_name(now: dt.datetime | None = None) -> str:
    return f"盤勢速報_{now or dt.datetime.now():%Y%m%d_%H%M}.xlsx"


def export_brief(
    conn: psycopg.Connection, tickers: Sequence[str], path: Path, *, today: dt.date | None = None
) -> BriefReceipt:
    """讀資料、寫成 Excel：總覽、大跌標的、重災區，與 `tickers` 每個一頁熱圖（依傳入順序）。"""
    brief = load_brief(conn, today)
    known = {r.ticker for r in brief.rows}
    chosen = [t for t in dict.fromkeys(tickers) if t in known]
    write_brief(brief, chosen, path)
    return BriefReceipt(path, len(brief.exposures), len(broken(brief.exposures)), tuple(chosen), brief.missing_fx)


def write_brief(brief: Brief, tickers: Sequence[str], path: Path) -> None:
    book = slide_book()
    _overview(book.create_sheet("總覽"), brief)
    _tickers(book.create_sheet("大跌標的"), brief)
    _hotspots(book.create_sheet("重災區"), brief)
    for ticker in tickers:
        _heatmap(book.create_sheet(_sheet_name(f"熱圖_{ticker}")), heatmap(brief, ticker))
    save_export(book, path)


def _sheet_name(name: str) -> str:
    return "".join("_" if ch in "[]:*?/\\" else ch for ch in name)[:31]


def _m(value: Decimal) -> str:
    """標題與文字格裡的面額（百萬）：四捨五入到一位小數，跟 Excel 顯示數字格的方式一樣。"""
    return f"{(value / MILLION).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP):,}"


def _millions(value: Decimal) -> float:
    return float(value / MILLION)


def _day(day: dt.date | None) -> str:
    return "—" if day is None else f"{day:%Y/%m/%d}"


def _overview(sheet, brief: Brief) -> None:
    total = sum((e.amount for e in brief.exposures), Decimal(0))
    counts: dict[str, int] = defaultdict(int)
    amounts: dict[str, Decimal] = defaultdict(Decimal)
    for e in brief.exposures:
        row = overview_row(e)
        if row is None:
            continue  # 不判斷的不列，但算在佔比的分母裡
        counts[row] += 1
        amounts[row] += e.amount
    rows = [
        (name, counts[name], _millions(amounts[name]), float(amounts[name] / total) if total else 0.0)
        for name in OVERVIEW_SHOWN
    ]
    first = write_slide(
        sheet,
        f"盤勢總覽｜ELN 商品依表現最差標的距執行價（K）分類｜{_day(brief.close_day)} 收盤"
        f"｜在庫 {len(brief.exposures)} 檔、連結 {len(brief.rows)} 個標的",
        (
            Column("ELN商品表現最差標的", 24),
            Column("檔數", 12, COUNT),
            Column("面額（M USD）", 18, MILLIONS),
            Column("面額佔比", 14, SHARE),
        ),
        rows,
    )
    for r in range(first, first + len(rows)):
        name = sheet.cell(r, 1).value
        if name == K_BROKEN:
            for c in range(1, 5):
                sheet.cell(r, c).fill = BROKEN_FILL


def _tickers(sheet, brief: Brief) -> None:
    rows = []
    for t in brief.rows[:TOP_TICKERS]:
        q = t.quote
        rows.append(
            (
                t.ticker,
                None if q is None else float(q.close),
                *(None if v is None else float(v) for v in (q.trailing if q else NO_TRAILING)),
                t.products,
                _millions(t.notional),
                t.k_count,
                _millions(t.k_notional),
            )
        )
    performance = [f"標的價格\n近{n}月表現" for n in TRAILING_MONTHS]
    broken_heads = ("跌破執行價\n檔數", "跌破執行價\n面額")
    first = write_slide(
        sheet,
        f"連結面額前 {TOP_TICKERS} 大標的｜{_day(brief.close_day)} 收盤｜面額單位 M USD",
        (
            Column("標的", 11),
            Column("昨收", 11, PRICE),
            *(Column(name, 12, PERCENT) for name in performance),
            Column("連結檔數", 11, COUNT),
            Column("連結面額", 11, MILLIONS),
            Column(broken_heads[0], 12, COUNT),
            Column(broken_heads[1], 12, MILLIONS),
        ),
        rows,
    )
    headers = [cell.value for cell in sheet[first - 1]]
    performance_cols = [headers.index(name) + 1 for name in performance]
    broken_cols = [headers.index(name) + 1 for name in broken_heads]
    for r in range(first, first + len(rows)):
        for c in performance_cols:
            value = sheet.cell(r, c).value
            if isinstance(value, float) and value < 0:
                sheet.cell(r, c).font = DOWN_FONT
        for c in broken_cols:
            if sheet.cell(r, c).value:
                sheet.cell(r, c).font = DOWN_FONT


def _hotspots(sheet, brief: Brief) -> None:
    hit = broken(brief.exposures)
    distance_rows = list(HOTSPOT_ROWS)
    due_cols = [label for _, label in DUE_BUCKETS]
    count: dict[tuple[str, str], int] = defaultdict(int)
    amount: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for e in brief.exposures:
        if (bucket := distance_bucket(e)) is None:
            continue  # 不判斷的不列，合計因此等於總覽三列加總
        keys = [bucket, "合計"]
        due = due_bucket(e.result.position.final_date, brief.today)
        for key in keys:
            for col in (due, "合計"):
                count[(key, col)] += 1
                amount[(key, col)] += e.amount

    def text(key: tuple[str, str]) -> str:
        return "-" if not count[key] else f"{_m(amount[key])}（{count[key]:,}檔）"  # 單位 M USD 寫在標題（#473）

    names = [*distance_rows, "合計"]
    rows = [(name, *(text((name, col)) for col in [*due_cols, "合計"])) for name in names]
    total = sum((e.amount for e in hit), Decimal(0))
    first = write_slide(
        sheet,
        f"重災區：最差標的跌破執行價｜{len(hit):,} 檔、{_m(total)}M USD｜依距執行價 × 距最終比價日｜面額單位 M USD",
        (Column("最差標的距K", 20), *(Column(col, 19) for col in due_cols), Column("合計", 22)),
        rows,
    )
    top = max((amount[(n, c)] for n in distance_rows for c in due_cols), default=Decimal(0))
    for i, name in enumerate(names):
        r = first + i
        for j, col in enumerate(due_cols, start=2):
            if name in distance_rows and (fill := heat_fill(amount[(name, col)], top)):
                sheet.cell(r, j).fill = fill
        if name == "合計":
            for c in range(1, len(due_cols) + 3):
                sheet.cell(r, c).fill, sheet.cell(r, c).font = TOTAL_FILL, BOLD_FONT


def _heatmap(sheet, h: Heatmap) -> None:
    labels = [band.label() for band in h.bands]
    month_names = [f"{y}/{m:02d}" for y, m in h.months] + (["之後"] if h.later else [])
    title = (  # #473：照作業人員改的標題，不放昨收
        f"執行價 × 最終比價日 熱圖：{h.row.ticker}｜連結 {h.products:,} 檔 {_m(h.notional)}M"
        f"｜目前跌破執行價 共{h.k_count:,} 檔 {_m(h.k_notional)}M USD"
    )
    rows = [
        (label, *(_millions(h.cells.get((r, c), Decimal(0))) for c in range(len(month_names))))
        for r, label in enumerate(labels)
    ]
    first = write_slide(
        sheet, title, (Column("市價格＼最終比價日", 22), *(Column(m, 10.5, MILLIONS) for m in month_names)), rows
    )
    top = max(h.cells.values(), default=Decimal(0))
    for (r, c), value in h.cells.items():
        if fill := heat_fill(value, top):
            sheet.cell(first + r, c + 2).fill = fill
    if h.line is not None:
        # 線畫在「下界 = 昨收線」那格的下緣（高價在上）
        for r, band in enumerate(h.bands):
            if band.low == h.line:
                for c in range(1, len(month_names) + 2):
                    cell = sheet.cell(first + r, c)
                    cell.border = Border(
                        left=cell.border.left, right=cell.border.right, top=cell.border.top, bottom=THICK
                    )
                break
