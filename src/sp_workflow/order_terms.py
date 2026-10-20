"""Order 條件讀取：從商品資料夾裡我方寄出的 `(Order)` 原信讀出全部條件，統一欄名後輸出條件清單（#40）。

對外只有 `read_orders`。商品資料夾只讀不寫；條件以 Order 原信為準，上手回覆信（RE:／FW:／DONE: 等）不讀，
唯一例外是 Order 沒寫的日期：依 `order_formats` 的 `supplement` 設定到指定的上手信補（GS、HSBC）。
信件內文的固定開頭（`We buy <TDCC> <幣別> <金額> @<價格>`、`Risk level:` 等）各上手共用一套讀法；
條件表（表頭一列、值一列的 HTML 表格）依 TDCC 前三碼的上手套用 `order_formats` 的欄名對照。
規則式、不猜值：讀不到、格式不認得、兩處寫法不一致、值有修改（`→`）、出現沒列過的欄名，
都在「待確認原因」寫出來，整檔標「待確認」。一封信下多檔 TDCC 時依各自的表拆成多列。
"""

from __future__ import annotations

import datetime as dt
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from sp_workflow.order_formats import (
    ALIASES,
    FIELDS,
    ISSUERS,
    KNOWN_WITHOUT_FORMAT,
    REQUIRED,
    SUPPLEMENT_FIELDS,
    TDCC_HEADERS,
    DateSupplement,
    IssuerFormat,
    Kind,
    header_key,
)
from sp_workflow.order_template import template_row
from sp_workflow.order_template import write_sheet as write_template_sheet
from sp_workflow.preparation import find_product_folders, is_tdcc, parse_tdccs

READ = "已讀取"
PENDING = "待確認"
NO_ORDER = "找不到 Order"
UNSUPPORTED = "未支援上手"

LIST_SHEET = "Order條件清單"
RAW_SHEET = "原文對照"
INFO_SHEET = "資料來源"
OTHER_COLUMN = "其他欄位"
# 清單的欄（欄名, 欄寬）：前面固定、中間是統一欄位、最後是其他欄位與來源
HEAD_COLUMNS = (("TDCC", 15), ("狀態", 10), ("上手", 9), ("待確認原因", 50))
TAIL_COLUMNS = ((OTHER_COLUMN, 50), ("來源信件", 50), ("寄件時間", 18))
RAW_COLUMNS = (("TDCC", 15), ("來源信件", 40), ("位置", 12), ("原始欄名", 30), ("原始值", 40), ("統一欄位", 14))

BUY_SOURCE = "We buy 行"
SUPPLEMENT_SOURCE = "補日期："  # 後面接信件檔名
LINE_SOURCE = "開頭行"
_EMPTY_VALUES = frozenset({"", "-", "n/a", "na"})
_ORDER_MARK = re.compile(r"(?i)[(\[]order[)\]]")  # (Order) 或 [Order]
_REPLY_MARK = re.compile(r"(?i)\b(re|fw|fwd|done)\s*[:：]|回覆\s*[:：]|轉寄\s*[:：]")
_SECTION_TDCC = re.compile(r"(?i)^\s*(?:[0-9]{1,2}\s*[.)、]\s*)?(?:TDCC(?:\s*code)?\s*[=:：]?\s*)?([0-9]{12})\s*$")
_SECTION_NUMBER = re.compile(r"^\s*([0-9]{1,2})\s*[.)、]\s*$")
_WE_BUY = re.compile(r"(?i)\bwe\s+buy\b")
_TDCC_IN_LINE = re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])")
_BUY_PRICE = re.compile(r"@\s*([0-9]+(?:\.[0-9]+)?)")
_BUY_UF = re.compile(r"(?i)\bUF\s*[=:]\s*([0-9]+(?:\.[0-9]+)?)\s*%?")
_BUY_BODY_END = re.compile(r"(?i)[,，]|\bUF\b")
_CURRENCY = re.compile(r"\b([A-Z]{3})\b")
_SETTLE = re.compile(r"(?i)please\s+settle\s+thru\s+(.+?)\.?\s*$")
_KEY_VALUE = re.compile(r"^([A-Za-z][A-Za-z0-9 .()%/?+\-]*?)\s*[:：]\s*(.+)$")
# 回覆信引用的上一封信從這裡開始，之後的內容不讀
_QUOTE_START = re.compile(r"(?i)^\s*(from|寄件者|sent|寄件日期)\s*[:：]|^\s*-{3,}\s*original message")
_ARROW = re.compile(r"→|->|=>|>>|(?<=[0-9])\s*>\s*(?=[0-9])")  # 改價寫法；單一個 > 只認兩邊都是數字
_MONTHS = {
    m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)
}
_AMOUNT_UNITS = {
    "": 1,
    "k": 1_000,
    "m": 1_000_000,
    "mm": 1_000_000,
    "mn": 1_000_000,
    "mio": 1_000_000,
    "million": 1_000_000,
}


class OrderTermsError(Exception):
    """整批無法開始（輸入或資料夾有問題）；訊息是給作業人員看的中文。"""


class MailReadError(Exception):
    """一封 `.msg` 讀不到；訊息是給作業人員看的中文。"""


@dataclass(frozen=True)
class Mail:
    subject: str
    sent_on: dt.datetime | None
    html: str


MailReader = Callable[[Path], Mail]


@dataclass(frozen=True)
class RawEntry:
    """信裡讀到的一個欄位：來源位置、原始欄名、原始值。"""

    source: str  # We buy 行／開頭行／條件表 n／補日期：<信件檔名>
    header: str
    value: str
    day_first: bool = False  # 日期寫成 日/月/年（只有指定的上手信）


@dataclass(frozen=True)
class OrderRow:
    tdcc: str
    status: str
    issuer: str = ""
    reasons: tuple[str, ...] = ()
    values: dict[str, object] = field(default_factory=dict)  # 統一欄位 → 統一後的值
    others: tuple[tuple[str, str], ...] = ()  # 不屬於統一欄位的（原始欄名, 值）
    raw: tuple[tuple[RawEntry, str], ...] = ()  # (原文, 對到的統一欄位或空字串)
    source: Path | None = None
    sent_on: dt.datetime | None = None
    template: tuple[object, ...] = ()  # 格式表的一列（order_template.COLUMNS 順序）
    flagged: frozenset[int] = frozenset()  # 格式表裡要標淺紅底的欄（COLUMNS 的位置）
    folders: tuple[Path, ...] = ()  # 這檔相關的商品資料夾（匯出問題信件用）


@dataclass(frozen=True)
class OrderReceipt:
    list_path: Path
    rows: tuple[OrderRow, ...]
    unreadable_dirs: tuple[Path, ...] = ()


# ---------- 讀信 ----------


def read_orders(
    text: str,
    root: Path,
    out_parent: Path,
    *,
    reader: MailReader | None = None,
    now: dt.datetime | None = None,
) -> OrderReceipt:
    """依輸入順序讀每個 TDCC 的 Order；同一封信裡的其他 TDCC 接在後面。回傳清單路徑與逐列結果。

    reader 預設用傳統版 Outlook 開 `.msg`（只讀，不會寄出或修改信件）。
    """
    tokens = parse_tdccs(text)
    if not tokens:
        raise OrderTermsError("沒有輸入 TDCC")
    if not root.is_dir():
        raise OrderTermsError(f"根目錄不存在：{root}")
    if not out_parent.is_dir():
        raise OrderTermsError(f"輸出位置不存在：{out_parent}")
    list_path = out_parent / f"Order條件清單_{(now or dt.datetime.now()):%Y%m%d-%H%M%S}.xlsx"
    if list_path.exists():
        raise OrderTermsError(f"清單已存在：{list_path}")

    read_mail = reader or OutlookMsgReader()
    valid = [t for t in tokens if is_tdcc(t)]
    folders, unreadable = find_product_folders(root, set(valid))
    library = _OrderLibrary(read_mail)
    folder_notes: dict[str, list[str]] = {}
    for tdcc in valid:
        found = folders.get(tdcc, [])
        if len(found) == 1:
            folder_notes[tdcc] = library.load_folder(found[0])

    rows: list[OrderRow] = []
    done: set[str] = set()
    for tdcc in tokens:
        if tdcc in done:
            continue
        done.add(tdcc)
        row = _row_for(tdcc, folders.get(tdcc, []), folder_notes.get(tdcc, []), unreadable, library)
        rows.append(row)
        # 同一封信下的其他 TDCC 接在後面（例如一封信下兩檔，只貼了第一檔）
        for sibling in library.siblings(tdcc):
            if sibling not in done and sibling not in tokens:
                done.add(sibling)
                rows.append(_row_for(sibling, folders.get(sibling, []), [], unreadable, library))
    _write_list(list_path, rows, root, unreadable)
    return OrderReceipt(list_path, tuple(rows), tuple(unreadable))


@dataclass(frozen=True)
class _ParsedMail:
    path: Path
    mail: Mail
    records: dict[str, list[RawEntry]]  # TDCC → 這封信裡屬於它的欄位
    problems: dict[str, list[str]]  # TDCC → 這封信讀這檔時的問題
    mail_problems: tuple[str, ...]  # 整封信的問題（例如找不到 TDCC）

    @property
    def identity(self) -> tuple[str, dt.datetime | None]:
        """同一封信存在兩個商品資料夾時算一封。"""
        return (self.mail.subject, self.mail.sent_on)


class _OrderLibrary:
    """這次讀過的 Order 原信；同一個檔只讀一次。"""

    def __init__(self, reader: MailReader) -> None:
        self.reader = reader
        self.orders: list[_ParsedMail] = []
        self.mails: dict[Path, Mail] = {}  # 讀過的每封信（補日期用）
        self._loaded: set[Path] = set()

    def load_folder(self, folder: Path) -> list[str]:
        """讀商品資料夾（不含子資料夾）裡的 `.msg`，留下 Order 原信；回傳讀不到的信件說明。"""
        notes: list[str] = []
        try:
            files = sorted(
                (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".msg"), key=lambda p: p.name
            )
        except OSError as exc:
            return [f"讀不到商品資料夾：{exc}"]
        for path in files:
            if path in self._loaded:
                continue
            self._loaded.add(path)
            try:
                mail = self.reader(path)
            except (MailReadError, OSError) as exc:
                notes.append(f"讀不到信件 {path.name}：{exc}")
                continue
            self.mails[path] = mail
            if is_order_subject(mail.subject):
                self.orders.append(parse_order(path, mail))
        return notes

    def orders_for(self, tdcc: str) -> list[_ParsedMail]:
        """提到這檔 TDCC 的 Order 原信；同一封信（主旨與寄件時間相同）只算一封。"""
        unique: dict[tuple[str, dt.datetime | None], _ParsedMail] = {}
        for order in self.orders:
            if tdcc in order.records:
                unique.setdefault(order.identity, order)
        return list(unique.values())

    def supplement_mails(self, order: _ParsedMail, tdcc: str, supplement: DateSupplement) -> list[tuple[Path, Mail]]:
        """補日期用的信：同一個商品資料夾、主旨符合設定；沒設定主旨時就是 Order 原信本身（含下方引用）。"""
        if supplement.subject is None:
            return [(order.path, order.mail)]
        pattern = re.compile(supplement.subject.replace("{tdcc}", re.escape(tdcc)), re.IGNORECASE)
        return [(p, m) for p, m in self.mails.items() if p.parent == order.path.parent and pattern.search(m.subject)]

    def orders_in(self, folder: Path) -> list[_ParsedMail]:
        return [o for o in self.orders if o.path.parent == folder]

    def siblings(self, tdcc: str) -> list[str]:
        found = self.orders_for(tdcc)
        if len(found) != 1:
            return []
        return [t for t in found[0].records if t != tdcc]


def is_order_subject(subject: str) -> bool:
    """主旨有 `(Order)` 或 `[Order]`（不分大小寫），且前面沒有 RE:／FW:／DONE: 等回覆、轉寄記號。"""
    mark = _ORDER_MARK.search(subject)
    if mark is None:
        return False
    return _REPLY_MARK.search(subject[: mark.start()]) is None


def _row_for(
    tdcc: str, folders: list[Path], notes: list[str], unreadable: Sequence[Path], library: _OrderLibrary
) -> OrderRow:
    if not is_tdcc(tdcc):
        return OrderRow(tdcc, NO_ORDER, reasons=("不是 12 碼數字",))
    issuer = _issuer_name(tdcc)
    orders = library.orders_for(tdcc)
    if not orders:
        reasons = [*notes]
        if len(folders) > 1:
            reasons.insert(0, f"找到 {len(folders)} 個同名商品資料夾：{'、'.join(str(f) for f in folders)}")
        elif not folders:
            reason = "找不到商品資料夾"
            if unreadable:
                reason += f"（搜尋時有 {len(unreadable)} 個資料夾讀不到，例如 {unreadable[0]}）"
            reasons.insert(0, reason)
        else:
            others = library.orders_in(folders[0])
            if others:
                names = "、".join(o.path.name for o in others)
                reasons.insert(0, f"資料夾裡的 Order 信沒有提到這檔 TDCC：{names}")
            else:
                reasons.insert(0, "商品資料夾裡沒有 (Order) 原信")
        # 有信讀不到時可能就是那封 Order，交給人確認
        return OrderRow(tdcc, PENDING if notes else NO_ORDER, issuer, tuple(reasons), folders=tuple(folders))
    order_folders = tuple(dict.fromkeys([*folders, *(o.path.parent for o in orders)]))
    if len(orders) > 1:
        names = "、".join(str(o.path) for o in orders)
        reason = f"有 {len(orders)} 封 (Order) 原信，請確認用哪一封：{names}"
        return OrderRow(tdcc, PENDING, issuer, (reason, *notes), folders=order_folders)
    order = orders[0]
    fmt = ISSUERS.get(tdcc[:3])
    raw = tuple(order.records[tdcc])
    if fmt is None:
        reason = "這家上手還沒有 Order 讀法（需要樣本）" if issuer else "TDCC 前三碼不是已知上手"
        return OrderRow(
            tdcc,
            UNSUPPORTED,
            issuer,
            (reason,),
            raw=tuple((e, "") for e in raw),
            source=order.path,
            sent_on=order.mail.sent_on,
            folders=order_folders,
        )
    supplement_notes: list[str] = []
    if fmt.supplement is not None:
        sources = library.supplement_mails(order, tdcc, fmt.supplement)
        for path, mail in sources:
            raw += tuple(supplement_entries(tdcc, path, mail, fmt.supplement.day_first))
        if not sources:
            supplement_notes.append(f"沒找到補日期的上手信（主旨符合 {fmt.supplement.subject}）")
    values, others, mapped, problems, flagged_fields = map_entries(raw, fmt)
    if any(name not in values for name in SUPPLEMENT_FIELDS):
        problems.extend(supplement_notes)
    row_level = bool(order.mail_problems or order.problems.get(tdcc) or notes)
    template, template_problems, flagged = template_row(
        tdcc, values, others, order.mail.subject, order.mail.sent_on, flagged_fields | ({""} if row_level else set())
    )
    reasons = (*order.mail_problems, *order.problems.get(tdcc, []), *problems, *template_problems, *notes)
    return OrderRow(
        tdcc,
        PENDING if reasons else READ,
        fmt.name,
        tuple(reasons),
        values,
        others,
        mapped,
        order.path,
        order.mail.sent_on,
        template,
        flagged,
        order_folders,
    )


def _issuer_name(tdcc: str) -> str:
    fmt = ISSUERS.get(tdcc[:3])
    return fmt.name if fmt else KNOWN_WITHOUT_FORMAT.get(tdcc[:3], "")


# ---------- 拆信件內容 ----------


@dataclass(frozen=True)
class _Line:
    text: str


@dataclass(frozen=True)
class _Table:
    rows: tuple[tuple[str, ...], ...]


class _BodyParser(HTMLParser):
    """把信件 HTML 拆成依序的文字行與表格（只看最外層表格）。"""

    _BLOCKS = frozenset({"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"})
    _SKIP = frozenset({"head", "style", "script", "title"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[_Line | _Table] = []
        self._text: list[str] = []
        self._skip = 0
        self._depth = 0
        self._rows: list[tuple[str, ...]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif tag == "table":
            if self._depth == 0:
                self._flush()
                self._rows = []
            self._depth += 1
        elif self._depth == 1 and tag == "tr":
            self._row = []
        elif self._depth == 1 and tag in ("td", "th"):
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")
        elif self._depth == 0 and tag in self._BLOCKS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "table" and self._depth:
            self._depth -= 1
            if self._depth == 0:
                self._end_row()
                self.items.append(_Table(tuple(self._rows)))
        elif self._depth == 1 and tag in ("td", "th"):
            self._end_cell()
        elif self._depth == 1 and tag == "tr":
            self._end_row()
        elif self._depth == 0 and tag in self._BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._depth == 0:
            self._text.append(data)
        elif self._cell is not None:
            self._cell.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _end_cell(self) -> None:
        if self._cell is not None and self._row is not None:
            self._row.append(_clean("".join(self._cell)))
        self._cell = None

    def _end_row(self) -> None:
        self._end_cell()
        if self._row is not None:
            self._rows.append(tuple(self._row))
        self._row = None

    def _flush(self) -> None:
        text = _clean("".join(self._text))
        if text:
            self.items.append(_Line(text))
        self._text = []


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_order(path: Path, mail: Mail) -> _ParsedMail:
    """拆出一封 Order 原信裡每檔 TDCC 的原始欄位（還沒對照統一欄位）。

    開頭的「名稱: 值」行屬於所有 TDCC；分段標記之後的行與表格屬於那檔。分段標記是單獨一行的 TDCC
    （可帶「1.」「TDCC =」「TDCC code:」「TDCC」）、分段裡只寫一檔的 We buy 行，或只寫「1.」「2.」時
    依 We buy 行的 TDCC 順序對應（有安全條件，見 `_resolve_numbered_sections`）；對不上的交給人工回填
    （使用者 2026-10-20：不過度客製，偏離常態的信接受人工回填）。
    表格屬於哪檔：表上有 TDCC 欄 → 依欄；前面有分段標記 → 那檔；整封信只有一檔 → 那檔。
    """
    parser = _BodyParser()
    parser.feed(mail.html)
    parser.close()

    records: dict[str, list[RawEntry]] = {}
    problems: dict[str, list[str]] = {}
    common: list[RawEntry] = []
    unassigned: list[tuple[str, tuple[str, ...], list[tuple[str, ...]]]] = []
    mail_problems: list[str] = []
    context: str | None = None
    buy_order: list[str] = []  # We buy 行的 TDCC 順序
    section_tables: dict[str, int] = {}  # 「1.」「2.」分段（暫用 #1、#2）各有幾張條件表
    tables = 0
    seen_table = False

    def entries_for(tdcc: str | None) -> list[RawEntry]:
        return common if tdcc is None else records.setdefault(tdcc, [])

    def switch_to(tdcc: str) -> str:
        """「1.」之後又寫了這段是哪檔（SG 的「TDCC = …」或一行 We buy）：暫用的 #1 併進那檔。"""
        if context in section_tables and section_tables[context] == 0:
            del section_tables[context]
            records.setdefault(tdcc, []).extend(records.pop(context, []))
        records.setdefault(tdcc, [])
        return tdcc

    for item in parser.items:
        if isinstance(item, _Line):
            line = item.text
            if _QUOTE_START.search(line):
                break
            standalone = _SECTION_TDCC.match(line)
            if standalone:  # 例：「156…」「1. 029…」「TDCC = 037…」「TDCC 086…」
                context = switch_to(standalone.group(1))
                continue
            numbered = _SECTION_NUMBER.match(line)
            if numbered:  # 只寫「1.」「2.」：先記成 #1、#2，讀完整封再依 We buy 行的順序對應
                context = f"#{int(numbered.group(1))}"
                section_tables.setdefault(context, 0)
                continue
            if _WE_BUY.search(line):
                segments = buy_segments(line)
                for tdcc, parts in segments:
                    target = entries_for(tdcc)
                    target.extend(RawEntry(BUY_SOURCE, name, value) for name, value in parts)
                    if tdcc not in buy_order:
                        buy_order.append(tdcc)
                if len(segments) == 1 and context is not None:
                    context = switch_to(segments[0][0])  # 分段裡各自一行 We buy（SG），這段就是那檔
            settle = _SETTLE.search(line)
            if settle:
                entries_for(context).append(RawEntry(LINE_SOURCE, "Please settle thru", settle.group(1).strip()))
                continue
            key_value = _KEY_VALUE.match(line)
            # 第一個條件表之前的「名稱: 值」都收；之後只收認得的名稱，避免把簽名檔（Tel: …）當條件
            if key_value and not _WE_BUY.search(line) and (not seen_table or header_key(key_value.group(1)) in ALIASES):
                entries_for(context).append(
                    RawEntry(LINE_SOURCE, key_value.group(1).strip(), key_value.group(2).strip())
                )
            continue
        rows = [r for r in item.rows if any(c for c in r)]
        if len(rows) < 2 or sum(bool(c) for c in rows[0]) < 2:
            continue  # 不是條件表（例如外部信件警示框）
        seen_table = True
        tables += 1
        source = f"條件表 {tables}"
        header, values = rows[0], rows[1:]
        tdcc_column = next((i for i, h in enumerate(header) if header_key(h) in TDCC_HEADERS), None)
        if tdcc_column is not None:
            for row in values:
                tdcc = row[tdcc_column] if tdcc_column < len(row) else ""
                if not is_tdcc(tdcc):
                    mail_problems.append(f"{source} 的 TDCC 欄不是 12 碼數字：{tdcc or '（空白）'}")
                    continue
                _add_table(records, problems, tdcc, source, header, [row])
        elif context is not None:
            _add_table(records, problems, context, source, header, values)
            if context in section_tables:
                section_tables[context] += 1
        else:
            unassigned.append((source, header, values))

    _resolve_numbered_sections(records, problems, section_tables, buy_order, mail_problems)
    for source, header, values in unassigned:
        if len(records) == 1:
            _add_table(records, problems, next(iter(records)), source, header, values)
        else:
            mail_problems.append(f"{source} 對不到是哪一檔 TDCC")
    if not records:
        mail_problems.append("信裡找不到 TDCC")
    for tdcc in records:
        records[tdcc] = [*common, *records[tdcc]]
    return _ParsedMail(path, mail, records, problems, tuple(mail_problems))


def _resolve_numbered_sections(
    records: dict[str, list[RawEntry]],
    problems: dict[str, list[str]],
    section_tables: dict[str, int],
    buy_order: list[str],
    mail_problems: list[str],
) -> None:
    """「1.」「2.」分段依 We buy 行的 TDCC 順序對應（使用者 2026-10-20 確認，交易室多檔下單的固定寫法）。

    安全條件：分段剛好是 #1～#n、n 等於 We buy 行的 TDCC 數，而且每段剛好一張條件表；不符合就不猜，交給人工。
    """
    if not section_tables:
        return
    expected = [f"#{n}" for n in range(1, len(buy_order) + 1)]
    ok = sorted(section_tables, key=lambda s: int(s[1:])) == expected and all(section_tables[s] == 1 for s in expected)
    for n, section in enumerate(sorted(section_tables, key=lambda s: int(s[1:]))):
        entries = records.pop(section, [])
        found = problems.pop(section, [])
        if ok:
            tdcc = buy_order[n]
            records.setdefault(tdcc, []).extend(entries)
            problems.setdefault(tdcc, []).extend(found)
    if not ok:
        mail_problems.append(
            f"分段「1.」「2.」…有 {len(section_tables)} 段、We buy 行有 {len(buy_order)} 檔，或某段不是剛好一張條件表，"
            "無法對應是哪一檔 TDCC"
        )


def buy_segments(line: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """拆 We buy 行：每檔 TDCC 後面到下一檔 TDCC 之前是它的幣別、金額、`@價格` 或 `UF=x%`。

    例：`We buy 0291… USD 200K @98.50`、`We buy 0760… 45 million JPY @97.9. Please …`、
    `We buy 3281… USD 950K @96.15、3281… USD 1 mio @96.15`、`We buy 1594… USD 300K , UF=0.85%`。
    """
    matches = list(_TDCC_IN_LINE.finditer(line))
    segments = []
    for i, m in enumerate(matches):
        text = line[m.end() : matches[i + 1].start() if i + 1 < len(matches) else len(line)]
        text = re.split(r"(?i)\bplease\b", text)[0]
        price = _BUY_PRICE.search(text)
        uf = _BUY_UF.search(text)
        body = text[: price.start()] if price else _BUY_BODY_END.split(text)[0]
        parts = []
        currency = _CURRENCY.search(body)
        if currency:
            parts.append(("幣別", currency.group(1)))
        parts.append(("金額", _CURRENCY.sub(" ", body).strip(" ,，、.")))
        if price:
            parts.append(("價格", price.group(1)))
        if uf:
            parts.append(("UF", uf.group(1)))
        segments.append((m.group(0), parts))
    return segments


def supplement_entries(tdcc: str, path: Path, mail: Mail, day_first: bool) -> list[RawEntry]:
    """從上手信整封（含下方引用）找這檔 TDCC 的日期；只收交易日、發行日、最終比價日、到期日。

    - 橫式表：表頭有 TDCC 欄，取 TDCC 相同的那列（GS 的 Order Summary）。
    - 直式表：每列「名稱 | 值」，而且緊接在寫了這檔 TDCC 的那一行後面（HSBC 回覆的「TDCC code: …」）。
      多檔一起下單的直式表（一檔一欄）不猜欄位順序，交給人工。
    """
    parser = _BodyParser()
    parser.feed(mail.html)
    parser.close()
    source = f"{SUPPLEMENT_SOURCE}{path.name}"
    entries: list[RawEntry] = []
    last_tdcc: str | None = None

    def add(header: str, value: str) -> None:
        if value and ALIASES.get(header_key(header)) in SUPPLEMENT_FIELDS:
            entries.append(RawEntry(source, header, value, day_first))

    for item in parser.items:
        if isinstance(item, _Line):
            found = _TDCC_IN_LINE.findall(item.text)
            if found:
                last_tdcc = found[-1]
            continue
        rows = [r for r in item.rows if any(r)]
        header = rows[0] if rows else ()
        column = next((i for i, h in enumerate(header) if header_key(h) in TDCC_HEADERS), None)
        if column is not None:
            for row in rows[1:]:
                if len(row) == len(header) and row[column] == tdcc:
                    for name, value in zip(header, row, strict=True):
                        add(name, value)
        elif last_tdcc == tdcc:
            for row in rows:
                cells = [c for c in row if c]
                if len(cells) == 2:
                    add(*cells)
        last_tdcc = None  # 直式表只認緊接在 TDCC 那行後面的第一張表
    return entries


def _add_table(
    records: dict[str, list[RawEntry]],
    problems: dict[str, list[str]],
    tdcc: str,
    source: str,
    header: tuple[str, ...],
    values: list[tuple[str, ...]],
) -> None:
    target = records.setdefault(tdcc, [])
    if len(values) != 1:
        problems.setdefault(tdcc, []).append(f"{source} 有 {len(values)} 列值，無法判斷用哪一列")
        return
    row = values[0]
    if len(row) != len(header):
        problems.setdefault(tdcc, []).append(f"{source} 欄數（{len(header)}）和值數（{len(row)}）對不上")
        return
    for name, value in zip(header, row, strict=True):
        if name and header_key(name) not in TDCC_HEADERS:
            target.append(RawEntry(source, name, value))


# ---------- 對照統一欄位 ----------

_BUY_FIELDS = {"幣別": "幣別", "金額": "承作金額", "價格": "價格(%)", "UF": "UF(%)"}
_HUNDRED = Decimal(100)
_SETTLE_FIELD = "交割說明"


def map_entries(
    entries: Iterable[RawEntry], fmt: IssuerFormat
) -> tuple[dict[str, object], tuple[tuple[str, str], ...], tuple[tuple[RawEntry, str], ...], list[str], set[str]]:
    """依上手讀法把原始欄位對到統一欄位並統一格式。

    回傳 (統一欄位值, 其他欄位, 原文與對到的統一欄位, 待確認原因, 有問題的統一欄位)。
    同一統一欄位有兩處寫法且統一後不同時留空。不屬於某一欄的問題（例如沒列過的欄）在有問題的欄位裡記成空字串。
    """
    candidates: dict[str, list[tuple[RawEntry, str]]] = {}
    others: list[tuple[str, str]] = []
    mapped: list[tuple[RawEntry, str]] = []
    problems: list[str] = []
    flagged: set[str] = set()

    def assign(entry: RawEntry, name: str | None, value: str) -> None:
        mapped.append((entry, name or ""))
        if name:
            candidates.setdefault(name, []).append((entry, value))
        elif not _is_empty(value):
            others.append((entry.header, value))

    for entry in entries:
        if entry.source == BUY_SOURCE:
            assign(entry, _BUY_FIELDS[entry.header], entry.value)
        elif entry.header == "Please settle thru":
            assign(entry, _SETTLE_FIELD, entry.value)
        elif entry.source.startswith(SUPPLEMENT_SOURCE):
            name = ALIASES.get(header_key(entry.header))
            if name in SUPPLEMENT_FIELDS:
                assign(entry, name, entry.value)
        elif entry.source == LINE_SOURCE:
            assign(entry, fmt.field_for(entry.header), entry.value)
        else:
            if not fmt.knows(entry.header):
                problems.append(f"條件表有沒列過的欄「{entry.header}」（上手格式可能改版）")
                flagged.add("")
            labels = fmt.embedded_labels(entry.header)
            if not labels:
                assign(entry, fmt.field_for(entry.header), entry.value)
                continue
            leftover, parts = split_embedded(entry.value, labels)
            if leftover:
                others.append((entry.header, leftover))
            for label, value in parts:
                sub = RawEntry(entry.source, f"{entry.header}／{label}", value)
                assign(sub, fmt.field_for(label), value)

    values: dict[str, object] = {}
    for name, found in candidates.items():
        normalized: list[tuple[RawEntry, object]] = []
        for entry, raw in found:
            value, revised = _latest(raw)
            if _is_empty(value):
                continue
            if revised:
                problems.append(f"{name} 有修改：{raw}")
                flagged.add(name)
            try:
                normalized.append((entry, normalize(FIELDS[name], value, day_first=entry.day_first)))
            except ValueError:
                problems.append(f"{name} 格式不認得：{raw}（{entry.source}「{entry.header}」）")
                flagged.add(name)
        distinct = {_compare_key(v) for _, v in normalized}
        if len(distinct) > 1:
            detail = "／".join(f"{e.source}「{e.header}」{e.value}" for e, _ in normalized)
            problems.append(f"{name} 不一致：{detail}")
            flagged.add(name)
        elif normalized:
            values[name] = normalized[0][1]
    for name, shift in fmt.shifts.items():
        if isinstance(values.get(name), int | Decimal):
            values[name] += shift
    # Citi 只給 UF、沒有票面價格：價格 = 100 − UF（與庫存的 Upfront / NotePrice 相同算法）
    if "價格(%)" not in values and isinstance(values.get("UF(%)"), Decimal) and not _has_problem(problems, "價格(%)"):
        values["價格(%)"] = _HUNDRED - values["UF(%)"]
    for name in REQUIRED:
        if name not in values and not _has_problem(problems, name):
            problems.append(f"讀不到{name}")
            flagged.add(name)
    return values, tuple(others), tuple(mapped), problems, flagged


def _has_problem(problems: Sequence[str], name: str) -> bool:
    return any(p.startswith(f"{name} ") for p in problems)


def split_embedded(text: str, labels: Sequence[str]) -> tuple[str, list[tuple[str, str]]]:
    """拆開一格裡的「名稱: 值」；回傳 (第一個名稱前的文字, [(名稱, 值)])。值去掉前後空白與方括號。"""
    pattern = re.compile(
        r"(?i)(?<![A-Za-z])(" + "|".join(re.escape(lb) for lb in sorted(labels, key=len, reverse=True)) + r")\s*:"
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return text.strip(), []
    leftover = text[: matches[0].start()].strip(" []")
    parts = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        label = next(lb for lb in labels if lb.lower() == m.group(1).lower())
        parts.append((label, text[m.end() : end].strip(" []")))
    return leftover, parts


def _latest(raw: str) -> tuple[str, bool]:
    """`96.20%→96.15%`、`99.65->99.62`、`99.92 >> 99.89` 取最後的值；回傳 (值, 是否有修改)。"""
    parts = _ARROW.split(raw)
    if len(parts) > 1:
        return parts[-1].strip(), True
    return raw.strip(), False


def _is_empty(value: str) -> bool:
    """空白、`-`、`N/A`，或完全沒有字母數字（例：寄件端亂碼成的 `�@`，原本是全形空白）。"""
    text = value.strip()
    return text.lower() in _EMPTY_VALUES or not any(ch.isalnum() for ch in text)


def _compare_key(value: object) -> object:
    return value.casefold() if isinstance(value, str) else value


def normalize(kind: Kind, raw: str, *, day_first: bool = False) -> object:
    """依欄位型別統一格式；不認得時丟 ValueError。"""
    text = _clean(raw)
    if kind is Kind.TEXT:
        return text
    if kind is Kind.PERCENT:
        return _decimal(text.replace("%", "").replace(",", "").strip())
    if kind is Kind.MONTHS:
        m = re.fullmatch(r"(?i)([0-9]+)\s*m?", text)
        if not m:
            raise ValueError(raw)
        return int(m.group(1))
    if kind is Kind.DATE:
        return parse_date(text, day_first=day_first)
    if kind is Kind.AMOUNT:
        return parse_amount(text)
    if kind is Kind.INITIAL_PRICE:
        lowered = text.lower()
        for mark, value in (("open", "開盤價"), ("clos", "收盤價"), ("vwap", "VWAP")):
            if mark in lowered:
                return value
        raise ValueError(raw)
    raise ValueError(raw)


def _decimal(text: str) -> Decimal:
    if not re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", text):
        raise ValueError(text)
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(text) from exc


def parse_date(text: str, *, day_first: bool = False) -> dt.date:
    """`15-Oct-26`、`20-Oct-2026`、`20Oct26`、`2026-10-13`、`2026/10/13`；day_first 時另認 `13/10/2026`。"""
    if day_first:  # 只用在指定的上手信，例：HSBC 13/10/2026
        m = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})", text)
        if m:
            return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.fullmatch(r"([0-9]{4})[-/]([0-9]{1,2})[-/]([0-9]{1,2})", text)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"([0-9]{1,2})[- ]?([A-Za-z]{3})[- ]?([0-9]{2}|[0-9]{4})", text)
    if m and m.group(2).lower() in _MONTHS:
        year = int(m.group(3))
        return dt.date(year + 2000 if year < 100 else year, _MONTHS[m.group(2).lower()], int(m.group(1)))
    raise ValueError(text)


def parse_amount(text: str) -> Decimal:
    """`200K`、`1.01M`、`45 million`、`USD 950 K`、`1 mio`、`10,000,000`；幣別字母略過。"""
    stripped = _CURRENCY.sub(" ", text).replace(",", "").strip()
    m = re.fullmatch(r"(?i)([0-9]+(?:\.[0-9]+)?)\s*([a-z]*)", stripped)
    if not m or m.group(2).lower() not in _AMOUNT_UNITS:
        raise ValueError(text)
    amount = Decimal(m.group(1)) * _AMOUNT_UNITS[m.group(2).lower()]
    return Decimal(int(amount)) if amount == amount.to_integral_value() else amount


# ---------- Outlook ----------


def _long_path(path: Path) -> str:
    r"""Windows 長路徑寫法（`\\?\` 前綴），超過 260 字也能讀檔。"""
    text = str(path)
    if sys.platform != "win32" or text.startswith("\\\\?\\"):
        return text
    return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text


class OutlookMsgReader:
    """用傳統版 Outlook（COM）開 `.msg` 讀主旨、寄件時間與 HTML 內文；開完即關閉、不存任何變更。"""

    _UNSENT_YEAR = 4501  # Outlook 沒有寄件時間時給 4501-01-01
    _LONG_PATH = 240  # Windows 傳統路徑上限 260，留一點餘裕

    def __call__(self, path: Path) -> Mail:
        import pythoncom
        import win32com.client

        full = path.resolve()  # Outlook 依它自己的工作目錄解讀相對路徑
        temp_dir = None
        if len(str(full)) >= self._LONG_PATH:
            # 公槽上檔名很長的信（例：GS Trade Executed）超過 Windows 路徑長度，Outlook 開不了；先複製成短檔名
            temp_dir = tempfile.mkdtemp(prefix="spw-msg-")
            short = Path(temp_dir) / "mail.msg"
            try:
                shutil.copyfile(_long_path(full), short)
            except OSError as exc:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise MailReadError(f"路徑太長，複製到暫存區也失敗：{exc}") from exc
            full = short
        pythoncom.CoInitialize()  # 視窗在背景執行緒呼叫，COM 要在這個執行緒初始化
        try:
            namespace = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
            item = namespace.OpenSharedItem(str(full))
            try:
                subject, html, sent = str(item.Subject or ""), str(item.HTMLBody or ""), item.SentOn
            finally:
                item.Close(1)  # 1 = olDiscard
        except pythoncom.com_error as exc:  # 原始訊息常是系統語系的亂碼，改成中文說明
            code = exc.args[0] if exc.args else ""
            hresult = f"0x{code & 0xFFFFFFFF:08X}" if isinstance(code, int) else str(code)
            raise MailReadError(f"Outlook 開不了這封信（可能 Outlook 還沒設定或跳出對話框），錯誤碼 {hresult}") from exc
        finally:
            pythoncom.CoUninitialize()
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)
        sent_on = None
        if sent is not None and sent.year < self._UNSENT_YEAR:
            # pywin32 把本地時間標成 UTC，只取欄位值
            sent_on = dt.datetime(sent.year, sent.month, sent.day, sent.hour, sent.minute, sent.second)
        return Mail(subject, sent_on, html)


# ---------- 清單 ----------


def _write_list(path: Path, rows: Sequence[OrderRow], root: Path, unreadable: Sequence[Path]) -> None:
    wb = openpyxl.Workbook()
    # 第一張是作業人員既有的「Outlook orders 轉成 Excel」格式
    # 使用者 2026-10-20 改為：待確認的也照樣放，有問題的格子淺紅底；沒有讀到 Order 的（沒有格式表列）不放
    write_template_sheet(wb.active, [(row.template, row.flagged) for row in rows if row.template])
    ws = wb.create_sheet(LIST_SHEET)
    columns = (*HEAD_COLUMNS, *((name, _field_width(name)) for name in FIELDS), *TAIL_COLUMNS)
    ws.append([name for name, _ in columns])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    wrap = Alignment(wrap_text=True, vertical="top")
    for row in rows:
        values = [
            row.tdcc,
            row.status,
            row.issuer or None,
            "\n".join(row.reasons) or None,
            *(row.values.get(name) for name in FIELDS),
            "\n".join(f"{k}={v}" for k, v in row.others) or None,
            str(row.source) if row.source else None,
            row.sent_on,
        ]
        ws.append(values)
        r = ws.max_row
        ws.cell(r, 1).number_format = "@"  # TDCC 是文字，保留前導零
        for offset, name in enumerate(FIELDS, start=len(HEAD_COLUMNS) + 1):
            ws.cell(r, offset).number_format = _number_format(FIELDS[name])
        ws.cell(r, len(columns)).number_format = "yyyy-mm-dd hh:mm"
        for col in (4, len(HEAD_COLUMNS) + len(FIELDS) + 1):
            ws.cell(r, col).alignment = wrap
    for i, (_, width) in enumerate(columns, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "B2"

    raw = wb.create_sheet(RAW_SHEET)
    raw.append([name for name, _ in RAW_COLUMNS])
    for cell in raw[1]:
        cell.font = Font(bold=True)
    for row in rows:
        for entry, name in row.raw:
            raw.append(
                [
                    row.tdcc,
                    row.source.name if row.source else None,
                    entry.source,
                    entry.header,
                    entry.value,
                    name or None,
                ]
            )
            raw.cell(raw.max_row, 1).number_format = "@"
            raw.cell(raw.max_row, 5).number_format = "@"
    for i, (_, width) in enumerate(RAW_COLUMNS, start=1):
        raw.column_dimensions[get_column_letter(i)].width = width
    raw.freeze_panes = "A2"

    info = wb.create_sheet(INFO_SHEET)
    info.append(["根目錄", str(root)])
    info.append(["讀不到的資料夾", len(unreadable)])
    for folder in unreadable:
        info.append([None, str(folder)])
    info.column_dimensions["A"].width = 18
    info.column_dimensions["B"].width = 70
    wb.save(path)


def _number_format(kind: Kind) -> str:
    return {
        Kind.PERCENT: "0.00##",
        Kind.DATE: "yyyy-mm-dd",
        Kind.AMOUNT: "#,##0.##",
        Kind.MONTHS: "0",
    }.get(kind, "@")


def _field_width(name: str) -> int:
    return {Kind.DATE: 12, Kind.AMOUNT: 14}.get(FIELDS[name], max(10, len(name) * 2))
