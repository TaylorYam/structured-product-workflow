"""比價（#155、ADR 0020）：每檔商品逐日比到收盤價表最新的一天，判定 KO、KI 觸及、到期接股或到期還本。

- 範圍：核對條件「完成」的商品（各標的進場價、執行價、下限價、KO 價已算出或由 TS 回填），已鎖住與未鎖住都比，
  比價狀態記下比價時是否鎖住（畫面標「未鎖住」）。已出場的不比（#169、ADR 0021）：已有存續事件
  （比價算出的 KO、接股、到期還本，人工登記的顧客贖回，舊清單）的商品比價狀態是已出場，事件不再重算；
  先假設收盤價不會事後更正。
- KO：收盤價 ≥ KO 價。非記憶式要同一個觀察日所有標的都達標；記憶式每檔標的在任一觀察日達標就記住，全部記過即 KO。
  D 型的觀察日是第一個比價日（比價日_{Non-Call}）起到最終比價日止、所有標的市場都開市的日子；P 型只看各期比價日。
  Non-Call 等於天期的商品不會提前出場，不觀察 KO。
- KI：EKI 只在最終比價日，收盤價 < 下限價；AKI 自 Strike date 之後、該標的市場的每個交易日；
  MKI（每期觀察）不比，KI 欄寫「MKI (manual)」，KO 照比、比價狀態照 KO 算；最終比價日沒有 KO 時待確認（#196）。
- 比價狀態、已比到、原因只講 KO（#196、ADR 0022）：已比到第一個比價日之前、不觀察 KO 的都空白；KI 另寫一欄
  （沒有 KI 寫 `-`，其他是英文代碼 EKI、AKI、MKI (manual)，觸及時 `AKI hit 日期 標的`，#198），每日觀察 KI 照樣每天比，
  缺它要的價格照樣等股價。
- 到期（最終比價日，沒有 KO）：沒有 KI，或 KI 曾觸及，且最差表現的標的（最終收盤價 ÷ 進場價最低）收盤價 < 執行價
  → 接股（記最差標的）；否則到期還本。事件日一律是觸價或判定的那個觀察日。
- 還在 Non-call 期間（第一個比價日之前）的在庫商品，原因寫「Non-call period（第一個比價日 …）」，每日觀察 KI 已開始比的
  也一樣；Non-Call 等於天期的還沒到最終比價日時寫「還沒到最終比價日（…）」（#194）。
- 缺價格就停住：某標的在它市場的交易日要用到價格、收盤價表沒有，這檔停在那一天（已比到的日期是前一天），
  比價狀態「等股價」寫出標的與日期。交易所不在假日表、日期超出假日表涵蓋範圍、要比價了收盤價表卻完全沒有這個代碼
  → 待確認並寫原因。
- 拆股（#270、ADR 0024）：登記的拆股生效日在 Strike date 之後、不晚於觀察日的，收盤價乘上「拆股後 ÷ 拆股前」股數
  換回交易時的基準再比；商品的四個價格不動。還沒登記的價格斷層（`stock_splits.SplitBook.jumps`）落在 Strike date 之後、
  比價範圍以內時，比到斷層前一天就停下來、待確認並寫出標的、日期與倍數；在那之前算出的出場照樣算。
- 寫入：第一次算出出場事件時經 `lifecycle.replace_auto_events` 寫入（留紀錄）；每檔一筆
  `workflow.pricing_status`，和上一次不同才寫。拆股登記改了之後，受影響、事件全部來自比價的已出場商品
  （`revisit`）忽略原本的事件重新比，結果不同就取代（不再出場就清掉事件，變回在庫）。

`observe` 只算一檔、不碰資料庫；`observe_all` 讀資料庫、算全部、寫回。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from sp_workflow.check_terms import NO_BARRIER as NO_KI
from sp_workflow.lifecycle import (
    DELIVERY,
    KO,
    LEGACY,
    MANUAL,
    MATURITY,
    OBSERVATION,
    AutoEvent,
    LifecycleError,
    StoredEvent,
    current_events_of,
    replace_auto_events,
)
from sp_workflow.product_record import KO_TYPES
from sp_workflow.status import CheckTerms, OrderReview, Pricing
from sp_workflow.stock_splits import Jump, SplitBook, load_splits

if TYPE_CHECKING:
    import psycopg

    from fcn_checker.trading_calendar import Market, TradingCalendar

ON_BOOK = Pricing.ON_BOOK
WAIT_PRICES = Pricing.WAIT_PRICES
TO_CONFIRM = Pricing.TO_CONFIRM
EXITED = Pricing.EXITED
STATUSES = tuple(Pricing)
UNLOCKED = OrderReview.UNLOCKED

MEMORY_TYPES = frozenset(("Daily Memory", "Period End Memory"))
DAILY_TYPES = frozenset(("Daily", "Daily Memory"))
EKI, AKI, MKI = "EKI", "AKI", "MKI"
NO_KI_TEXT = "-"  # 沒有 KI 寫一槓，和有 KI 的代碼一眼分開
MKI_KI = "MKI (manual)"  # MKI 不比（比價日 1～12 不是每一期，ADR 0020），KI 欄提醒人工追蹤


@dataclass(frozen=True)
class Underlying:
    """一個標的的代碼與核對條件算出的四個價格。"""

    seq: int
    code: str
    initial: Decimal | None  # 進場價
    strike: Decimal | None  # 執行價
    ki: Decimal | None  # 下限價
    ko: Decimal | None  # KO 價

    @property
    def name(self) -> str:
        return f"UL_{self.seq} {self.code}"


@dataclass(frozen=True)
class Terms:
    """比價用的商品條件。"""

    tdcc: str
    ko_type: str | None
    barrier_type: str | None
    strike_date: dt.date | None
    final_date: dt.date | None  # 最終比價日
    autocall: tuple[dt.date, ...]  # 資料庫的比價日（空白的不算），由早到晚
    underlyings: tuple[Underlying, ...]
    no_autocall: bool = False  # Non-Call 等於天期：不會提前出場


@dataclass(frozen=True)
class Outcome:
    """一檔的比價結果：比價狀態、原因、已比到的日期（含，只算 KO 觀察）、算出的自動事件與 KI 欄的字。"""

    status: str
    reason: str = ""
    observed_through: dt.date | None = None
    events: tuple[AutoEvent, ...] = ()
    ki: str = ""


def observe(
    terms: Terms,
    closes: Mapping[str, Mapping[dt.date, Decimal]],
    calendar: TradingCalendar,
    through: dt.date | None,
    splits: SplitBook | None = None,
    jumps: Mapping[str, Sequence[Jump]] | None = None,
) -> Outcome:
    """把一檔商品逐日比到 `through`（收盤價表最新的一天）。`closes` 是代碼 → {收盤日: 收盤價}（原始值）。

    `splits` 是拆股登記；`jumps` 是各代碼的價格斷層（`observe_all` 先用 `price_jumps` 算好一次），沒給時這裡算。"""
    problem = _terms_problem(terms, calendar)
    if problem:
        return Outcome(TO_CONFIRM, problem)
    markets = {u.code: calendar.market_of(u.code) for u in terms.underlyings}
    final = terms.final_date
    assert final is not None and markets  # 由 _terms_problem 確認
    daily = terms.ko_type in DAILY_TYPES
    memory = terms.ko_type in MEMORY_TYPES
    ko_days = frozenset() if terms.no_autocall or daily else frozenset(terms.autocall)
    ko_start = terms.autocall[0] if daily and not terms.no_autocall else None
    aki = terms.barrier_type == AKI
    ko_first = None if terms.no_autocall else terms.autocall[0]  # 第一個比價日；之前是 Non-call 期間
    touch: tuple[dt.date, Underlying] | None = None  # KI 第一次觸及的日期與標的

    def result(status: str, reason: str, day: dt.date | None = None) -> Outcome:
        """還沒出場的結果：已比到只算 KO 觀察（第一個比價日之前、不觀察 KO 的都空白，#196）。"""
        shown = day if day is not None and ko_first is not None and day >= ko_first else None
        return Outcome(status, reason, shown, ki=_ki_text(terms, touch))

    first = min(
        day
        for day in (
            terms.strike_date + dt.timedelta(days=1) if aki and terms.strike_date else None,
            ko_start,
            min(ko_days) if ko_days else None,
            final,
        )
        if day is not None
    )
    if through is None:
        return result(ON_BOOK, "收盤價表還沒有資料")
    if through < first:
        return result(ON_BOOK, _waiting(ko_first, final))
    unknown = [u.name for u in terms.underlyings if u.code not in closes]
    if unknown:
        return result(TO_CONFIRM, "；".join(f"{name}：收盤價表沒有這個代碼" for name in unknown))
    last = min(final, through)
    for code, market in markets.items():
        assert market is not None
        if not market.covers(first, last):
            return result(
                TO_CONFIRM,
                f"{_name(terms, code)}：{first}～{last} 超出{market.name}假日表的涵蓋範圍"
                f"（{market.covered_from}～{market.covered_through}）",
            )

    book = splits or SplitBook()
    base = terms.strike_date or first - dt.timedelta(days=1)  # 進場價的基準日
    if jumps is None:
        jumps = price_jumps(book, calendar, {u.code: closes[u.code] for u in terms.underlyings})
    jump = _first_jump(terms, jumps, base, last)
    end = last if jump is None else min(last, jump.day - dt.timedelta(days=1))

    remembered: set[str] = set()
    day = first
    while day <= end:
        open_ = {code for code, market in markets.items() if _open(market, day)}
        is_final = day == final
        ko_day = (ko_start is not None and day >= ko_start and len(open_) == len(markets)) or day in ko_days
        needed = set(markets) if ko_day or is_final else set()
        if aki and terms.strike_date is not None and day > terms.strike_date:
            needed |= open_
        closed = sorted(needed - open_)
        if closed:
            what = "最終比價日" if is_final else "比價日"
            return result(
                TO_CONFIRM,
                f"{what} {day} 是 {'、'.join(_name(terms, c) for c in closed)} 市場的休市日",
                _before(day, first),
            )
        prices: dict[str, Decimal] = {}
        for u in terms.underlyings:
            if u.code not in needed:
                continue
            price = closes.get(u.code, {}).get(day)
            if price is None:
                for_ki = "" if ko_day or is_final else "（每日觀察 KI）"
                return result(WAIT_PRICES, f"{u.name}：{day} 沒有收盤價{for_ki}", _before(day, first))
            prices[u.code] = price * book.factor(u.code, base, day)
        if aki and touch is None:
            hit_ki = [u for u in terms.underlyings if u.code in prices and u.ki is not None and prices[u.code] < u.ki]
            if hit_ki:
                touch = (day, hit_ki[0])
        if ko_day:
            hit = {u.code for u in terms.underlyings if u.ko is not None and prices[u.code] >= u.ko}
            remembered |= hit
            if (memory and remembered == set(markets)) or (not memory and hit == set(markets)):
                return Outcome(EXITED, f"KO {day}", day, (AutoEvent(KO, day),), _ki_text(terms, touch))
        if is_final:
            return _maturity(terms, prices, touch, day)
        day += dt.timedelta(days=1)
    if jump is not None:
        return result(TO_CONFIRM, _jump_reason(terms, jump), _before(jump.day, first))
    reason = _waiting(ko_first, final) if ko_first is None or last < ko_first else ""
    return result(ON_BOOK, reason, last)


def price_jumps(
    book: SplitBook, calendar: TradingCalendar, closes: Mapping[str, Mapping[dt.date, Decimal]]
) -> dict[str, tuple[Jump, ...]]:
    """各代碼的價格斷層，只看該市場的交易日（休市日的收盤價不算）；交易所不在假日表的代碼全部看。"""
    found = {}
    for code, series in closes.items():
        market = calendar.market_of(code)
        days = series if market is None else {d: p for d, p in series.items() if _open(market, d)}
        found[code] = book.jumps(code, days)
    return found


def _first_jump(terms: Terms, jumps: Mapping[str, Sequence[Jump]], base: dt.date, last: dt.date) -> Jump | None:
    """基準日之後、比到的最後一天以前，各標的最早的價格斷層。"""
    found = [j for u in terms.underlyings for j in jumps.get(u.code, ()) if base < j.day <= last]
    return min(found, key=lambda j: (j.day, _seq(terms, j.code)), default=None)


def _jump_reason(terms: Terms, jump: Jump) -> str:
    return (
        f"{_name(terms, jump.code)}：{jump.day} 收盤價是 {jump.before} 的 {jump.ratio:.3g} 倍，疑似拆股或收盤價有誤"
        "（拆股請到「維護 → 拆股登記…」登記，價格錯了請重匯）"
    )


def _waiting(ko_first: dt.date | None, final: dt.date) -> str:
    """還沒到第一個比價日的在庫原因：Non-call 期間；Non-Call 等於天期的沒有比價日可等，等的是最終比價日。"""
    if ko_first is None:
        return f"還沒到最終比價日（{final}）"
    return f"Non-call period（第一個比價日 {ko_first}）"


def _ki_text(terms: Terms, touch: tuple[dt.date, Underlying] | None = None) -> str:
    """進度頁 KI 欄（#196）：KI 種類的英文代碼（#198），觸及時加第一次觸及的日期與標的。"""
    if terms.barrier_type == NO_KI:
        return NO_KI_TEXT
    if terms.barrier_type == MKI:
        return MKI_KI
    if touch is not None:
        return f"{terms.barrier_type} hit {touch[0]} {touch[1].name}"
    return terms.barrier_type


def _maturity(
    terms: Terms, prices: Mapping[str, Decimal], touch: tuple[dt.date, Underlying] | None, day: dt.date
) -> Outcome:
    """最終比價日沒有 KO：依 KI 與最差表現判定接股或到期還本。"""
    if terms.barrier_type == MKI:
        return Outcome(TO_CONFIRM, f"每期觀察 KI，最終比價日 {day} 沒有 KO，要人工判定接股或到期還本", day, ki=MKI_KI)
    if terms.barrier_type == EKI:
        hit_ki = [u for u in terms.underlyings if u.ki is not None and prices[u.code] < u.ki]
        touch = (day, hit_ki[0]) if hit_ki else None
    ki = _ki_text(terms, touch)
    worst = min(terms.underlyings, key=lambda u: (prices[u.code] / u.initial, u.seq))  # type: ignore[operator]
    knocked = terms.barrier_type == NO_KI or touch is not None
    assert worst.strike is not None
    if knocked and prices[worst.code] < worst.strike:
        return Outcome(EXITED, f"接股 {worst.code} {day}", day, (AutoEvent(DELIVERY, day, worst.code),), ki)
    return Outcome(EXITED, f"到期還本 {day}", day, (AutoEvent(MATURITY, day),), ki)


def _terms_problem(terms: Terms, calendar: TradingCalendar) -> str:
    """比價前先確認條件齊全；有問題時回傳待確認的原因。"""
    if not terms.underlyings:
        return "核對條件沒有標的價格"
    if terms.ko_type not in KO_TYPES:
        return f"KO Type「{terms.ko_type or ''}」不認得"
    if terms.barrier_type not in (NO_KI, EKI, AKI, MKI):
        return f"Barrier Type「{terms.barrier_type or ''}」不認得"
    if terms.final_date is None:
        return "沒有最終比價日"
    if not terms.autocall:
        return "沒有比價日"
    if terms.autocall[-1] != terms.final_date:
        return f"最後一個比價日 {terms.autocall[-1]} 和最終比價日 {terms.final_date} 不同"
    if terms.barrier_type == AKI and terms.strike_date is None:
        return "每日觀察 KI 要有 Strike date"
    problems = []
    for u in terms.underlyings:
        wanted = {"進場價": u.initial, "執行價": u.strike}
        if not terms.no_autocall:
            wanted["KO 價"] = u.ko
        if terms.barrier_type in (EKI, AKI):
            wanted["下限價"] = u.ki
        missing = [name for name, value in wanted.items() if value is None or (name == "進場價" and value <= 0)]
        if missing:
            problems.append(f"{u.name} 缺{'、'.join(missing)}")
        elif calendar.market_of(u.code) is None:
            problems.append(f"{u.name}：交易所不在假日表")
    return "；".join(problems)


def _open(market: Market, day: dt.date) -> bool:
    return day.weekday() < 5 and day not in market.holidays


def _before(day: dt.date, first: dt.date) -> dt.date | None:
    return None if day <= first else day - dt.timedelta(days=1)


def _name(terms: Terms, code: str) -> str:
    return next(u.name for u in terms.underlyings if u.code == code)


def _seq(terms: Terms, code: str) -> int:
    return next(u.seq for u in terms.underlyings if u.code == code)


# ---------- 資料庫 ----------


@dataclass(frozen=True)
class Observed:
    """一檔比價後的狀態（給完成訊息看）。"""

    tdcc: str
    status: str
    reason: str
    observed_through: dt.date | None
    locked: bool
    ki: str = ""  # 進度頁 KI 欄（#196）


@dataclass(frozen=True)
class EventChange:
    """這次比價改了的自動事件：[事件, 事件日, 標的]，依事件日排序。"""

    tdcc: str
    previous: tuple[tuple[str, str, str | None], ...]
    current: tuple[tuple[str, str, str | None], ...]


@dataclass(frozen=True)
class ObservationReceipt:
    """一次比價的結果：比到哪一天、每檔的比價狀態、這次改了的自動事件。"""

    through: dt.date | None  # 收盤價表最新的一天
    results: tuple[Observed, ...] = ()
    changes: tuple[EventChange, ...] = ()
    written: int = 0  # 比價狀態有變、寫了的檔數
    errors: tuple[tuple[str, str], ...] = ()  # (TDCC, 原因)：自動事件寫不進去的

    def count(self, status: str) -> int:
        return sum(r.status == status for r in self.results)

    def unfinished(self) -> tuple[Observed, ...]:
        """還沒完成的比價狀態：等股價、待確認。"""
        return tuple(r for r in self.results if r.status in (WAIT_PRICES, TO_CONFIRM))


def observe_all(
    conn: psycopg.Connection, calendar: TradingCalendar, *, user: str | None = None, revisit: Collection[str] = ()
) -> ObservationReceipt:
    """比價全部：核對條件完成、還沒出場的商品逐日比到收盤價表最新的一天，寫出場事件與比價狀態（有變才寫）。

    已出場的不比，比價狀態是已出場；例外是 `revisit`（拆股登記改了、受影響的商品，`stock_splits.affected_exits`）
    裡事件全部來自比價的，忽略原本的事件重新比。核對條件不再是完成、但以前比過的商品不比，
    還沒出場的比價狀態改成待確認（事件不動）。
    """
    with conn.transaction():
        (through,) = conn.execute("SELECT max(close_date) FROM workflow.close_price").fetchone()
        terms, locked, not_done = _load_terms(conn)
        closes = _load_closes(conn, terms)
        book = SplitBook(load_splits(conn))
        jumps = price_jumps(book, calendar, closes)
        stored = _stored_status(conn)
        existing = current_events_of(conn, [t.tdcc for t in terms])
        results: list[Observed] = []
        changes: list[EventChange] = []
        errors: list[tuple[str, str]] = []
        for t in terms:
            events = existing.get(t.tdcc, [])
            again = t.tdcc in revisit and all(e.source == OBSERVATION for e in events)
            # 已出場：不再比價（ADR 0020），已比到的日期與 KI 沿用上一次；沒存過 KI 的寫 KI 種類（#198）
            if events and not again:
                before = stored.get(t.tdcc)
                results.append(
                    Observed(
                        t.tdcc,
                        EXITED,
                        _exit_reason(events),
                        before.observed_through if before else None,
                        locked[t.tdcc],
                        before.ki if before and before.ki else _ki_text(t),
                    )
                )
                continue
            outcome = observe(t, closes, calendar, through, book, jumps)
            if outcome.status == EXITED or events:  # 重新比的已出場商品不再出場時清掉事件
                new = outcome.events if outcome.status == EXITED else ()
                previous = tuple(AutoEvent(e.event, e.event_date, e.underlying) for e in events if e.event_date)
                try:
                    if replace_auto_events(conn, t.tdcc, new, user=user):
                        changes.append(
                            EventChange(
                                t.tdcc,
                                tuple(e.key() for e in _sorted(previous)),
                                tuple(e.key() for e in _sorted(new)),
                            )
                        )
                except LifecycleError as exc:  # 例如比價中有人登記了顧客贖回
                    errors.append((t.tdcc, str(exc)))
                    outcome = Outcome(TO_CONFIRM, f"自動事件寫不進去：{exc}", outcome.observed_through, ki=outcome.ki)
            results.append(
                Observed(t.tdcc, outcome.status, outcome.reason, outcome.observed_through, locked[t.tdcc], outcome.ki)
            )
        exited = current_events_of(conn, [tdcc for tdcc in not_done if tdcc in stored])
        for tdcc, (status, reason) in not_done.items():
            if exited.get(tdcc):  # 已出場的商品，核對條件後來不是完成也不影響
                before = stored[tdcc]
                results.append(
                    Observed(
                        tdcc, EXITED, _exit_reason(exited[tdcc]), before.observed_through, locked[tdcc], before.ki or ""
                    )
                )
            elif tdcc in stored:
                text = f"核對條件{status}，這次沒有比價" + (f"：{reason}" if reason else "")
                before = stored[tdcc]
                results.append(Observed(tdcc, TO_CONFIRM, text, before.observed_through, locked[tdcc], before.ki or ""))
        written = 0
        for r in results:
            if stored.get(r.tdcc) != StoredStatus(
                r.status, r.reason or None, r.observed_through, r.locked, r.ki or None
            ):
                _write_status(conn, r)
                written += 1
    return ObservationReceipt(through, tuple(results), tuple(changes), written, tuple(errors))


def _exit_reason(events: Sequence[StoredEvent]) -> str:
    """已出場的比價原因：顧客贖回、舊清單寫出來源；比價事件和當初算出時一樣（例：KO 2026-11-18）。"""
    manual = [e for e in events if e.source == MANUAL]
    if manual:
        e = manual[-1]
        return f"{e.event} {e.event_date or ''}".strip()
    legacy = [e for e in events if e.source == LEGACY]
    if legacy:
        return f"舊清單：{_events_text(legacy)}"
    return _events_text(events)


def _sorted(events: Sequence[AutoEvent]) -> list[AutoEvent]:
    return sorted(events, key=lambda e: (e.event_date, e.event, e.underlying or ""))


def _events_text(events: Sequence[StoredEvent]) -> str:
    return "、".join(
        " ".join(str(part) for part in (e.event, e.underlying, e.event_date or "沒有事件日") if part) for e in events
    )


def _load_terms(
    conn: psycopg.Connection,
) -> tuple[list[Terms], dict[str, bool], dict[str, tuple[str, str | None]]]:
    """核對條件完成的商品的比價條件、每檔是否鎖住，以及核對條件不是完成的 {TDCC: (狀態, 原因)}。

    商品條件還是自己讀；要改讀法時先看能不能用 `product_terms.read_many`（#204）。
    """
    rows = conn.execute(  # 鎖住＝`locked_at` 有值（同 `product_terms.locked`）
        "SELECT p.tdcc, p.ko_type, p.barrier_type, p.strike_date, p.final_valuation_date,"
        " p.non_call_months, p.tenor_months, p.locked_at IS NOT NULL, c.status, c.reason"
        " FROM workflow.product p JOIN workflow.check_terms c ON c.tdcc = p.tdcc ORDER BY p.tdcc"
    ).fetchall()
    locked = {row[0]: row[7] for row in rows}
    not_done = {row[0]: (row[8], row[9]) for row in rows if row[8] != CheckTerms.DONE}
    done = [row for row in rows if row[8] == CheckTerms.DONE]
    tdccs = [row[0] for row in done]
    underlyings: dict[str, list[Underlying]] = {}
    for tdcc, seq, code, initial, strike, ki, ko in conn.execute(
        "SELECT tdcc, seq, code, initial_price, strike_price, ki_price, ko_price FROM workflow.underlying_price"
        " WHERE tdcc = ANY(%s) ORDER BY tdcc, seq",
        (tdccs,),
    ):
        underlyings.setdefault(tdcc, []).append(Underlying(seq, code, initial, strike, ki, ko))
    autocall: dict[str, list[dt.date]] = {}
    for tdcc, value in conn.execute(
        "SELECT tdcc, value FROM workflow.autocall_date WHERE tdcc = ANY(%s) AND value IS NOT NULL ORDER BY tdcc, value",
        (tdccs,),
    ):
        autocall.setdefault(tdcc, []).append(value)
    terms = [
        Terms(
            tdcc,
            ko_type,
            barrier,
            strike_date,
            final,
            tuple(autocall.get(tdcc, ())),
            tuple(underlyings.get(tdcc, ())),
            non_call is not None and tenor is not None and non_call >= tenor,
        )
        for tdcc, ko_type, barrier, strike_date, final, non_call, tenor, *_ in done
    ]
    return terms, locked, not_done


def _load_closes(conn: psycopg.Connection, terms: Collection[Terms]) -> dict[str, dict[dt.date, Decimal]]:
    """比價會用到的代碼的收盤價（從最早的 Strike date 起）。"""
    codes = sorted({u.code for t in terms for u in t.underlyings})
    starts = [d for t in terms for d in (t.strike_date, *t.autocall[:1]) if d is not None]
    closes: dict[str, dict[dt.date, Decimal]] = {}
    if not codes or not starts:
        return closes
    for code, day, price in conn.execute(
        "SELECT code, close_date, close FROM workflow.close_price WHERE code = ANY(%s) AND close_date >= %s",
        (codes, min(starts)),
    ):
        closes.setdefault(code, {})[day] = price
    return closes


@dataclass(frozen=True)
class StoredStatus:
    """`workflow.pricing_status` 裡一檔上一次的比價狀態（#209：欄位命名，不用索引取值）。"""

    status: str
    reason: str | None
    observed_through: dt.date | None
    locked: bool
    ki: str | None


def _stored_status(conn: psycopg.Connection) -> dict[str, StoredStatus]:
    return {
        tdcc: StoredStatus(*row)
        for tdcc, *row in conn.execute(
            "SELECT tdcc, status, reason, observed_through, locked, ki FROM workflow.pricing_status"
        )
    }


def _write_status(conn: psycopg.Connection, r: Observed) -> None:
    conn.execute(
        "INSERT INTO workflow.pricing_status (tdcc, status, reason, observed_through, locked, ki)"
        " VALUES (%s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (tdcc) DO UPDATE SET status = EXCLUDED.status, reason = EXCLUDED.reason,"
        " observed_through = EXCLUDED.observed_through, locked = EXCLUDED.locked, ki = EXCLUDED.ki,"
        " calculated_at = now()",
        (r.tdcc, r.status, r.reason or None, r.observed_through, r.locked, r.ki or None),
    )
