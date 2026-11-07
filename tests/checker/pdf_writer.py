"""合成說明書與投資人須知用的 PDF 寫入工具（不分上手），以及各家共用的改字 `Edit`。

各上手的合成器（tests/checker/*_synth.py）都只透過這裡的公開工具排版：`PdfWriter` 逐行寫字；`FlowDoc` 在其上
依段落代號改字、超寬自動換行，各家的差別（右邊界、行距、續行縮排、換行規則、章名寫法…）都寫在 `FlowLayout`。
第一章各條「N. 標題」同一段的寫法用 `FlowDoc.article`；其他條號寫法、封面兩欄表、頁尾等各家獨有的版面由各合成器自行寫成函式。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import fitz

FONT = "china-t"


def zh_date(d: dt.date) -> str:
    return f"{d.year} 年{d.month} 月{d.day} 日"


def _is_western(ch: str) -> bool:
    try:
        ch.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return True


def _runs(text: str) -> list[tuple[bool, str]]:
    out: list[tuple[bool, str]] = []
    for ch in text:
        w = _is_western(ch)
        if out and out[-1][0] == w:
            out[-1] = (w, out[-1][1] + ch)
        else:
            out.append((w, ch))
    return out


@dataclass(frozen=True)
class Edit:
    """合成文件的一處改字（製造錯誤用）：在段落代號落在 `where` 之內的文字單位裡，把 `old` 換成 `new`。

    段落代號是以「.」分層的路徑，第一層是文件：說明書 `ts`、投資人須知 `iis`（例：`ts.art17`、`iis.warn`）。
    `where` 以整層比對前綴：`ts.art1` 含 `ts.art1.x`，不含 `ts.art17`；空白表示兩份文件全文。
    各上手的段落代號見各合成器的模組說明。一行文字整個被換成空字串時，那一行不畫（不佔行距）。
    每個 `Edit` 至少要換到一處，否則合成器報錯（`require_used`），以免段落代號或原文打錯時測試白白通過。
    """

    old: str
    new: str
    where: str = ""

    def covers(self, path: str) -> bool:
        return not self.where or path == self.where or path.startswith(self.where + ".")


def require_used(edits: Sequence[Edit], used: set[Edit]) -> None:
    """合成完一份（或成對的兩份）文件後檢查：每個改字都至少換到一處。"""
    missing = [e for e in edits if e not in used]
    if missing:
        raise ValueError(f"改字沒有換到任何文字（原文或段落代號有誤）：{missing}")


class PdfWriter:
    """A4 由上而下逐行排版；超過頁底自動換頁。

    寫入的每個文字單位（`put`／`line`／`row` 一格／`para` 整段／`numbered` 標題）先套用 `edits` 中涵蓋
    目前段落（`kind`.`section`）的改字；合成器自行分行的文字先以 `edit` 整段改過，再在 `verbatim()` 內寫入。

    同一頁的文字共用一個 `Shape`，換頁或取用 `doc` 時才 commit（每段各自 `page.insert_text` 太慢）；
    合成器另外加字請經由 `doc`（例：`w.doc[0].insert_text`），不要直接寫 `page`，否則會排在尚未 commit 的內文之前。
    """

    TOP, BOTTOM = 80.0, 770.0

    def __init__(self, edits: Sequence[Edit] = (), kind: str = "ts", used: set[Edit] | None = None) -> None:
        self._doc = fitz.open()
        self._shape: fitz.Shape | None = None
        self.page: fitz.Page
        self.y = 0.0
        self.edits, self.kind = tuple(edits), kind  # kind：文件層段落代號（ts／iis）
        self.section = ""  # 目前段落代號（不含文件層）
        self.used = set() if used is None else used  # 換到過文字的改字（成對文件可共用同一個 set）
        self._verbatim = False
        self.new_page()

    def path(self, section: str | None = None) -> str:
        sec = self.section if section is None else section
        return f"{self.kind}.{sec}" if sec else self.kind

    def edit(self, text: str, section: str | None = None) -> str:
        """套用涵蓋段落 `section`（省略為目前段落）的改字；`verbatim()` 內不改。"""
        if self._verbatim:
            return text
        path = self.path(section)
        for e in self.edits:
            if e.covers(path) and e.old in text:
                text = text.replace(e.old, e.new)
                self.used.add(e)
        return text

    @contextmanager
    def verbatim(self) -> Iterator[None]:
        """區塊內寫入的文字不再改字（已先以 `edit` 整段改過、再由合成器自行分行的文字）。"""
        before, self._verbatim = self._verbatim, True
        try:
            yield
        finally:
            self._verbatim = before

    @property
    def doc(self) -> fitz.Document:
        """先 commit 目前頁尚未寫出的文字，再交出文件（合成器加頁尾、增刪頁時用）。"""
        self.flush()
        return self._doc

    def flush(self) -> None:
        if self._shape is not None:
            self._shape.commit()
            self._shape = None

    def shape(self) -> fitz.Shape:
        if self._shape is None:
            self._shape = self.page.new_shape()
        return self._shape

    def new_page(self) -> None:
        self.flush()  # 必須在換頁前 commit：留到最後才 commit 會因頁面已失效而失敗
        self.page = self._doc.new_page(width=595, height=842)
        self.y = self.TOP

    def need(self, h: float) -> None:
        if self.y + h > self.BOTTOM:
            self.new_page()

    def put(self, x: float, y: float, text: str, size: float = 10, section: str | None = None) -> None:
        """西文字元（Latin-1）用 Helvetica、其餘用 CJK 字型，逐段相接排版，避免全形寬度造成溢出或重疊。

        `section` 指定這個文字單位的段落代號（例：表格某一格），省略為目前段落。
        """
        for western, run in _runs(self.edit(text, section)):
            if western:
                self.shape().insert_text((x, y + size), run, fontname="helv", fontsize=size)
                x += fitz.get_text_length(run, fontname="helv", fontsize=size)
            else:
                self.shape().insert_text((x, y + size), run, fontname=FONT, fontsize=size)
                x += size * len(run)

    def line(self, x: float, text: str, size: float = 10, gap: float = 13) -> None:
        text = self.edit(text)
        if not text:
            return
        self.need(gap)
        with self.verbatim():
            self.put(x, self.y, text, size)
        self.y += gap

    def row(
        self, cells: Sequence[tuple[float, str] | tuple[float, str, str]], gap: float = 20, size: float = 10
    ) -> None:
        """一列表格；格子可帶第三個元素作為該格的段落代號。"""
        self.need(gap)
        for x, t, *section in cells:
            self.put(x, self.y, t, size, section[0] if section else None)
        self.y += gap

    def grid(
        self,
        header: Sequence[tuple[float, Sequence[str]]],
        rows: Sequence[Sequence[tuple[float, Sequence[str]]]],
        size: float = 8,
        gap: float = 10,
        space: float = 4,
        breaks: Sequence[int] = (),
    ) -> None:
        """一張表畫成真實欄列版面（價格表，Issue #288）：`header` 為表頭各格、`rows` 為每列各格，每格是（左緣 x、由上到下
        的幾行字）；同一列各格從同一個 y 起，一格拆幾行就往下幾行，列與列之間空 `space`。表頭與各列的格左緣對齊各欄，
        依座標讀表的 parser 才分得出欄。`breaks` 為在第幾列（1 起算）之前換頁。"""
        height = max(len(t) for _, t in header) * gap
        self.need(height + space + (max(len(t) for _, t in rows[0]) * gap if rows else 0))
        for x, texts in header:
            for i, t in enumerate(texts):
                self.put(x, self.y + i * gap, t, size)
        self.y += height + space
        for k, cells in enumerate(rows, start=1):
            height = max(len(t) for _, t in cells) * gap
            if k in breaks:
                self.new_page()
            self.need(height)
            for x, texts in cells:
                for i, t in enumerate(texts):
                    self.put(x, self.y + i * gap, t, size)
            self.y += height + space

    def para(self, x: float, text: str, width: int = 40, size: float = 10, gap: float = 13) -> None:
        text = self.edit(text)
        with self.verbatim():
            for k in range(0, len(text), width):
                self.line(x, text[k : k + width], size, gap)

    def numbered(self, n: str, x_num: float, x_body: float, title: str, gap: float = 20) -> None:
        self.need(gap)
        self.put(x_num, self.y, n)
        self.put(x_body, self.y, title)
        self.y += gap

    def space(self, h: float = 8) -> None:
        self.y += h

    def save(self, path: Path) -> Path:
        self.doc.save(path)
        self.doc.close()
        return path


def tokens(text: str) -> list[str]:
    """換行單位：連續的英數字（含符號）不拆開，空白、中文一字一個。"""
    return re.findall(r"[\x21-\x7e]+|\s|.", text)


def text_width(text: str, size: float) -> float:
    """`put` 排出來的寬度：西文字元以 Helvetica 量，其餘一字一個字級寬。"""
    return sum(fitz.get_text_length(c, fontname="helv", fontsize=size) if ord(c) < 256 else size for c in text)


Split = Callable[[str, float, float, float], list[str]]
"""換行規則：（文字, 首行可用寬, 續行可用寬, 字級）→ 各行文字。"""


def split_tokens(text: str, first: float, rest: float, size: float) -> list[str]:
    """依 `tokens` 換行（英數字串不拆開），換行處的空白丟掉、每行去頭尾空白。"""
    out: list[str] = []
    cur, used, width = "", 0.0, first
    for tok in tokens(text):
        tw = text_width(tok, size)
        if used + tw > width and cur.strip():
            out.append(cur.strip())
            cur, used, width = "", 0.0, rest
            if tok.isspace():
                continue
        cur += tok
        used += tw
    if cur.strip():
        out.append(cur.strip())
    return out


def split_digits(text: str, first: float, rest: float, size: float) -> list[str]:
    """逐字換行，只有數字（含「,」「.」）之間不拆開；每行原樣保留（不去空白）。"""
    out: list[str] = []
    cur, used, width = "", 0.0, first
    for ch in text:
        cw = text_width(ch, size)
        if used + cw > width and cur and not (cur[-1] in "0123456789,." and ch in "0123456789,."):
            out.append(cur)
            cur, used, width = "", 0.0, rest
        cur += ch
        used += cw
    out.append(cur)
    return out


@dataclass(frozen=True)
class FlowLayout:
    """一家說明書／投資人須知的流式排版參數；`doc()` 開一份新文件（可就該文件改幾個參數，例：投資人須知較寬）。"""

    right: float = 560  # 右邊界 x：每行可用寬 = right − 行首 x
    gap: float = 13  # `text` 行距
    size: float = 10  # `text` 字級
    indent: float = 0  # 續行縮排（`text` 沒給 `cont` 時，續行 x = 首行 x + indent）
    keep_width: bool = False  # 續行沿用首行的可用寬（不依續行 x 重算）
    split: Split = split_tokens  # 換行規則
    table_size: float = 8  # `centered_row` 預設字級
    cell_gap: float = 13  # `cell` 行距
    row_gap: float = 20  # `row` 預設列距
    chapter_fmt: str = "第{n}章、{name}"  # 章名一行的寫法；段落代號為 ch{n}
    chapter_x: float = 36
    chapter_gap: float = 16
    article_x: float = 36  # `article` 條號與標題一行的 x
    article_cont: float = 54  # `article` 標題換行時續行 x

    def doc(self, edits: Sequence[Edit], used: set[Edit] | None = None, kind: str = "ts", **changes: float) -> FlowDoc:
        return FlowDoc(edits, used, kind, replace(self, **changes) if changes else self)


class FlowDoc:
    """逐行排版；文字依目前段落代號（`section`，起始為 cover）套用改字，超過寬度時依 `FlowLayout.split` 換行。

    底層的 `PdfWriter` 是 `w`，各家獨有的版面（條號、兩欄表…）直接用 `w` 排。
    """

    def __init__(self, edits: Sequence[Edit], used: set[Edit] | None, kind: str, layout: FlowLayout) -> None:
        self.w = PdfWriter(edits, kind, used)
        self.layout = layout
        self.section = "cover"
        self.chapters: list[tuple[int, str]] = []  # （頁索引, 章名一行）

    @property
    def section(self) -> str:
        return self.w.section

    @section.setter
    def section(self, value: str) -> None:
        self.w.section = value

    def edit(self, text: str) -> str:
        return self.w.edit(text)

    def text(
        self, x: float, text: str, cont: float | None = None, *, gap: float | None = None, size: float | None = None
    ) -> None:
        """一段文字：先整段改字，再換行；首行 x 為 `x`、續行 x 為 `cont`（省略為 x + `indent`）。"""
        text = self.w.edit(text)
        if not text:
            return
        lay = self.layout
        gap = lay.gap if gap is None else gap
        size = lay.size if size is None else size
        nxt = x + lay.indent if cont is None else cont
        first = lay.right - x
        lines = lay.split(text, first, first if lay.keep_width else lay.right - nxt, size)
        with self.w.verbatim():
            for k, part in enumerate(lines):
                self.w.line(nxt if k else x, part, size=size, gap=gap)

    def cell(self, x: float, text: str) -> None:
        """一行（表格一格）不換行；整格換成空字串時不畫。"""
        self.w.line(x, text, gap=self.layout.cell_gap)

    def row(self, cells: Sequence[tuple[float, str]], gap: float | None = None, size: float | None = None) -> None:
        """一列表格（不置中）；列距、字級省略為 `row_gap`、`size`。"""
        lay = self.layout
        self.w.row(cells, gap=lay.row_gap if gap is None else gap, size=lay.size if size is None else size)

    def centered_row(self, cells: Sequence[tuple[float, str]], gap: float, size: float | None = None) -> None:
        """一列表格，每格置中於欄中心 x。"""
        size = self.layout.table_size if size is None else size
        self.w.row([(cx - text_width(t, size) / 2, t) for cx, t in cells], gap=gap, size=size)

    def chapter(self, n: str, name: str) -> None:
        """章名一行（新頁）；段落代號 ch{n}。"""
        lay = self.layout
        self.w.new_page()
        self.section = f"ch{n}"
        title = lay.chapter_fmt.format(n=n, name=name)
        self.chapters.append((len(self.w.doc) - 1, title))
        self.w.line(lay.chapter_x, title, gap=lay.chapter_gap)

    def enter_article(self, n: int, first_chapter: str = "ch一") -> None:
        """進入第一章第 `n` 條：目前段落在第一章內（封面、第一章章名 `first_chapter`、前一條或其下層）時段落代號改為
        art{n}；其他章的條沿用章的代號（ch二…）。"""
        s = self.section
        if s == "cover" or s.startswith("art") or s == first_chapter:
            self.section = f"art{n}"

    def article(self, n: int, title: str) -> None:
        """一條「{n}. {title}」寫成一段（x `article_x`、續行 `article_cont`）；段落代號見 `enter_article`。"""
        self.enter_article(n)
        self.text(self.layout.article_x, f"{n}. {title}", self.layout.article_cont)

    def number_pages(
        self, label: str, at: tuple[float, float], *, fontname: str = FONT, size: float = 9
    ) -> fitz.Document:
        """每頁加頁碼 `label`（`{n}` 第幾頁、`{m}` 總頁數；真實文件的頁碼，擷取時排除），交出文件。"""
        doc = self.w.doc
        for p, page in enumerate(doc):
            page.insert_text(at, label.format(n=p + 1, m=len(doc)), fontname=fontname, fontsize=size)
        return doc
