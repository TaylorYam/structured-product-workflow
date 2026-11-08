"""拆股行事曆（#509、ADR 0031）：按「匯入自動拆股登記」時從公開的拆股行事曆抓已公布的拆股，自動登記，生效日再對齊收盤價斷層。

- 來源（`fetch`）：Yahoo Finance 拆股行事曆（美、日、港股，提前約 1～2 個月）與 Nasdaq 拆股行事曆（只有美股，含 ETF）。
  兩個都不是官方 API、免 key，只用標準函式庫；抓不到的來源只寫原因（`Fetched.errors`），不影響匯入。
- 登記（`register`）：只收收盤價表或商品有的代碼（代碼對照見 `source_key`）；比例異常的、同一個代碼生效日前後
  `NEARBY_DAYS` 天內已經有登記的不收；同一個代碼兩個來源的日期或比例不同時不登記、列在結果裡給作業人員判斷。
  寫入走 `stock_splits.add_split`（一筆一個 transaction、留紀錄），備註以 `AUTO_NOTE` 開頭。
- 對齊（`align`）：行事曆的日期不一定是收盤價表第一次出現新股數價格的那天（日股常晚一個營業日）。
  生效日在最近 `ALIGN_DAYS` 天內的自動登記，生效日當天沒有對應的價格斷層、但前後 `ALIGN_TRADING_DAYS` 個收盤日內
  恰好有一個套用這次拆股就消失的斷層時，把生效日改到那一天（刪掉重登在同一個 transaction，各留紀錄）。人工登記的不動。
畫面是「1 每日匯入」頁的「匯入自動拆股登記」按鈕（`sp_workflow.tabs.daily`），跟每日匯入分開；動作在 `window_actions.import_split_calendar`。
"""

from __future__ import annotations

import datetime as dt
import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

from sp_workflow.stock_splits import (
    Split,
    SplitBook,
    StockSplitError,
    add_split,
    affected_exits,
    delete_split,
    in_range,
    load_splits,
)

if TYPE_CHECKING:
    import psycopg

    from fcn_checker.trading_calendar import TradingCalendar

YAHOO, NASDAQ = "Yahoo", "Nasdaq"
SOURCES = (YAHOO, NASDAQ)
AUTO_NOTE = "自動登記"
DAYS_BEFORE, DAYS_AFTER = 10, 120  # 抓行事曆的範圍：今天前後幾天（前面幾天是補抓前幾天沒匯入時漏掉的）
MAX_RATIO = 1000  # 前後股數比例超過這個不收：多半是現金收購少數股東（例：3124872 合 1）
NEARBY_DAYS = 7  # 同一個代碼生效日前後幾天內已經有登記就不再登記
ALIGN_DAYS = 30  # 生效日在最近幾天內的自動登記才對齊
ALIGN_TRADING_DAYS = 3  # 對齊時找生效日前後幾個收盤日內的斷層
CLOSE_WINDOW_DAYS = 15  # 對齊時讀生效日前後幾天的收盤價（涵蓋前後 3 個收盤日與連假）
TIMEOUT = 20  # 秒
PAGE = 100
MAX_PAGES = 30
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


@dataclass(frozen=True)
class Announced:
    """行事曆上的一筆拆股：`key` 是 (市場, 代號)，見 `source_key`。"""

    key: tuple[str, str]
    effective_date: dt.date
    shares_before: int
    shares_after: int
    source: str


@dataclass(frozen=True)
class Fetched:
    """這次抓到的行事曆；抓不到的來源只有原因。"""

    fetched_on: dt.date
    announced: tuple[Announced, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class Skipped:
    """沒有自動登記、要作業人員看一下的：代碼與原因。"""

    code: str
    reason: str


@dataclass(frozen=True)
class Moved:
    """對齊價格斷層：原本的登記改到新的生效日。"""

    before: Split
    after: Split


@dataclass(frozen=True)
class CalendarReceipt:
    """匯入自動拆股登記的結果；`revisit` 是要重新比價的已出場商品（`stock_splits.affected_exits`）。"""

    registered: tuple[Split, ...] = ()
    moved: tuple[Moved, ...] = ()
    skipped: tuple[Skipped, ...] = ()
    errors: tuple[str, ...] = ()
    revisit: tuple[str, ...] = ()

    def plus(self, other: CalendarReceipt) -> CalendarReceipt:
        """兩份結果合成一份（登記在前、對齊在後）。"""
        return CalendarReceipt(
            self.registered + other.registered,
            self.moved + other.moved,
            self.skipped + other.skipped,
            self.errors + other.errors,
            tuple(dict.fromkeys(self.revisit + other.revisit)),
        )


# ---------- 抓行事曆（網路） ----------

Opener = Callable[[urllib.request.Request], bytes]


def fetch(today: dt.date, opener: Opener | None = None) -> Fetched:
    """抓 Yahoo 與 Nasdaq 的拆股行事曆（今天前 `DAYS_BEFORE` 天到後 `DAYS_AFTER` 天）；任一個抓不到只寫原因。"""
    read = opener or _browser()
    announced: list[Announced] = []
    errors: list[str] = []
    for name, source in ((YAHOO, _yahoo), (NASDAQ, _nasdaq)):
        try:
            announced += source(read, today)
        except Exception as exc:  # 網路、格式改版都一樣：這個來源這次沒抓到
            errors.append(f"{name} 拆股行事曆沒抓到（{type(exc).__name__}：{_first_line(exc)}）")
    return Fetched(today, tuple(announced), tuple(errors))


def _browser() -> Opener:
    """帶 cookie 的連線（Yahoo 要先拿 cookie 才拿得到 crumb）。"""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def read(request: urllib.request.Request) -> bytes:
        request.add_header("User-Agent", USER_AGENT)
        with opener.open(request, timeout=TIMEOUT) as response:
            return response.read()

    return read


def _yahoo(read: Opener, today: dt.date) -> list[Announced]:
    try:
        read(urllib.request.Request("https://fc.yahoo.com"))  # 只為了拿 cookie；回 404 也有
    except urllib.error.HTTPError:
        pass
    crumb = read(urllib.request.Request("https://query2.finance.yahoo.com/v1/test/getcrumb")).decode().strip()
    if not crumb or "<" in crumb:
        raise ValueError("拿不到 crumb")
    rows: list[dict[str, object]] = []
    for page in range(MAX_PAGES):
        body = {
            "sortType": "ASC",
            "entityIdType": "splits",
            "sortField": "startdatetime",
            "includeFields": ["ticker", "startdatetime", "old_share_worth", "share_worth"],
            "query": {
                "operator": "and",
                "operands": [
                    {"operator": "gte", "operands": ["startdatetime", str(today - dt.timedelta(days=DAYS_BEFORE))]},
                    {"operator": "lte", "operands": ["startdatetime", str(today + dt.timedelta(days=DAYS_AFTER))]},
                ],
            },
            "offset": page * PAGE,
            "size": PAGE,
        }
        request = urllib.request.Request(
            "https://query1.finance.yahoo.com/v1/finance/visualization?lang=en-US&region=US&crumb="
            + urllib.parse.quote(crumb, safe=""),
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        found = yahoo_rows(json.loads(read(request)))
        rows += found
        if len(found) < PAGE:
            break
    return parse_yahoo(rows)


def _nasdaq(read: Opener, today: dt.date) -> list[Announced]:
    request = urllib.request.Request(
        "https://api.nasdaq.com/api/calendar/splits", headers={"Accept": "application/json"}
    )
    data = json.loads(read(request))
    return parse_nasdaq(((data.get("data") or {}).get("rows")) or [])


# ---------- 解析（不需網路） ----------


def yahoo_rows(data: Mapping[str, object]) -> list[dict[str, object]]:
    """Yahoo 回應的一頁：欄名 → 值。"""
    (document,) = data["finance"]["result"][0]["documents"]  # type: ignore[index]
    names = [column["id"] for column in document["columns"]]
    return [dict(zip(names, row, strict=False)) for row in document["rows"]]


def parse_yahoo(rows: Iterable[Mapping[str, object]]) -> list[Announced]:
    """Yahoo 的列；美、日、港股以外（例：.KS、.F）與讀不懂的略過。`old_share_worth` 是拆股前、`share_worth` 是拆股後。"""
    found = []
    for row in rows:
        key = _yahoo_key(str(row.get("ticker") or ""))
        day = _iso_day(row.get("startdatetime"))
        before, after = _whole(row.get("old_share_worth")), _whole(row.get("share_worth"))
        if key and day and before is not None and after is not None:
            found.append(Announced(key, day, before, after, YAHOO))
    return found


def parse_nasdaq(rows: Iterable[Mapping[str, object]]) -> list[Announced]:
    """Nasdaq 的列（只有美股）：比例寫「拆股後:拆股前」（例：8:1 是 1 拆 8），日期是 月/日/年。"""
    found = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        after_text, _, before_text = str(row.get("ratio") or "").partition(":")
        before, after = _whole(before_text), _whole(after_text)
        try:
            day = dt.datetime.strptime(str(row.get("executionDate") or "").strip(), "%m/%d/%Y").date()
        except ValueError:
            continue
        if symbol and before is not None and after is not None:
            found.append(Announced(("US", symbol.replace(".", "/").replace("-", "/")), day, before, after, NASDAQ))
    return found


def _yahoo_key(ticker: str) -> tuple[str, str] | None:
    ticker = ticker.strip().upper()
    symbol, dot, suffix = ticker.rpartition(".")
    if not dot:
        return ("US", ticker.replace("-", "/")) if ticker else None
    if suffix == "T":
        return ("JP", symbol)
    if suffix == "HK" and symbol.isdigit():
        return ("HK", str(int(symbol)))
    return None  # 其他交易所


def source_key(code: str, calendar: TradingCalendar) -> tuple[str, str] | None:
    """收盤價表的代碼（例：SNDK UW、6861 JT、700 HK）對到行事曆的 (市場, 代號)；不是美、日、港股的是 None。"""
    market = calendar.market_of(code)
    symbol = code.split()[0].upper() if code.split() else ""
    if market is None or not symbol:
        return None
    if market.code == "HK":
        return ("HK", str(int(symbol))) if symbol.isdigit() else None
    if market.code in ("US", "JP"):
        return (market.code, symbol)
    return None


def _iso_day(value: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _whole(value: object) -> int | None:
    """正整數（行事曆偶爾寫 2.0）；不是就 None。"""
    try:
        number = Decimal(str(value).strip())
    except ArithmeticError:
        return None
    if number <= 0 or number != number.to_integral_value():
        return None
    return int(number)


# ---------- 登記 ----------


def candidates(
    fetched: Fetched, codes: Mapping[str, tuple[str, str]], existing: Sequence[Split]
) -> tuple[list[Split], list[Skipped]]:
    """要自動登記的拆股與略過的：`codes` 是收盤價表、商品有的代碼 → (市場, 代號)。

    同一個代碼前後 `NEARBY_DAYS` 天內已經有登記的、比例異常的（前後相同、超過 `MAX_RATIO` 倍）直接不收、不列出
    （每天都會再抓到）；只有兩個來源不一致的列出來給作業人員判斷。"""
    by_key: dict[tuple[str, str], list[Announced]] = {}
    for item in fetched.announced:
        by_key.setdefault(item.key, []).append(item)
    adds: list[Split] = []
    skipped: list[Skipped] = []
    for code in sorted(codes):
        found = _distinct(by_key.get(codes[code], ()))
        for group in _clusters(found):
            first = group[0]
            if _registered_near(existing, code, first.effective_date):
                continue
            if len({(a.effective_date, a.shares_before, a.shares_after) for a in group}) > 1:
                listed = "、".join(f"{a.source} {a.effective_date} {_ratio(a)}" for a in group)
                skipped.append(Skipped(code, f"行事曆不一致（{listed}），請人工確認後登記"))
                continue
            if first.shares_before == first.shares_after or _extreme_ratio(first):
                continue
            sources = "、".join(dict.fromkeys(a.source for a in group))
            note = f"{AUTO_NOTE}：{sources} 行事曆（{fetched.fetched_on} 抓取）"
            adds.append(Split(code, first.effective_date, first.shares_before, first.shares_after, note))
    return adds, skipped


def _distinct(items: Iterable[Announced]) -> list[Announced]:
    """同一個來源重複列出的同一筆只留一筆；依生效日、來源（Yahoo 先）排序。"""
    return sorted(dict.fromkeys(items), key=lambda a: (a.effective_date, SOURCES.index(a.source)))


def _clusters(items: Sequence[Announced]) -> list[list[Announced]]:
    """生效日相距 `NEARBY_DAYS` 天以內的算同一次拆股（不同來源可能差一兩天）。"""
    groups: list[list[Announced]] = []
    for item in items:
        if groups and (item.effective_date - groups[-1][-1].effective_date).days <= NEARBY_DAYS:
            groups[-1].append(item)
        else:
            groups.append([item])
    return groups


def _registered_near(existing: Sequence[Split], code: str, day: dt.date) -> bool:
    return any(s.code == code and abs((s.effective_date - day).days) <= NEARBY_DAYS for s in existing)


def _extreme_ratio(item: Announced) -> bool:
    return max(item.shares_before, item.shares_after) > MAX_RATIO * min(item.shares_before, item.shares_after)


def _ratio(item: Announced) -> str:
    verb = "拆" if item.shares_after >= item.shares_before else "合"
    return f"{item.shares_before} {verb} {item.shares_after}"


def known_codes(conn: psycopg.Connection, calendar: TradingCalendar) -> dict[str, tuple[str, str]]:
    """收盤價表或商品有的代碼 → 行事曆的 (市場, 代號)（同 `add_split` 收的代碼）。"""
    rows = conn.execute(
        "SELECT code FROM workflow.close_price UNION SELECT code FROM workflow.underlying_price WHERE code IS NOT NULL"
    ).fetchall()
    found = {}
    for (code,) in rows:
        key = source_key(code, calendar)
        if key:
            found[code] = key
    return found


def register(
    conn: psycopg.Connection, fetched: Fetched, calendar: TradingCalendar, *, user: str | None = None
) -> CalendarReceipt:
    """登記行事曆上、收盤價表或商品有的代碼的拆股；寫不進去的那一筆列在略過裡，其他照寫。"""
    adds, skipped = candidates(fetched, known_codes(conn, calendar), load_splits(conn))
    registered: list[Split] = []
    revisit: list[str] = []
    for split in adds:
        try:
            add_split(conn, split, user=user)
        except StockSplitError as exc:
            skipped.append(Skipped(split.code, str(exc)))
            continue
        registered.append(split)
        revisit += affected_exits(conn, split)
    return CalendarReceipt(tuple(registered), (), tuple(skipped), fetched.errors, tuple(dict.fromkeys(revisit)))


# ---------- 對齊價格斷層 ----------


def gap_day(split: Split, others: SplitBook, closes: Mapping[dt.date, Decimal]) -> dt.date | None:
    """自動登記的生效日要改到的那天（價格斷層的日子）；不用改或看不出來是 None。

    `others` 是這個代碼其他的登記；`closes` 是生效日前後的收盤價。生效日（不是收盤日時是之後第一個收盤日）
    前後 `ALIGN_TRADING_DAYS` 個收盤日內，套用這次拆股就消失的斷層恰好一個、又不在那個收盤日，才改到斷層那天。"""
    days = sorted(closes)
    position = sum(d < split.effective_date for d in days)  # 生效日之後第一個收盤日在 days 裡的位置
    matching = [
        j.day
        for j in others.jumps(split.code, closes)
        if in_range(j.ratio * split.factor) and abs(days.index(j.day) - position) <= ALIGN_TRADING_DAYS
    ]
    if len(matching) != 1 or (position < len(days) and days[position] == matching[0]):
        return None
    return matching[0]


def align(conn: psycopg.Connection, today: dt.date, *, user: str | None = None) -> CalendarReceipt:
    """生效日在最近 `ALIGN_DAYS` 天內的自動登記，對齊收盤價斷層（刪掉重登，各留紀錄）。"""
    splits = load_splits(conn)
    moved: list[Moved] = []
    skipped: list[Skipped] = []
    revisit: list[str] = []
    for split in splits:
        if (
            not (split.note or "").startswith(AUTO_NOTE)
            or not today - dt.timedelta(days=ALIGN_DAYS) <= split.effective_date <= today
        ):
            continue
        others = SplitBook(s for s in splits if s.code == split.code and s != split)
        day = gap_day(split, others, _closes_around(conn, split.code, split.effective_date))
        if day is None:
            continue
        after = replace(
            split, effective_date=day, note=f"{split.note}；依收盤價斷層從 {split.effective_date} 改到 {day}"
        )
        try:
            with conn.transaction():  # 刪掉、重登一起成功或一起不做
                delete_split(conn, split, user=user)
                add_split(conn, after, user=user)
        except StockSplitError as exc:
            skipped.append(Skipped(split.code, f"生效日想改到 {day} 但沒改成：{exc}"))
            continue
        moved.append(Moved(split, after))
        revisit += affected_exits(conn, split) + affected_exits(conn, after)
    return CalendarReceipt(moved=tuple(moved), skipped=tuple(skipped), revisit=tuple(dict.fromkeys(revisit)))


def _closes_around(conn: psycopg.Connection, code: str, day: dt.date) -> dict[dt.date, Decimal]:
    rows = conn.execute(
        "SELECT close_date, close FROM workflow.close_price WHERE code = %s AND close_date BETWEEN %s AND %s",
        (code, day - dt.timedelta(days=CLOSE_WINDOW_DAYS), day + dt.timedelta(days=CLOSE_WINDOW_DAYS)),
    ).fetchall()
    return dict(rows)


# ---------- 「匯入自動拆股登記」按鈕用 ----------


def register_and_align(
    conn: psycopg.Connection, fetched: Fetched, calendar: TradingCalendar, *, user: str | None = None
) -> CalendarReceipt:
    """先登記行事曆上的新拆股，再對齊最近的自動登記；兩段合成一份結果。"""
    return register(conn, fetched, calendar, user=user).plus(align(conn, fetched.fetched_on, user=user))


def registered_text(split: Split) -> str:
    """一筆新登記：代碼、生效日、比例與來源。"""
    return f"{split.code} {split.effective_date} {split.label}（{_sources(split)}）"


def moved_text(moved: Moved) -> str:
    """一筆改了生效日的。"""
    return f"{moved.before.code} {moved.before.label}：{moved.before.effective_date} → {moved.after.effective_date}"


def summary(receipt: CalendarReceipt) -> str:
    """完成訊息的一段：新登記、改生效日與要人工確認各幾筆，抓不到的來源寫原因。"""
    lines = [
        f"拆股行事曆：新登記 {len(receipt.registered)} 筆、依收盤價斷層改生效日 {len(receipt.moved)} 筆、"
        f"要人工確認 {len(receipt.skipped)} 筆（明細在下面的表）。"
    ]
    lines += [f"拆股行事曆：{e}" for e in receipt.errors]
    return "\n".join(lines)


def _sources(split: Split) -> str:
    """自動登記備註裡的來源（例：Yahoo、Nasdaq）。"""
    return (split.note or "").removeprefix(f"{AUTO_NOTE}：").split(" 行事曆", 1)[0]


def _first_line(exc: BaseException) -> str:
    return str(exc).splitlines()[0] if str(exc) else type(exc).__name__
