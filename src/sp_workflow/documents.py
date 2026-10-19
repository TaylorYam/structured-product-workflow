"""看 PDF 第一頁內容判斷是不是某商品的中文產品說明書（TS）或中文投資人須知（IIS）。

規則（#5、#7；以 12 個真實商品資料夾驗證）：

- 第一頁文字去掉所有空白後，「中文產品說明書」與「中文投資人須知」哪個先出現就是哪一種；
  標題必須出現在前 `TITLE_WINDOW` 字內（高齡聲明書第一頁約第 600 字才提到兩者，真實樣本標題最晚在第 148 字）。
- 說明書：第一頁一定要有資料夾名稱的 TDCC。
- 投資人須知：第一頁有資料夾的 TDCC，或完全沒有 12 碼代號（UBS、HSBC 整份沒有 TDCC）才算；有別的 12 碼代號不算。
- 讀不到、判斷不了一律不是候選，不猜。
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

import pymupdf

TITLE_WINDOW = 300
_WHITESPACE = re.compile(r"\s+")
_TWELVE_DIGITS = re.compile(r"(?<!\d)\d{12}(?!\d)")


class DocKind(Enum):
    TS = "TS"
    IIS = "IIS"

    @property
    def label(self) -> str:
        return "中文產品說明書" if self is DocKind.TS else "中文投資人須知"


def first_page_text(path: Path) -> str | None:
    """PDF 第一頁的文字；損毀、加密或沒有頁面時回傳 None。"""
    try:
        with pymupdf.open(path) as doc:
            if doc.needs_pass or doc.page_count == 0:
                return None
            return doc[0].get_text()
    except Exception:  # PyMuPDF 對損毀檔丟出的例外型別不固定
        return None


def classify(text: str, tdcc: str) -> DocKind | None:
    """第一頁文字屬於這個 TDCC 的哪一種文件；都不是時回傳 None。"""
    compact = _WHITESPACE.sub("", text)
    head = compact[:TITLE_WINDOW]
    found = {kind: head.find(kind.label) for kind in DocKind if kind.label in head}
    if not found:
        return None
    kind = min(found, key=found.__getitem__)
    if tdcc in compact:
        return kind
    if kind is DocKind.IIS and not _TWELVE_DIGITS.search(text):
        return kind
    return None


def classify_pdf(path: Path, tdcc: str) -> DocKind | None:
    text = first_page_text(path)
    return None if text is None else classify(text, tdcc)
