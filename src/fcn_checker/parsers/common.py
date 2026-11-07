"""各上手說明書 parser 共用的小工具：數字、推得欄位、文件上的一組值（含原文行）、月配息率出處、沒有 KO 價時的 KO 100%，
以及保留空白的原文合併文字 `RawText`、同一句另一組的 `capture_group`（Nomura、GS），
費用表的 `fee_rates`（各家只宣告座標差異）與不看座標的 `fee_rates_by_pattern`、固定寫法剛好一處的 `exactly_one`（Super Bull）。

MS 與 BNP（之後的上手同樣）的 `read` 與情境分析解析都用這裡的版本，不各自複製一份（Issue #196）。
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from typing import Generic, NamedTuple, TypeVar

from .. import standard_fields
from ..schema import Evidence, FieldStatus, Line, ParsedField
from ..text import compat_standard, squash
from .layout import TextIndex, capture

NUM = r"([\d,]+(?:\.\d+)?)"  # 數字（可有千分位與小數）
SCENARIO_HEAD = re.compile(r"^情境([一二三四五六七八九十]+)[:：](.*)$")  # 情境標題「情境X：標題」（去空白後）
# 說明書沒有自動提前出場價（Non-Call = 天期）：機制仍在、只是不會觸發，KO 視為期初價格的 100%（參考條件表填 100／進場價）
NO_AUTOCALL_KO_PCT = Decimal(100)

T = TypeVar("T")


def dec(s: str) -> Decimal:
    """去掉千分位逗號的 Decimal。"""
    return Decimal(s.replace(",", ""))


def number(s: str) -> int | Decimal:
    """金額：整數值回傳 int，否則 Decimal。"""
    d = dec(s)
    return int(d) if d == d.to_integral_value() else d


def ko_at_initial(prices: dict) -> dict:
    """一列標的價格補上 KO 價 = 期初價格（說明書沒有 KO 價時，見 `NO_AUTOCALL_KO_PCT`）。"""
    out = dict(prices)
    if "ko" not in out and "initial" in out:
        out["ko"] = out["initial"]
    return out


def by_value(hits: Iterable[tuple[T, Sequence[Line]]]) -> dict[T, list[Line]]:
    """重複寫出的值：同一個值的各處合成一筆（依第一次出現的順序），原文行為每一處的行（情境假設值，Issue #294）。"""
    out: dict[T, list[Line]] = {}
    for value, lines in hits:
        out.setdefault(value, []).extend(lines)
    return out


def derived(name: str, src: ParsedField, value=None) -> ParsedField:
    """由另一個欄位推得（證據沿用來源）：來源有問題時沿用來源的狀態與說明。"""
    if not src.ok:
        return ParsedField(name, src.status, None, list(src.evidence), list(src.candidates), src.note)
    return ParsedField(name, FieldStatus.PRESENT, value, list(src.evidence))


class Amount(NamedTuple):
    """金額與旁邊的幣別字（例：累積總配息金額）。"""

    amount: Decimal
    currency: str


@dataclass(frozen=True)
class Hit(Generic[T]):
    """文件上的一個值（或一組具名的值，`typing.NamedTuple`）與原文行。"""

    value: T
    lines: tuple[Line, ...]


def hits(ti: TextIndex, pattern: str, convert: Callable[[re.Match[str]], T]) -> list[Hit[T]]:
    """`pattern` 在 `ti` 的每一處，`convert(match)` 為值。"""
    return [Hit(convert(m), tuple(ti.lines_for(m.start(), m.end()))) for m in ti.finditer(pattern)]


def one(found: list[Hit[T]]) -> Hit[T] | None:
    """剛好一處時回傳那一處；沒有或多處為 None（由規則轉人工覆核）。"""
    return found[0] if len(found) == 1 else None


def exactly_one(
    name: str, ti: TextIndex, pattern: str, missing: str, convert: Callable[[re.Match[str]], object] = lambda m: m[0]
) -> ParsedField:
    """固定寫法（去空白後的句型）剛好一處；值預設為原文去空白，`convert` 可改取其中的數值（Super Bull，workflow #326）。"""
    found = list(ti.finditer(pattern))
    if len(found) != 1:
        return ParsedField.missing(name, missing if not found else f"{missing}（出現多次）")
    m = found[0]
    return ParsedField.present(name, convert(m), ti.lines_for(m.start(), m.end()))


@dataclass(frozen=True)
class Mention:
    """重複寫出的值的一個出處（月配息率、年化報酬率；GS 另有面額、單位數、天期等，Issue #317）；值讀不到時為 None。"""

    where: str
    value: Decimal | int | None
    evidence: tuple[Evidence, ...]

    def parsed(self, name: str) -> ParsedField:
        """當成欄位 `name` 交給規則：讀不到時為缺漏（證據保留出處附近的原文行）。"""
        if self.value is None:
            return ParsedField(name, FieldStatus.MISSING, evidence=list(self.evidence), note=f"找不到{self.where}")
        return ParsedField(name, FieldStatus.PRESENT, self.value, list(self.evidence))

    @classmethod
    def of_field(cls, where: str, pf: ParsedField) -> Mention:
        return cls(where, pf.value if pf.ok else None, tuple(pf.evidence))

    @classmethod
    def of_hit(cls, where: str, value: Decimal, hit: Hit) -> Mention:
        return cls(where, value, tuple(Evidence.of(ln) for ln in hit.lines))

    @classmethod
    def missing(cls, where: str, lines) -> Mention:
        return cls(where, None, tuple(Evidence.of(ln) for ln in lines))

    @classmethod
    def of_optional_hit(cls, where: str, hit: Hit | None, pick: Callable, fallback) -> Mention:
        """`hit` 找不到時為讀不到（證據為 `fallback` 行）；找到時值為 `pick(hit.value)`。"""
        return cls.missing(where, fallback) if hit is None else cls.of_hit(where, pick(hit.value), hit)


# ---------------------------------------------------------------- 原文合併文字（Nomura、GS）


class RawText:
    """多行原文合併（同 `layout.join_text`：英數字跨行補一個空白），並可由字元位置找回原始行（作為證據）。

    名稱、地址、電話等要保留空白的值在這裡讀；數字、日期與句型在去空白的 `TextIndex` 上讀。
    """

    def __init__(self, lines: Sequence[Line]):
        self.lines = list(lines)
        out, self._starts = "", []
        for ln in self.lines:
            t = ln.text.strip()
            if out and t and re.search(r"[A-Za-z0-9.,)]$", out) and re.match(r"[A-Za-z0-9(]", t):
                out += " "
            self._starts.append(len(out))
            out += t
        self.text = out

    def lines_for(self, start: int, end: int) -> list[Line]:
        if not self.lines:
            return []
        i = max(0, bisect.bisect_right(self._starts, start) - 1)
        j = max(i, bisect.bisect_right(self._starts, max(start, end - 1)) - 1)
        return self.lines[i : j + 1]

    def capture(self, name: str, pattern: str, convert: Callable = lambda x: x.strip(), group: int = 1) -> ParsedField:
        """`pattern` 第 `group` 組的每一處（證據為整個比對所在的行）；不同值 → 歧義，沒有 → 缺漏，轉不成值 → 不合法。"""
        found = []
        for m in re.finditer(pattern, self.text, re.S):
            lns = self.lines_for(m.start(), m.end())
            try:
                value = convert(m[group])
            except (ValueError, TypeError, InvalidOperation):
                value = None
            if value is None:
                return ParsedField.invalid(name, lns, f"「{m[group]}」無法辨識")
            found.append((value, lns))
        return ParsedField.from_hits(name, found, missing_note="找不到欄位標籤或已知寫法")


ZH_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def zh_number(s: str) -> int | None:
    """中文數字（一～九十九）或阿拉伯數字 → int（Citi 自動提前出場起始期、UBS 項號「二十六、」）。"""
    if s.isdigit():
        return int(s)
    if s in ZH_DIGITS:
        return ZH_DIGITS[s]
    m = re.fullmatch(r"([一二三四五六七八九]?)十([一二三四五六七八九]?)", s)
    if not m:
        return None
    return (ZH_DIGITS.get(m[1], 1) if m[1] else 1) * 10 + (ZH_DIGITS[m[2]] if m[2] else 0)


def compat_normalized(lines: Sequence[Line]) -> list[Line]:
    """每行的 CJK 相容字轉成標準字（GS 投資人須知、CA 兩種文件）；字數不變，字座標照舊。"""
    return [replace(ln, text=t) if (t := compat_standard(ln.text)) != ln.text else ln for ln in lines]


def capture_group(lns: list[Line], pattern: str, group: int, name: str, convert: Callable = lambda x: x) -> ParsedField:
    """同一句的另一組（例：最低加價申購金額接在最低申購金額後面）。"""
    ti = TextIndex(lns)
    found = []
    for m in ti.finditer(pattern):
        try:
            found.append((convert(m[group]), ti.lines_for(m.start(), m.end())))
        except (ValueError, TypeError, InvalidOperation):
            return ParsedField.invalid(name, ti.lines_for(m.start(), m.end()), f"「{m[group]}」無法辨識")
    return ParsedField.from_hits(name, found, missing_note="找不到欄位標籤或已知寫法")


# ---------------------------------------------------------------- 費用表
#
# 說明書第四章 2.／投資人須知的費用表：每個費用項目一列（左欄項目名稱），費率欄寫「X%~Y%」。各家只差座標（左欄與
# 費率欄怎麼定位、一列從哪裡切到哪裡）與費率格的寫法，讀法只有 `fee_rates` 一份（Issue #486）；不看座標、只靠固定句型的
# 上手（BNP、SG、Citi、JPM）用 `fee_rates_by_pattern`。

FEE_RATE = r"(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)"
FEE_ROWS = ("申購費用", "提前贖回費用", "管理費用", "分銷費用", "保費費用", "解約費用", "其他費用")  # 範本費用表的七列
# 費用表：左欄項目名稱的開頭 → 審查標準 [fees] 的鍵（投資人須知寫「贖回費用」）；None 是表上有、不核對的列
FEE_ITEMS = {
    "申購費用": "申購費用",
    "提前贖回費用": "提前贖回費用",
    "贖回費用": "提前贖回費用",
    "管理費用": None,
    "分銷費": "分銷費用",
    "保費費用": None,
    "解約費用": None,
    "其他費用": None,
}
FEE_CHECKED = ("申購費用", "提前贖回費用", "分銷費用")  # 範本核對的三項


class FeeGrid(NamedTuple):
    """一張費用表的座標：表的行、哪些行在左欄（費用項目名稱）、某一列的費率欄有哪些行（找不到時為說明）。"""

    lines: list[Line]
    is_label: Callable[[Line], bool]
    column: Callable[[Line, str], Callable[[Line], bool] | str]  # (列首行, 費用項目) → 費率欄的行；或找不到的說明


def _row_missing(fee: str) -> str:
    return f"費用表找不到「{fee}」這一列或出現多次"


def rate_header_grid(lines: list[Line]) -> FeeGrid | str:
    """費率欄從表頭「費率（百分比）」的 x 往左 40 起、到下一欄「收取時點」之前 5（分銷費用的子列標籤「報酬／費用／折讓」也在
    這個範圍）；左欄是這個範圍左邊、表頭以下的行（GS、Natixis、CA、DBS、UBS、BBVA）。"""
    head = next((ln for ln in lines if squash(ln.text).startswith("費率")), None)
    if head is None:
        return "找不到費用表表頭「費率（百分比）」"
    # 下一欄表頭：和「費率」那一行垂直重疊（CA 表頭「費率（百分」／「比）」兩行，其他欄置中在兩行之間）
    right = min(
        (
            ln.x0
            for ln in lines
            if ln.page == head.page and ln.y0 < head.y1 and ln.y1 > head.y0 and ln.x0 > head.x0 + 10
        ),
        default=None,
    )
    lo = head.x0 - 40

    def column(top: Line, fee: str):
        if right is None:
            return _row_missing(fee)
        return lambda ln: lo <= ln.x0 < right - 5

    return FeeGrid(lines, lambda ln: ln.x0 < lo and (ln.page, ln.y0) > (head.page, head.y0), column)


def nearest_column_grid(lines: list[Line]) -> FeeGrid | str:
    """費率欄是表頭「費率(百分比)」右邊最近的一欄（各行的 x 和它差 10 以內）；左欄是表頭 x 往左 10 之前的行（Nomura）。"""
    head = next((ln for ln in lines if squash(ln.text) == "費率(百分比)"), None)
    if head is None:
        return "找不到費用表表頭「費率(百分比)」"
    lo = head.x0 - 10
    rate_x = min((ln.x0 for ln in lines if ln.x0 >= lo and (ln.page, ln.y0) > (head.page, head.y0 + 3)), default=None)

    def column(top: Line, fee: str):
        if rate_x is None:
            return _row_missing(fee)
        return lambda ln: abs(ln.x0 - rate_x) <= 10

    return FeeGrid(lines, lambda ln: ln.x0 < lo, column)


def item_header_grid(lines: list[Line]) -> FeeGrid:
    """左欄是 x < 100 的行；費率欄從同一頁表頭「費用項目」的右緣起、到「收取時點」之前 5（沒有「收取時點」時到表頭「費率」
    往右 65）。表頭「費率」可能置中拆成兩行（`費率` / `(百分比)`），比費率格右移 20 pt 以上，不能拿它的 x 當左界（MS，
    Issue #310）。"""

    def column(top: Line, fee: str):
        rate_hdr = [ln for ln in lines if ln.page == top.page and squash(ln.text).startswith("費率")]
        item_hdr = [ln for ln in lines if ln.page == top.page and squash(ln.text) == "費用項目"]
        if len(rate_hdr) != 1 or len(item_hdr) != 1:
            return "找不到費用表「費用項目」或「費率」欄"
        left = item_hdr[0].x1
        right = next(
            (ln.x0 for ln in lines if ln.page == top.page and squash(ln.text) == "收取時點"), rate_hdr[0].x0 + 65
        )
        return lambda ln: left <= ln.x0 < right - 5

    return FeeGrid(lines, lambda ln: ln.x0 < 100, column)


@dataclass(frozen=True)
class FixedColumns:
    """左欄是 x < `edge` 的行，費率欄是 `lo` < x < `hi` 的行（HSBC）。"""

    edge: float
    lo: float
    hi: float

    def __call__(self, lines: list[Line]) -> FeeGrid:
        return FeeGrid(lines, lambda ln: ln.x0 < self.edge, lambda top, fee: lambda ln: self.lo < ln.x0 < self.hi)


def titled_grid(lines: list[Line]) -> FeeGrid | str:
    """表從「投資人應負擔的各項費用」那一行起、到「附註」之前；左欄是表頭「費用項目」左邊起頭、右緣不超過它 80 的行；費率欄在
    「費用項目」右緣與「收取時點」往左 15 之間（Barclays）。"""
    start = next((i for i, ln in enumerate(lines) if ln.text.startswith("投資人應負擔的各項費用")), None)
    if start is None:
        return "第四章找不到費用表"
    seg = lines[start:]
    seg = seg[: next((i for i, ln in enumerate(seg) if ln.text.startswith("附註")), len(seg))]
    item = next((ln for ln in seg if ln.text == "費用項目"), None)
    when = next((ln for ln in seg if ln.text == "收取時點"), None)
    if item is None or when is None:
        return "費用表表頭不完整"
    return FeeGrid(
        seg,
        lambda ln: ln.x1 <= item.x1 + 80 and ln.x0 < item.x0,
        lambda top, fee: lambda ln: item.x1 < ln.x0 < when.x0 - 15,
    )


@dataclass(frozen=True)
class LabelEdge:
    """左欄是 x < `edge` 的行，其他欄都算費率欄（BNP 投資人須知：格內文字擷取順序因樣本而異，不依座標拆欄）。"""

    edge: float

    def __call__(self, lines: list[Line]) -> FeeGrid:
        return FeeGrid(lines, lambda ln: ln.x0 < self.edge, lambda top, fee: lambda ln: ln.x0 >= self.edge)


# 一列的範圍：(表的行, 各列首行, 第幾列, 費率欄的行) → 這一列費率欄的行（依閱讀順序）


def rows_by_position(lines: list[Line], tops: list[Line], k: int, in_column: Callable[[Line], bool]) -> list[Line]:
    """從列首那一行到下一個列首之前（可跨頁；y 容差 3），依頁、y、x 排序（擷取時欄位文字可能交錯）。"""
    top, bottom = tops[k], tops[k + 1] if k + 1 < len(tops) else None
    return sorted(
        (
            ln
            for ln in lines
            if in_column(ln)
            and (ln.page, ln.y0 + 3) >= (top.page, top.y0)
            and (bottom is None or (ln.page, ln.y0 + 3) < (bottom.page, bottom.y0))
        ),
        key=lambda x: (x.page, x.y0, x.x0),
    )


@dataclass(frozen=True)
class SamePage:
    """同一頁、從列首 y 往上 `above` 起到同頁下一個列首 y 往上 `below` 之前；同頁沒有下一列時到列首往下 `tail`
    （None：到頁尾）。行照擷取順序。"""

    above: float
    below: float
    tail: float | None

    def __call__(self, lines: list[Line], tops: list[Line], k: int, in_column: Callable[[Line], bool]) -> list[Line]:
        top = tops[k]
        nexts = [ln.y0 for ln in tops if ln.page == top.page and ln.y0 > top.y0 + 1]
        bottom = min(nexts) if nexts else (None if self.tail is None else top.y0 + self.tail)
        return [
            ln
            for ln in lines
            if ln.page == top.page
            and in_column(ln)
            and top.y0 - self.above <= ln.y0
            and (bottom is None or ln.y0 < bottom - self.below)
        ]


def rows_in_order(lines: list[Line], tops: list[Line], k: int, in_column: Callable[[Line], bool]) -> list[Line]:
    """列首之後、下一個列首之前的行（照擷取順序，不看座標）。"""
    a = next(i for i, ln in enumerate(lines) if ln is tops[k])
    b = next(i for i, ln in enumerate(lines) if ln is tops[k + 1]) if k + 1 < len(tops) else len(lines)
    return [ln for ln in lines[a + 1 : b] if in_column(ln)]


# 費率格的寫法：(欄位名, 費用項目, 列首行, 這一列費率欄的行) → 欄位


@dataclass(frozen=True)
class OneRate:
    """費率欄文字接起來剛好有一個「X%~Y%」（不要求前綴寫法）；`with_label` 時列首那一行在項目名稱之後的字也算（列首和其他欄
    黏成一行，BNP Super Bull 投資人須知）。"""

    pattern: str = FEE_RATE
    note: str = "費用表「{fee}」的費率欄沒有剛好一個「X%~Y%」"
    with_label: bool = False

    def __call__(self, name: str, fee: str, label: str, top: Line, cells: list[Line]) -> ParsedField:
        head = squash(top.text)[len(label) :] if self.with_label else ""
        rates = re.findall(self.pattern, head + "".join(squash(ln.text) for ln in cells))
        if len(rates) == 1:
            return ParsedField.present(name, rates[0], [top, *cells])
        return ParsedField.invalid(name, [top, *cells], self.note.format(fee=fee))


@dataclass(frozen=True)
class PrefixedRate:
    """費率欄文字接起來 = 前綴＋「X%~Y%」（例：「申購價金的0%~3%」；Nomura）。"""

    prefixes: Mapping[str, str]  # 費用項目 → 費率格的前綴

    def __call__(self, name: str, fee: str, label: str, top: Line, cells: list[Line]) -> ParsedField:
        prefix = self.prefixes[fee]
        m = re.fullmatch(re.escape(prefix) + FEE_RATE, "".join(squash(ln.text) for ln in cells))
        if m:
            return ParsedField.present(name, m[1], [top, *cells])
        return ParsedField.invalid(name, [top, *cells], f"費用表「{fee}」的費率不是「{prefix}X%~Y%」")


@dataclass(frozen=True)
class EachRate:
    """費率欄每一處「X%~Y%」都算，值都相同才成立（不同 → 歧義）；證據 `row` 為整列（Barclays），否則只引費率所在的行
    （MS、HSBC）。"""

    pattern: str = FEE_RATE
    missing: str = "找不到欄位標籤或已知寫法"
    ambiguous: str = ""
    row: bool = False

    def __call__(self, name: str, fee: str, label: str, top: Line, cells: list[Line]) -> ParsedField:
        if self.row:
            hits = [(r, [top, *cells]) for r in re.findall(self.pattern, "".join(squash(ln.text) for ln in cells))]
        else:
            ti = TextIndex(cells)
            hits = [(m[1], ti.lines_for(m.start(), m.end())) for m in ti.finditer(self.pattern)]
        return ParsedField.from_hits(
            name, hits, missing_note=self.missing.format(fee=fee), ambiguous_note=self.ambiguous
        )


@dataclass(frozen=True)
class FeeTable:
    """一家費用表的版面（`fee_rates` 的參數）；預設是 GS、Natixis、CA、DBS、UBS、BBVA 的表。

    `rows`：左欄項目名稱的開頭 → 費用項目（None：表上有、不核對，仍是列的分界）；`fees`：交出哪些費用項目（None：
    `rows` 的值）。費用項目在左欄剛好一列才讀；不是時為缺漏，說明 `duplicate`（`unlisted` 有值時，沒有這一列另用它；
    `last` 時重複的列取最後一列，Barclays）。"""

    grid: Callable[[list[Line]], FeeGrid | str] = rate_header_grid
    band: Callable[[list[Line], list[Line], int, Callable[[Line], bool]], list[Line]] = rows_by_position
    rate: Callable[[str, str, str, Line, list[Line]], ParsedField] = OneRate()
    rows: Mapping[str, str | None] = field(default_factory=lambda: FEE_ITEMS)
    fees: tuple[str, ...] | None = None
    duplicate: str = "費用表找不到「{fee}」這一列或出現多次"
    unlisted: str | None = None
    last: bool = False

    def wanted(self) -> tuple[str, ...]:
        return self.fees if self.fees is not None else tuple(dict.fromkeys(v for v in self.rows.values() if v))


SHARED_FEE_TABLE = FeeTable()  # GS、Natixis、CA、DBS、UBS、BBVA 與其投資人須知


def fee_rates(lines: list[Line], table: FeeTable = SHARED_FEE_TABLE) -> dict[str, ParsedField]:
    """費用表各費用項目的費率區間（標準欄位 `fee_<費用項目>`）：每個費用項目一列，從左欄項目名稱那一行到下一個項目名稱之前，
    該列費率欄的文字依 `table.rate` 讀成費率。座標見 `FeeTable`。"""
    fees = table.wanted()
    grid = table.grid(lines)
    if isinstance(grid, str):
        return {(name := standard_fields.fee_field(fee)): ParsedField.missing(name, grid) for fee in fees}
    starts = [
        (ln, label)
        for ln in grid.lines
        if grid.is_label(ln) and (label := next((k for k in table.rows if squash(ln.text).startswith(k)), None))
    ]
    tops = [ln for ln, _ in starts]
    out: dict[str, ParsedField] = {}
    for fee in fees:
        name = standard_fields.fee_field(fee)
        rows = [k for k, (_, label) in enumerate(starts) if table.rows[label] == fee]
        if len(rows) != 1 and not (table.last and rows):
            note = table.unlisted if table.unlisted is not None and not rows else table.duplicate
            out[name] = ParsedField.missing(name, note.format(fee=fee))
            continue
        k = rows[-1]
        top, label = starts[k]
        in_column = grid.column(top, fee)
        if isinstance(in_column, str):
            out[name] = ParsedField.missing(name, in_column)
            continue
        out[name] = table.rate(name, fee, label, top, table.band(grid.lines, tops, k, in_column))
    return out


def fee_rates_by_pattern(
    lines: list[Line] | None, patterns: Mapping[str, str], *, whole_match: bool = True, no_table: str = ""
) -> dict[str, ParsedField]:
    """不看座標的費用表（BNP、SG、Citi、JPM）：表的文字（去空白）中各費用項目那一列的固定句型 `patterns`（費用項目 →
    含費率一組的 pattern，例「申購費用申購價金的(X%~Y%)」）。證據 `whole_match` 引整句所在的行，否則只引費率所在的行
    （Citi、JPM 投資人須知）；`lines` 為 None（找不到費用段落）時都是缺漏，說明 `no_table`。"""
    if lines is None:
        return {(name := standard_fields.fee_field(fee)): ParsedField.missing(name, no_table) for fee in patterns}
    ti = TextIndex(lines)
    return {
        (name := standard_fields.fee_field(fee)): capture(name, ti, pattern, whole_match=whole_match)
        for fee, pattern in patterns.items()
    }
