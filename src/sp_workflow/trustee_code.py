"""轉檔前補上 MS 中文產品說明書封面空白的「受託機構商品代號：」（#56）。

MS 的說明書 Word 封面「商品代號：<TDCC>」有填，下一行「受託機構商品代號：」固定空白；核對工具的 MS 規則
要求它等於封面商品代號、空白判不一致，其他上手這格填的也都是 TDCC。所以轉 PDF 前在暫存複本補上 TDCC。

- 只改暫存複本的 `word/document.xml`，商品資料夾的原始 Word 不動；只處理 `.docx`（MS 只給 `.docx`）。
- 只補「受託機構商品代號：」後面空白的段落；已有值不動（不一致交給核對工具判）。
- 用字串處理 XML、不重新序列化：Word 的命名空間前綴（`mc:Ignorable` 會引用）必須原樣保留。
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

FILL_ISSUERS = frozenset({"147"})  # TDCC 前三碼：MS
LABELS = ("受託機構商品代號：", "受託機構商品代號:")
_DOCUMENT = "word/document.xml"
_PARAGRAPH = re.compile(r"<w:p[ >].*?</w:p>", re.DOTALL)  # `<w:p ` 或 `<w:p>`，不會吃到 `<w:pPr>`
_TEXT = re.compile(r"(<w:t(?:\s[^>]*)?>)([^<]*)(</w:t>)")
_WHITESPACE = re.compile(r"\s+")


def needs_fill(tdcc: str, word: Path) -> bool:
    """這家上手的說明書 Word 要不要補受託機構商品代號。"""
    return tdcc[:3] in FILL_ISSUERS and word.suffix.lower() == ".docx"


def fill_trustee_code(path: Path, tdcc: str) -> bool:
    """在 path（暫存複本）空白的「受託機構商品代號：」後面補上 TDCC；有補回傳 True。不是 .docx 或沒有這一格時不動。"""
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            data = {info.filename: zf.read(info) for info in infos}
        xml = data[_DOCUMENT].decode("utf-8")
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError):
        return False
    filled = _PARAGRAPH.sub(lambda m: _fill_paragraph(m.group(0), tdcc), xml)
    if filled == xml:
        return False
    data[_DOCUMENT] = filled.encode("utf-8")
    staging = path.with_name(path.name + ".filling")
    with zipfile.ZipFile(staging, "w") as zf:
        for info in infos:  # 原本的順序與壓縮方式照舊
            zf.writestr(info, data[info.filename])
    staging.replace(path)
    return True


def _fill_paragraph(paragraph: str, tdcc: str) -> str:
    """整段文字（去空白）剛好是標籤時，把 TDCC 接在最後一段文字後面；其他段落原樣回傳。"""
    texts = list(_TEXT.finditer(paragraph))
    if not texts or _WHITESPACE.sub("", "".join(t.group(2) for t in texts)) not in LABELS:
        return paragraph
    last = texts[-1]
    return paragraph[: last.end(2)] + tdcc + paragraph[last.end(2) :]
