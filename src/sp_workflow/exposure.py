"""標的曝險分析（#345）：選定的標的大跌時，近期到期、連結它們的在庫商品有哪些會接股、面額與估損多少。

- 範圍：存續狀態在庫（沒有還有效的存續事件）、連結任一選定標的、最終比價日不晚於截止日的商品，商品種類不限
  （FCN、FCN_Express、Super Bull）。已過最終比價日、還在庫的（例：等股價）一併列入。
- 價格：每個標的用收盤價表最新的一筆，依拆股登記換回價格基準（同比價，`stock_splits.SplitBook.factor`）；不做假設價格。
- 分類看最差標的（收盤價 ÷ 進場價最低）：收盤價 < 執行價，且沒有 KI、收盤價 < 下限價或比價已記 AKI 觸及 → 會接股；
  < 執行價但還在下限價之上 → EKI 未破下限（觀察）；MKI 不比、人工確認；其他是高於執行價；缺價格的無法計算。
- 金額照交易明細逐筆（分公司、理專、金額），沒有交易明細的用庫存的分公司配置（理專寫 `-`）。
  估損 = 金額 ×（1 − 最差標的收盤價 ÷ 執行價），不含已領配息；USD 以收盤價表最新的匯率換算，查不到匯率的留空。
- 只讀資料庫，不改任何資料。

`assess` 只判斷一檔、不碰資料庫；`load_choices`、`load_positions` 讀資料庫；`export_exposure` 寫 Excel。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from openpyxl.utils import get_column_letter

from sp_workflow.check_terms import NO_BARRIER
from sp_workflow.file_tools import MAX_WIDTH, excel_value, save_export, table_book, text_cell
from sp_workflow.lifecycle import IN_STOCK, lifecycle_status
from sp_workflow.stock_splits import SplitBook, load_splits

if TYPE_CHECKING:
    import psycopg

DELIVER = "會接股"
WATCH = "EKI 未破下限"
MANUAL = "MKI 人工確認"
SAFE = "高於執行價"
UNKNOWN = "無法計算"
CATEGORIES = (DELIVER, WATCH, MANUAL, SAFE, UNKNOWN)
NOTIFY = (DELIVER, WATCH, MANUAL)  # 通知明細列的分類
MKI = "MKI"
AKI_HIT = "AKI hit"  # 比價寫的 KI 欄：`AKI hit 日期 標的`（observation._ki_text）
NO_FC = "-"
_UNDERLYING_COLUMNS = ", ".join(f"underlying_{n}" for n in range(1, 6))  # workflow.product 的連結標的


@dataclass(frozen=True)
class Choice:
    """視窗上可選的一個標的：在庫商品連結它的檔數與最新收盤價。"""

    code: str
    products: int
    close_date: dt.date | None
    close: Decimal | None


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


@dataclass(frozen=True)
class Holding:
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
    legs: tuple[Leg, ...]
    holdings: tuple[Holding, ...]
    usd_rate: Decimal | None  # 1 單位幣別換幾 USD


@dataclass(frozen=True)
class Assessed:
    position: Position
    category: str
    worst: Leg | None
    loss_ratio: Decimal  # 估損占面額的比例；不會接股時是 0
    reason: str = ""

    @property
    def notional(self) -> Decimal:
        return sum((h.amount for h in self.position.holdings), Decimal(0))


def assess(position: Position) -> Assessed:
    """依最差標的判斷一檔的分類與估損比例（見 module 說明）。"""
    if not position.legs:
        return Assessed(position, UNKNOWN, None, Decimal(0), "沒有標的價格（核對條件沒完成）")
    missing = [leg.code for leg in position.legs if leg.performance is None or leg.strike is None]
    if missing:
        return Assessed(position, UNKNOWN, None, Decimal(0), f"缺進場價、執行價或收盤價：{'、'.join(missing)}")
    worst = min(position.legs, key=lambda leg: (leg.performance, leg.seq))
    assert worst.close is not None and worst.strike is not None
    if position.barrier_type == MKI:
        return Assessed(position, MANUAL, worst, Decimal(0), "MKI 每期觀察，系統不比，請人工確認")
    if worst.close >= worst.strike:
        return Assessed(position, SAFE, worst, Decimal(0))
    knocked = (
        position.barrier_type in (None, NO_BARRIER)
        or (worst.ki is not None and worst.close < worst.ki)
        or position.ki_text.startswith(AKI_HIT)
    )
    if not knocked:
        return Assessed(position, WATCH, worst, Decimal(0), "低於執行價，但還在下限價之上")
    return Assessed(position, DELIVER, worst, 1 - worst.close / worst.strike)


# ---------------------------------------------------------------- 讀資料庫


def load_choices(conn: psycopg.Connection) -> list[Choice]:
    """在庫商品連結的標的代碼，依代碼排序。"""
    rows = conn.execute(
        f"SELECT DISTINCT tdcc, code FROM workflow.product, unnest(ARRAY[{_UNDERLYING_COLUMNS}]) AS code"
        " WHERE code IS NOT NULL"
    ).fetchall()
    statuses = lifecycle_status(conn, [tdcc for tdcc, _ in rows])
    counts: dict[str, int] = defaultdict(int)
    for tdcc, code in rows:
        if statuses[tdcc].status == IN_STOCK:
            counts[code] += 1
    latest = _latest_closes(conn, counts)
    return [Choice(code, counts[code], *latest.get(code, (None, None))) for code in sorted(counts)]


def load_positions(conn: psycopg.Connection, codes: Iterable[str], until: dt.date) -> list[Position]:
    """在庫、連結任一 `codes`、最終比價日不晚於 `until` 的商品，依最終比價日、TDCC 排序。"""
    wanted = sorted(set(codes))
    rows = conn.execute(
        "SELECT p.tdcc, p.product, p.issuer, p.currency, p.final_valuation_date, p.barrier_type, p.strike_date,"
        " s.ki FROM workflow.product p LEFT JOIN workflow.pricing_status s ON s.tdcc = p.tdcc"
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
    for tdcc, product, issuer, currency, final, barrier, strike_date, ki_text in rows:
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
                tuple(legs),
                tuple(holdings.get(tdcc, ())),
                rates[currency],
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
    tickers = "_".join(dict.fromkeys(code.split()[0] for code in codes))[:40]
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
    counts = {category: sum(r.category == category for r in results) for category in CATEGORIES}
    book = table_book(OVERVIEW, ("項目", "值"), _overview(codes, chosen, until, today or dt.date.today(), results))
    _style(book.active, widths=(26, 60))
    for title, headers, rows, widths in (
        (BY_BRANCH, BRANCH_HEADERS, _rollup(results, lambda h: h.branch, lambda h: h.fc), BRANCH_WIDTHS),
        (BY_FC, FC_HEADERS, _rollup(results, lambda h: h.fc, lambda h: h.branch), BRANCH_WIDTHS),
        (DETAIL, DETAIL_HEADERS, _detail(results, set(codes)), DETAIL_WIDTHS),
        (ALL, ALL_HEADERS, [_all_row(r, set(codes)) for r in results], ALL_WIDTHS),
    ):
        sheet = book.create_sheet(title)
        sheet.append(list(headers))
        for row in rows:
            sheet.append([excel_value(value) for value in row])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        _style(sheet, widths=widths)
    save_export(book, path)
    return ExposureReceipt(path, len(results), counts)


OVERVIEW = "總覽"
BY_BRANCH = "依分公司"
BY_FC = "依理專"
DETAIL = "通知明細"
ALL = "全部商品"
_ROLLUP = (
    "會接股檔數",
    "會接股面額(USD)",
    "估損(USD)",
    "觀察檔數",
    "觀察面額(USD)",
    "觀察帳面估損(USD)",
    "MKI 檔數",
    "連結檔數",
    "連結面額(USD)",
)
BRANCH_HEADERS = ("分公司", *_ROLLUP, "理專")
FC_HEADERS = ("理專", *_ROLLUP, "服務的分公司")
BRANCH_WIDTHS = (12, 12, 18, 14, 10, 16, 18, 10, 10, 16, 40)
DETAIL_HEADERS = (
    "最終比價日",
    "分類",
    "分公司",
    "理專",
    "TDCC",
    "商品",
    "發行機構",
    "幣別",
    "KI",
    "最差標的",
    "最差表現",
    "最差是選定標的",
    "最差標的收盤價",
    "執行價",
    "下限價",
    "金額",
    "估損",
    "估損(USD)",
    "說明",
)
DETAIL_WIDTHS = (12, 14, 10, 12, 15, 12, 10, 6, 24, 12, 10, 14, 14, 10, 10, 14, 12, 12, 30)
ALL_HEADERS = (
    "最終比價日",
    "分類",
    "TDCC",
    "商品",
    "發行機構",
    "幣別",
    "KI",
    "面額",
    "面額(USD)",
    "最差標的",
    "最差表現",
    "最差是選定標的",
    "估損",
    "估損(USD)",
    "各標的表現",
    "說明",
)
ALL_WIDTHS = (12, 14, 15, 12, 10, 6, 24, 14, 14, 12, 10, 14, 12, 12, 50, 30)


def _overview(
    codes: Sequence[str],
    chosen: Mapping[str, tuple[dt.date, Decimal]],
    until: dt.date,
    today: dt.date,
    results: Sequence[Assessed],
) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = [("分析日", today), ("最終比價日截止", until)]
    for code in codes:
        day, close = chosen.get(code, (None, None))
        rows.append((f"選定標的 {code}", f"最新收盤 {close}（{day}）" if close is not None else "收盤價表沒有這個代碼"))
    rows.append(("列入商品", f"{len(results)} 檔（在庫、連結選定標的、最終比價日不晚於截止日）"))
    for category in CATEGORIES:
        sel = [r for r in results if r.category == category]
        amount, loss, partial = _usd_sum(sel)
        note = "；部分幣別查不到匯率，未計入" if partial else ""
        rows.append((category, f"{len(sel)} 檔，面額 USD {amount:,.0f}，估損 USD {loss:,.0f}{note}"))
    rows.append(("說明", "會接股：最差標的收盤價 < 執行價，且沒有 KI、收盤價 < 下限價或已觸及 AKI。"))
    rows.append(("", "估損 = 金額 ×（1 − 最差標的收盤價 ÷ 執行價），不含已領配息；USD 以收盤價表最新匯率換算。"))
    return rows


def _usd(amount: Decimal, rate: Decimal | None) -> Decimal | None:
    return None if rate is None else amount * rate


def _usd_sum(results: Iterable[Assessed]) -> tuple[Decimal, Decimal, bool]:
    """面額與估損的 USD 合計，以及有沒有查不到匯率而沒算進去的。"""
    amount = loss = Decimal(0)
    partial = False
    for r in results:
        rate = r.position.usd_rate
        if rate is None:
            partial = True
            continue
        amount += r.notional * rate
        loss += r.notional * r.loss_ratio * rate
    return amount, loss, partial


def _rollup(results: Sequence[Assessed], key, other) -> list[tuple[object, ...]]:
    """依分公司或理專加總（一檔分給同一個人好幾筆時只算一檔），依估損排序。"""
    groups: dict[str, dict] = defaultdict(
        lambda: {"tdccs": defaultdict(set), "usd": defaultdict(Decimal), "other": set()}
    )
    for r in results:
        rate = r.position.usd_rate
        for h in r.position.holdings:
            g = groups[key(h)]
            g["other"].add(other(h))
            g["tdccs"]["all"].add(r.position.tdcc)
            g["tdccs"][r.category].add(r.position.tdcc)
            if rate is None:
                continue
            usd = h.amount * rate
            g["usd"]["all"] += usd
            g["usd"][r.category] += usd
            if r.category == DELIVER:
                g["usd"]["loss"] += usd * r.loss_ratio
            if r.category == WATCH and r.worst is not None:
                assert r.worst.close is not None and r.worst.strike is not None
                g["usd"]["watch_loss"] += usd * (1 - r.worst.close / r.worst.strike)
    rows = []
    for name, g in groups.items():
        n, usd = g["tdccs"], g["usd"]
        rows.append(
            (
                name,
                len(n[DELIVER]),
                usd[DELIVER],
                usd["loss"],
                len(n[WATCH]),
                usd[WATCH],
                usd["watch_loss"],
                len(n[MANUAL]),
                len(n["all"]),
                usd["all"],
                "、".join(sorted(g["other"])),
            )
        )
    rows.sort(key=lambda row: (-row[3], -row[2], -row[5], str(row[0])))
    return rows


def _detail(results: Sequence[Assessed], chosen: Collection[str]) -> list[tuple[object, ...]]:
    rows = []
    for r in results:
        if r.category not in NOTIFY:
            continue
        p, w = r.position, r.worst
        for h in p.holdings:
            loss = h.amount * r.loss_ratio
            rows.append(
                (
                    p.final_date,
                    r.category,
                    h.branch,
                    h.fc,
                    p.tdcc,
                    p.product,
                    p.issuer,
                    p.currency,
                    p.ki_text,
                    w and w.code,
                    w and _pct(w.performance),
                    _worst_chosen(w, chosen),
                    w and w.close,
                    w and w.strike,
                    w and w.ki,
                    h.amount,
                    loss,
                    _usd(loss, p.usd_rate),
                    r.reason,
                )
            )
    return rows


def _all_row(r: Assessed, chosen: Collection[str]) -> tuple[object, ...]:
    p, w = r.position, r.worst
    legs = " / ".join(f"{leg.code} {'-' if leg.performance is None else f'{leg.performance:+.1%}'}" for leg in p.legs)
    loss = r.notional * r.loss_ratio
    row = (
        p.final_date,
        r.category,
        p.tdcc,
        p.product,
        p.issuer,
        p.currency,
        p.ki_text,
        r.notional,
        _usd(r.notional, p.usd_rate),
        w and w.code,
        w and _pct(w.performance),
        _worst_chosen(w, chosen),
        loss,
        _usd(loss, p.usd_rate),
        legs,
        r.reason,
    )
    return row


def _worst_chosen(worst: Leg | None, chosen: Collection[str]) -> str | None:
    """最差標的是不是選定的標的之一（是／否）；算不出最差標的時空白。"""
    return None if worst is None else ("是" if worst.code in chosen else "否")


def _pct(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:+.1%}"


MONEY_WORDS = ("面額", "金額", "估損")  # 表頭有這些字的欄是金額，顯示到整數、千分位


def _style(sheet, *, widths: Sequence[float]) -> None:
    money = {cell.column for cell in sheet[1] if any(word in str(cell.value) for word in MONEY_WORDS)}
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            text_cell(cell)
            if isinstance(cell.value, dt.date):
                cell.number_format = "yyyy/m/d"
            elif cell.column in money and isinstance(cell.value, int | float | Decimal):
                cell.number_format = "#,##0"
    for i, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(i)].width = min(width, MAX_WIDTH + 20)
