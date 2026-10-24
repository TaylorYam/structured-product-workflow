"""算出核對條件（#88、ADR 0006、0008、0010、0011、0012）。

起單資料匯入或每日匯入完成後呼叫 `recalculate`：替每檔還沒鎖住的商品算出核對需要、但起單資料沒有的資料，
和上一次算的不同才寫入；已鎖住（`locked_at` 有值）的商品不重算。對外：
- `recalculate`：重算還沒鎖住的商品，回傳每檔的狀態（完成／等股價／等交易明細／待確認）。
- `release_differences`：作業人員放行交叉比對的差異（記 Windows 使用者名稱與時間），再重算那一檔。
- `recalculate_corrected`：更正商品條件（#55、ADR 0014）或交易明細（#116、ADR 0018）後重算那一檔，已鎖住也算；VWAP 保留 TS 回填的價格。
- `calculate`：一檔商品的計算本身（不碰資料庫）。

規則：
- 進場價：取價日比 Strike date（S）晚的第一份股價檔。依交易所假日表確認 S 是標的市場的交易日，
  而且 S 和取價日之間沒有該市場的其他交易日（股價檔裡是前一個交易日的價格）。收盤價取「最新收盤價」、
  開盤價取「最新開盤價」；VWAP 不算（由 TS 回填，#68），也不算待確認。
- 執行價／下限價／KO 價 = 股價檔原值 × 執行價(%)／KI(%)／KO(%) ÷ 100；四個價格各自 half-up 到小數第 4 位。
  KI 型態 NONE 時下限價空白。標的有問題時只有那個標的的價格空白。
- 分公司配置：交易明細的列（取消的列不在資料庫，PB 也算）同一分公司金額加總，依出現先後，
  取代 `workflow.branch_allocation`；交易明細還沒有這檔時保留原本的配置。
- 交叉比對：幣別、交易日、UF（交易明細是比例，起單資料是百分點）；承作金額不比（#97）。
  交易列之間不一樣也算不一致。放行過、兩邊的值都一樣的差異不再擋。
- 商品條件缺起單資料的必填欄（#114、ADR 0016）：不算價格、不交叉比對，待確認並寫出缺哪幾欄；
  分公司配置照樣由交易明細算。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import bisect
import datetime as dt
import getpass
import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from sp_workflow.database import OperatorError, require_in_transaction
from sp_workflow.product_record import missing_fields, plain
from sp_workflow.status import CheckTerms

if TYPE_CHECKING:
    from pathlib import Path

    import psycopg

    from fcn_checker.trading_calendar import Market, TradingCalendar

# 狀態
DONE = CheckTerms.DONE
WAIT_PRICES = CheckTerms.WAIT_PRICES
WAIT_TRADES = CheckTerms.WAIT_TRADES
TO_CONFIRM = CheckTerms.TO_CONFIRM
STATUSES = tuple(CheckTerms)  # 畫面列出各狀態檔數的順序

# 交叉比對的項目
CURRENCY, TRADE_DATE, UF = "幣別", "交易日", "UF"

CLOSE, OPEN, VWAP = "收盤價", "開盤價", "VWAP"
NO_BARRIER = "NONE"
_PRICE = Decimal("0.0001")
_HUNDRED = Decimal(100)
_BLANK = "空白"


class CheckTermsError(OperatorError):
    """核對條件算不了（設定檔有問題、商品已鎖住）；訊息是給作業人員看的中文。"""


def load_calendar(path: Path) -> TradingCalendar:
    """交易所假日表（`config/exchange_holidays.toml`）。"""
    from fcn_checker.config import load_exchange_holidays
    from fcn_checker.ingestion import IngestionError

    try:
        return load_exchange_holidays(path)
    except IngestionError as exc:
        raise CheckTermsError(str(exc)) from exc


# ---------- 計算（不碰資料庫） ----------


@dataclass(frozen=True)
class ProductTerms:
    """`workflow.product` 裡計算要用的欄位。

    必填欄可以空白（#114）：`missing` 有值時 `calculate` 不算價格、不交叉比對，所以後面的計算只會拿到
    `missing` 是空的商品，那時只有 `ki_pct` 與 UL_2～UL_5 可能是 None。
    """

    tdcc: str
    currency: str | None
    trade_date: dt.date | None
    strike_date: dt.date | None
    initial_pricing: str | None
    underlyings: tuple[str | None, ...]  # UL_1～UL_5
    strike_pct: Decimal | None
    ko_pct: Decimal | None
    barrier_type: str | None
    ki_pct: Decimal | None
    upfront_pct: Decimal | None  # 百分點（起單建檔的價格 98.01 → 1.99）
    missing: tuple[str, ...] = ()  # 空白的必填欄（畫面的欄名，依 `product_record.REQUIRED` 的順序）


@dataclass(frozen=True)
class TradeRow:
    """交易明細的一列（資料庫裡的都是沒有取消的）。"""

    trade_date: dt.date
    currency: str
    amount: Decimal
    branch: str
    uf: Decimal | None  # 比例（0.0199）


@dataclass(frozen=True)
class Quote:
    """股價檔裡一個代碼的價格；`conflict` 是同一個代碼有好幾列、值不同（價格沒有存）。"""

    last_close: Decimal | None
    last_open: Decimal | None
    conflict: bool = False


@dataclass(frozen=True)
class UnderlyingPrice:
    seq: int  # 1～5
    code: str
    initial: Decimal | None = None  # 進場價
    strike: Decimal | None = None  # 執行價
    ki: Decimal | None = None  # 下限價
    ko: Decimal | None = None  # KO 價


@dataclass(frozen=True)
class Difference:
    """交易明細和起單資料的一個差異；`released` 是作業人員放行過同樣的差異。"""

    item: str  # 幣別／交易日／UF
    order_value: str
    trade_value: str  # 交易列之間不一樣時以「、」分隔
    released: bool = False

    @property
    def key(self) -> tuple[str, str, str]:
        return self.item, self.order_value, self.trade_value

    def values_text(self) -> str:
        """兩邊的值；UF 另外寫出交易明細換算成百分點的值。"""
        shown = self.trade_value
        if self.item == UF:
            shown += f"（換算 {_uf_points(self.trade_value)}）"
        return f"起單資料 {self.order_value}、交易明細 {shown}"

    def describe(self) -> str:
        return f"{self.item}不一致：{self.values_text()}"


@dataclass(frozen=True)
class Calculation:
    status: str
    reason: str
    price_date: dt.date | None  # 用的股價檔
    prices: tuple[UnderlyingPrice, ...]
    allocation: tuple[tuple[str, Decimal], ...] | None  # (分公司, 金額)；交易明細還沒有這檔時 None（不動原本的）
    differences: tuple[Difference, ...]


def calculate(
    product: ProductTerms,
    trades: Sequence[TradeRow],
    price_dates: Sequence[dt.date],
    quotes: Mapping[tuple[dt.date, str], Quote],
    released: Collection[tuple[str, str, str]],
    calendar: TradingCalendar,
) -> Calculation:
    """一檔商品的核對條件。`price_dates` 是已匯入的股價檔取價日（由小到大），`quotes` 以 (取價日, 代碼) 查價格，
    `released` 是放行過的差異（`Difference.key`）。"""
    problems: list[str] = []
    notes: list[str] = []
    underlyings = [(n, code) for n, code in enumerate(product.underlyings, 1) if code]
    price_date = None
    waiting_prices = False
    prices = tuple(UnderlyingPrice(n, code) for n, code in underlyings)  # 算不出來時四個價格都空白
    if product.missing:
        reason = "；".join(filter(None, (f"缺 {'、'.join(product.missing)}", "" if trades else "交易明細還沒有這檔")))
        allocation = branch_allocation(trades) if trades else None
        return Calculation(TO_CONFIRM, reason, None, prices, allocation, ())
    pricing = _pricing(product.initial_pricing)
    if pricing == VWAP:
        notes.append("期初定價是 VWAP，價格由 TS 回填")
    elif pricing is None:
        problems.append(f"期初定價「{product.initial_pricing or _BLANK}」不是收盤價／開盤價／VWAP，算不出進場價")
    elif product.strike_date is None:
        problems.append("沒有 Strike date，找不到股價檔")
    else:
        price_date = price_file_after(product.strike_date, price_dates)
        if price_date is None:
            waiting_prices = True
            notes.append(f"還沒有取價日比 Strike date {product.strike_date} 晚的股價檔")
        else:
            found = []
            for n, code in underlyings:
                price, problem = _underlying_price(product, pricing, n, code, price_date, quotes, calendar)
                found.append(price)
                if problem:
                    problems.append(f"UL_{n} {code}：{problem}")
            prices = tuple(found)
    if product.barrier_type != NO_BARRIER and product.ki_pct is None:
        problems.append(f"KI 型態是 {product.barrier_type}，但沒有 KI(%)，算不出下限價")

    allocation = None
    differences: tuple[Difference, ...] = ()
    if trades:
        allocation = branch_allocation(trades)
        differences = tuple(Difference(*d.key, released=d.key in released) for d in cross_check(product, trades))
        problems += [d.describe() for d in differences if not d.released]
        released_texts = [f"{d.item}（{d.values_text()}）" for d in differences if d.released]
        if released_texts:
            notes.append("已放行的差異：" + "；".join(released_texts))
    else:
        notes.insert(0, "交易明細還沒有這檔")

    if problems:
        status = TO_CONFIRM
    elif not trades:
        status = WAIT_TRADES
    elif waiting_prices:
        status = WAIT_PRICES
    else:
        status = DONE
    return Calculation(status, "；".join(problems + notes), price_date, prices, allocation, differences)


def price_file_after(strike_date: dt.date, price_dates: Sequence[dt.date]) -> dt.date | None:
    """取價日比 Strike date 晚的第一份股價檔；還沒有時 None。"""
    i = bisect.bisect_right(price_dates, strike_date)
    return price_dates[i] if i < len(price_dates) else None


def branch_allocation(trades: Iterable[TradeRow]) -> tuple[tuple[str, Decimal], ...]:
    """同一分公司的金額加總，依在交易明細出現的先後。"""
    totals: dict[str, Decimal] = {}
    for trade in trades:
        totals[trade.branch] = totals.get(trade.branch, Decimal(0)) + trade.amount
    return tuple(totals.items())


def cross_check(product: ProductTerms, trades: Sequence[TradeRow]) -> list[Difference]:
    """幣別、交易日、UF 不一致的項目（還沒看放行）。交易列之間不一樣也算。"""
    differences = []
    currencies = _distinct(t.currency for t in trades)
    if _distinct(c.strip().upper() for c in currencies) != [product.currency.strip().upper()]:
        differences.append(Difference(CURRENCY, product.currency, "、".join(currencies)))
    dates = _distinct(t.trade_date for t in trades)
    if dates != [product.trade_date]:
        differences.append(Difference(TRADE_DATE, product.trade_date.isoformat(), "、".join(map(str, dates))))
    ufs = _distinct(t.uf for t in trades)
    if len(ufs) != 1 or ufs[0] is None or ufs[0] * _HUNDRED != product.upfront_pct:
        shown = "、".join(_BLANK if uf is None else _text(uf) for uf in ufs)
        differences.append(Difference(UF, _text(product.upfront_pct), shown))
    return differences


def _underlying_price(
    product: ProductTerms,
    pricing: str,
    n: int,
    code: str,
    price_date: dt.date,
    quotes: Mapping[tuple[dt.date, str], Quote],
    calendar: TradingCalendar,
) -> tuple[UnderlyingPrice, str]:
    """一個標的的四個價格與問題（空字串表示沒問題）；有問題時價格空白。"""
    blank = UnderlyingPrice(n, code)
    strike_date = product.strike_date
    assert strike_date is not None
    problem = _calendar_problem(calendar, code, strike_date, price_date)
    if problem:
        return blank, problem
    quote = quotes.get((price_date, code))
    if quote is None:
        return blank, f"股價檔（取價日 {price_date}）沒有這個代碼"
    if quote.conflict:
        return blank, f"股價檔（取價日 {price_date}）這個代碼有好幾列、值不同"
    raw = quote.last_close if pricing == CLOSE else quote.last_open
    column = "最新收盤價" if pricing == CLOSE else "最新開盤價"
    if raw is None:
        return blank, f"股價檔（取價日 {price_date}）沒有{column}"
    if raw <= 0:
        return blank, f"股價檔（取價日 {price_date}）的{column} {raw} 不是正數"
    ki = (
        None
        if product.barrier_type == NO_BARRIER or product.ki_pct is None
        else _round(raw * product.ki_pct / _HUNDRED)
    )
    return (
        UnderlyingPrice(
            n,
            code,
            initial=_round(raw),
            strike=_round(raw * product.strike_pct / _HUNDRED),
            ki=ki,
            ko=_round(raw * product.ko_pct / _HUNDRED),
        ),
        "",
    )


def _calendar_problem(calendar: TradingCalendar, code: str, strike_date: dt.date, price_date: dt.date) -> str:
    """S 要是標的市場的交易日，而且 S 和取價日之間沒有這個市場的其他交易日。"""
    market = calendar.market_of(code)
    if market is None:
        return "交易所不在交易所假日表，無法確認股價檔是不是 Strike date 的價格"
    if not market.covers(strike_date, price_date):
        return (
            f"{strike_date}～{price_date} 不在{market.name}假日表的涵蓋範圍"
            f"（{market.covered_from}～{market.covered_through}）"
        )
    if not _trading_day(market, strike_date):
        return f"Strike date {strike_date} 不是{market.name}的交易日"
    between = [
        day
        for day in (strike_date + dt.timedelta(days=i) for i in range(1, (price_date - strike_date).days))
        if _trading_day(market, day)
    ]
    if between:
        return (
            f"Strike date {strike_date} 和取價日 {price_date} 之間還有{market.name}的交易日"
            f" {'、'.join(map(str, between))}（是不是漏匯了股價檔？）"
        )
    return ""


def _trading_day(market: Market, day: dt.date) -> bool:
    return day.weekday() < 5 and day not in market.holidays


def _pricing(text: str | None) -> str | None:
    """期初定價 → 收盤價／開盤價／VWAP；其他寫法 None。"""
    value = (text or "").strip()
    if value.upper() == VWAP:
        return VWAP
    return value if value in (CLOSE, OPEN) else None


def _distinct(values: Iterable[Any]) -> list[Any]:
    return list(dict.fromkeys(values))


def _round(value: Decimal) -> Decimal:
    return value.quantize(_PRICE, rounding=ROUND_HALF_UP)


def _text(value: Decimal) -> str:
    return format(plain(value), "f")


def _uf_points(trade_value: str) -> str:
    """交易明細的 UF（比例）換成百分點，給錯訊看。"""
    return "、".join(t if t == _BLANK else _text(Decimal(t) * _HUNDRED) for t in trade_value.split("、"))


# ---------- 資料庫 ----------


@dataclass(frozen=True)
class CalcLine:
    """一檔的結果；`changed` 是這次重算和上一次不同（有寫入）。"""

    tdcc: str
    status: str
    reason: str
    changed: bool
    differences: tuple[Difference, ...] = ()

    @property
    def problem(self) -> bool:
        return self.status == TO_CONFIRM

    @property
    def unreleased(self) -> tuple[Difference, ...]:
        """還沒放行的差異（可以放行）。"""
        return tuple(d for d in self.differences if not d.released)


@dataclass(frozen=True)
class CalcReceipt:
    lines: tuple[CalcLine, ...]  # 每檔還沒鎖住的商品，依 TDCC

    def count(self, status: str) -> int:
        return sum(line.status == status for line in self.lines)

    @property
    def changed(self) -> int:
        return sum(line.changed for line in self.lines)

    def line_of(self, tdcc: str) -> CalcLine | None:
        return next((line for line in self.lines if line.tdcc == tdcc), None)

    def to_show(self) -> tuple[CalcLine, ...]:
        """匯入結果要列出的：這次有變的，和還沒完成的。"""
        return tuple(line for line in self.lines if line.changed or line.status != DONE)


# 計算要用的交易明細欄（`TradeRow`）：人工維護改了這幾欄才算影響核對條件（#116）
CHECK_TRADE_COLUMNS = ("trade_date", "currency", "amount", "branch", "uf")


def recalculate(
    conn: psycopg.Connection, calendar: TradingCalendar, *, tdccs: Collection[str] | None = None
) -> CalcReceipt:
    """重算還沒鎖住的商品（`tdccs` 有值時只算這幾檔），和上一次不同的寫入；整次在同一個 transaction 裡。"""
    with conn.transaction():
        products = _products(conn, tdccs, unlocked=True)
        lines = [line for line, _, _ in _recalculate(conn, products, calendar)]
    return CalcReceipt(tuple(lines))


_PRICE_NAMES = (("進場價", "initial"), ("執行價", "strike"), ("下限價", "ki"), ("KO價", "ko"))  # 更正紀錄的項目名


def recalculate_corrected(
    conn: psycopg.Connection,
    calendar: TradingCalendar,
    tdcc: str,
    previous: Mapping[str, Any],
    *,
    trades_changed: bool = False,
) -> tuple[CalcLine, tuple[tuple[str, Any, Any], ...]] | None:
    """更正商品條件（#55）之後重算這一檔（已鎖住也算），回傳 (結果, 改變的價格 [(UL_n_進場價…, 前, 後)])。

    `previous` 是更正前的商品欄位。沒有算過核對條件的商品（以前清單更新匯入的），或改的欄都不影響核對條件
    （例：Coupon），不重算、回傳 None（股價檔或假日表之後變了也不會把原本的價格算掉）。
    VWAP 的價格是 TS 回填的：更正前也是 VWAP、同一位置同一標的的保留，執行價(%)、KI(%)、KO(%) 或 Barrier Type
    改了的價格由保留的進場價重算；其他位置的價格空白。`trades_changed`：同一次也改了交易明細裡核對條件會用到的欄
    （`CHECK_TRADE_COLUMNS`，#116），條件沒改也重算（分公司配置、交叉比對）。在呼叫端的 transaction 裡執行。
    """
    require_in_transaction(conn)
    if conn.execute("SELECT 1 FROM workflow.check_terms WHERE tdcc = %s", (tdcc,)).fetchone() is None:
        return None
    before = _product(tdcc, previous)
    products = _products(conn, [tdcc])
    if products == [before] and not trades_changed:
        return None
    ((line, stored, calc),) = _recalculate(conn, products, calendar, vwap_from=before)
    old = {p.seq: p for p in stored.prices} if stored is not None else {}
    new = {p.seq: p for p in calc.prices}
    changes = []
    for seq in sorted(old.keys() | new.keys()):
        was, now = old.get(seq, UnderlyingPrice(seq, "")), new.get(seq, UnderlyingPrice(seq, ""))
        for name, attr in _PRICE_NAMES:
            if getattr(was, attr) != getattr(now, attr):
                changes.append((f"UL_{seq}_{name}", getattr(was, attr), getattr(now, attr)))
    return line, tuple(changes)


def _products(conn: psycopg.Connection, tdccs: Collection[str] | None, *, unlocked: bool = False) -> list[ProductTerms]:
    """這幾檔（None：全部）計算用的商品條件，依 TDCC 排、鎖住商品條件列（`product_terms.read_many`）。

    `unlocked`：只要還沒鎖住的（`product_terms.locked`）。讀到的每一檔都鎖到 transaction 結束，包括已鎖住的。
    """
    from sp_workflow.product_terms import (  # product_terms 也 import 這裡（寫入後重算），避免循環 import
        locked,
        read_many,
    )

    stored = read_many(conn, None if tdccs is None else list(tdccs), lock=True)
    return [_product(s.tdcc, s.columns) for s in stored.values() if not (unlocked and locked(s))]


def _recalculate(
    conn: psycopg.Connection,
    products: Sequence[ProductTerms],
    calendar: TradingCalendar,
    *,
    vwap_from: ProductTerms | None = None,
) -> list[tuple[CalcLine, Calculation | None, Calculation]]:
    """算這幾檔，和上一次不同的寫入；回傳 (結果, 上一次算的, 這次算的)。`vwap_from` 見 `recalculate_corrected`。"""
    keys = [p.tdcc for p in products]
    trades = _trades(conn, keys)
    price_dates = [d for (d,) in conn.execute("SELECT DISTINCT price_date FROM workflow.price_import ORDER BY 1")]
    quotes = _quotes(conn, products, price_dates)
    released = _released(conn, keys)
    stored = _stored(conn, keys)
    lines = []
    for product in products:
        calc = calculate(
            product,
            trades.get(product.tdcc, []),
            price_dates,
            quotes,
            released.get(product.tdcc, set()),
            calendar,
        )
        old = stored.get(product.tdcc)
        if vwap_from is not None and _pricing(product.initial_pricing) == VWAP:
            calc = _keep_vwap(calc, product, vwap_from, () if old is None else old.prices)
        changed = not _same(calc, old)
        if changed:
            _write(conn, product.tdcc, calc)
        lines.append((CalcLine(product.tdcc, calc.status, calc.reason, changed, calc.differences), old, calc))
    return lines


def _keep_vwap(
    calc: Calculation, product: ProductTerms, before: ProductTerms, old: Sequence[UnderlyingPrice]
) -> Calculation:
    """更正後的 VWAP 商品：保留 TS 回填的價格，只重算比例改了的；保留不了的位置寫在說明裡。"""
    kept = {p.seq: p for p in old} if _pricing(before.initial_pricing) == VWAP else {}
    no_ki = product.barrier_type == NO_BARRIER or product.ki_pct is None
    ki_same = (product.barrier_type, product.ki_pct) == (before.barrier_type, before.ki_pct)
    prices, blank = [], []
    for price in calc.prices:
        was = kept.get(price.seq)
        if was is None or was.code != price.code or was.initial is None:
            prices.append(price)
            blank.append(f"UL_{price.seq} {price.code}")
            continue
        prices.append(
            UnderlyingPrice(
                price.seq,
                price.code,
                initial=was.initial,
                strike=_kept_or_derived(was.initial, was.strike, product.strike_pct, before.strike_pct),
                ki=None if no_ki else _kept_or_derived(was.initial, was.ki, product.ki_pct, before.ki_pct, ki_same),
                ko=_kept_or_derived(was.initial, was.ko, product.ko_pct, before.ko_pct),
            )
        )
    if not blank:
        return replace(calc, prices=tuple(prices))
    problem = f"更正後 {'、'.join(blank)} 沒有 TS 回填的價格"  # 價格空白，不能算完成
    reason = "；".join(filter(None, (problem, calc.reason)))
    return replace(calc, status=TO_CONFIRM, prices=tuple(prices), reason=reason)


def _kept_or_derived(
    initial: Decimal, kept: Decimal | None, pct: Decimal, pct_before: Decimal | None, same: bool = True
) -> Decimal | None:
    """比例沒改時保留 TS 回填的價格，改了由進場價重算。"""
    return kept if same and pct == pct_before else _round(initial * pct / _HUNDRED)


def release_differences(
    conn: psycopg.Connection,
    tdcc: str,
    differences: Iterable[Difference],
    calendar: TradingCalendar,
    *,
    user: str | None = None,
) -> CalcLine:
    """放行這幾個差異（作業人員在畫面上看到的），記 Windows 使用者名稱與時間，再重算這一檔。

    資料在這之間變了的話，放行的是舊的差異，新的差異照樣待確認。商品已鎖住或不在資料庫時丟 `CheckTermsError`。
    """
    from sp_workflow.product_terms import locked, read  # 同 `_products`，避免循環 import

    with conn.transaction():
        found = read(conn, tdcc, lock=True)
        if found is None:
            raise CheckTermsError(f"{tdcc}：資料庫沒有這檔商品")
        if locked(found):
            raise CheckTermsError(f"{tdcc}：已鎖住，不重算，也不用放行")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO workflow.cross_check_release (tdcc, item, order_value, trade_value, released_by)"
                " VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [(tdcc, *d.key, user or getpass.getuser()) for d in differences],
            )
        (line,) = recalculate(conn, calendar, tdccs=[tdcc]).lines
    return line


def _product(tdcc: str, values: Mapping[str, Any]) -> ProductTerms:
    """商品條件的欄（`PRODUCT_COLUMNS` → 值）→ 計算用的欄位；用不到的欄只用來看缺哪幾欄（#114），值不放進來：
    更正只改 Coupon、到期日時照舊不重算。"""
    return ProductTerms(
        tdcc=tdcc,
        currency=values["currency"],
        trade_date=values["trade_date"],
        strike_date=values["strike_date"],
        initial_pricing=values["initial_pricing"],
        underlyings=tuple(values[f"underlying_{n}"] for n in range(1, 6)),
        strike_pct=values["strike_pct"],
        ko_pct=values["ko_pct"],
        barrier_type=values["barrier_type"],
        ki_pct=values["ki_pct"],
        upfront_pct=values["upfront_pct"],
        missing=tuple(missing_fields(values)),
    )


def _trades(conn: psycopg.Connection, tdccs: Sequence[str]) -> dict[str, list[TradeRow]]:
    found: dict[str, list[TradeRow]] = {}
    for tdcc, *values in conn.execute(
        f"SELECT tdcc, {', '.join(CHECK_TRADE_COLUMNS)} FROM workflow.trade_row WHERE tdcc = ANY(%s) ORDER BY tdcc, row_seq",
        (list(tdccs),),
    ):
        found.setdefault(tdcc, []).append(TradeRow(*values))
    return found


def _quotes(
    conn: psycopg.Connection, products: Sequence[ProductTerms], price_dates: Sequence[dt.date]
) -> dict[tuple[dt.date, str], Quote]:
    """每檔用到的那一份股價檔裡、它的標的的價格。"""
    wanted = {
        (price_date, code)
        for p in products
        if p.strike_date is not None and (price_date := price_file_after(p.strike_date, price_dates)) is not None
        for code in p.underlyings
        if code
    }
    if not wanted:
        return {}
    dates = sorted({d for d, _ in wanted})
    codes = sorted({c for _, c in wanted})
    return {
        (price_date, code): Quote(last_close, last_open, conflict)
        for price_date, code, last_close, last_open, conflict in conn.execute(
            "SELECT price_date, code, last_close, last_open, conflict_rows IS NOT NULL FROM workflow.daily_price"
            " WHERE price_date = ANY(%s) AND code = ANY(%s)",
            (dates, codes),
        )
    }


def _released(conn: psycopg.Connection, tdccs: Sequence[str]) -> dict[str, set[tuple[str, str, str]]]:
    found: dict[str, set[tuple[str, str, str]]] = {}
    for tdcc, *key in conn.execute(
        "SELECT tdcc, item, order_value, trade_value FROM workflow.cross_check_release WHERE tdcc = ANY(%s)",
        (list(tdccs),),
    ):
        found.setdefault(tdcc, set()).add(tuple(key))
    return found


def _stored(conn: psycopg.Connection, tdccs: Sequence[str]) -> dict[str, Calculation]:
    """上一次算的結果（分公司配置是資料庫目前的）。"""
    prices: dict[str, list[UnderlyingPrice]] = {}
    for tdcc, *values in conn.execute(
        "SELECT tdcc, seq, code, initial_price, strike_price, ki_price, ko_price FROM workflow.underlying_price"
        " WHERE tdcc = ANY(%s) ORDER BY tdcc, seq",
        (list(tdccs),),
    ):
        prices.setdefault(tdcc, []).append(UnderlyingPrice(*values))
    allocations: dict[str, list[tuple[str, Decimal]]] = {}
    for tdcc, branch, amount in conn.execute(
        "SELECT tdcc, branch, amount FROM workflow.branch_allocation WHERE tdcc = ANY(%s) ORDER BY tdcc, seq",
        (list(tdccs),),
    ):
        allocations.setdefault(tdcc, []).append((branch, amount))
    return {
        tdcc: Calculation(
            status,
            reason or "",
            price_date,
            tuple(prices.get(tdcc, ())),
            tuple(allocations.get(tdcc, ())),
            tuple(Difference(*d) for d in differences or ()),
        )
        for tdcc, status, reason, price_date, differences in conn.execute(
            "SELECT tdcc, status, reason, price_date, differences FROM workflow.check_terms WHERE tdcc = ANY(%s)",
            (list(tdccs),),
        )
    }


def _same(calc: Calculation, stored: Calculation | None) -> bool:
    """和上一次算的一樣；交易明細還沒有這檔時不比分公司配置（不會動它）。"""
    if stored is None:
        return False
    if calc.allocation is None:
        stored = replace(stored, allocation=None)
    return calc == stored


def _write(conn: psycopg.Connection, tdcc: str, calc: Calculation) -> None:
    differences = [[*d.key, d.released] for d in calc.differences] or None
    conn.execute(
        "INSERT INTO workflow.check_terms (tdcc, status, reason, price_date, differences)"
        " VALUES (%s, %s, %s, %s, %s::jsonb)"
        " ON CONFLICT (tdcc) DO UPDATE SET status = EXCLUDED.status, reason = EXCLUDED.reason,"
        " price_date = EXCLUDED.price_date, differences = EXCLUDED.differences, calculated_at = now()",
        (
            tdcc,
            calc.status,
            calc.reason or None,
            calc.price_date,
            None if differences is None else json.dumps(differences, ensure_ascii=False),
        ),
    )
    conn.execute("DELETE FROM workflow.underlying_price WHERE tdcc = %s", (tdcc,))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO workflow.underlying_price (tdcc, seq, code, initial_price, strike_price, ki_price, ko_price)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(tdcc, p.seq, p.code, p.initial, p.strike, p.ki, p.ko) for p in calc.prices],
        )
    if calc.allocation is not None:
        conn.execute("DELETE FROM workflow.branch_allocation WHERE tdcc = %s", (tdcc,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO workflow.branch_allocation (tdcc, seq, branch, amount) VALUES (%s, %s, %s, %s)",
                [(tdcc, seq, branch, amount) for seq, (branch, amount) in enumerate(calc.allocation, 1)],
            )
