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
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pymupdf

TITLE_WINDOW = 300
_WHITESPACE = re.compile(r"\s+")
_TWELVE_DIGITS = re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])")  # 只認半形數字


class DocKind(Enum):
    TS = "TS"
    IIS = "IIS"

    @property
    def label(self) -> str:
        return "中文產品說明書" if self is DocKind.TS else "中文投資人須知"


@dataclass(frozen=True)
class Reading:
    """一份 PDF 第一頁的判讀：依標題是哪一種文件（不是說明書或須知時為 None），以及商品代號是否符合資料夾。"""

    kind: DocKind | None
    code_matches: bool = False

    @property
    def candidate(self) -> DocKind | None:
        return self.kind if self.code_matches else None


def first_page_text(path: Path) -> str | None:
    """PDF 第一頁的文字；損毀、加密或沒有頁面時回傳 None。"""
    try:
        with pymupdf.open(path) as doc:
            if doc.needs_pass or doc.page_count == 0:
                return None
            return doc[0].get_text()
    except Exception:  # PyMuPDF 對損毀檔丟出的例外型別不固定
        return None


def classify(text: str, tdcc: str) -> Reading:
    """判讀第一頁文字對這個 TDCC 而言是哪一種文件、商品代號符不符合。"""
    compact = _WHITESPACE.sub("", text)
    head = compact[:TITLE_WINDOW]
    found = {kind: head.find(kind.label) for kind in DocKind if kind.label in head}
    if not found:
        return Reading(None)
    kind = min(found, key=found.__getitem__)
    if tdcc in compact:
        return Reading(kind, True)
    # 換行可能把代號切開，原始文字與去空白文字都要找
    has_other_code = bool(_TWELVE_DIGITS.search(text) or _TWELVE_DIGITS.search(compact))
    return Reading(kind, kind is DocKind.IIS and not has_other_code)


def read_pdf(path: Path, tdcc: str) -> Reading:
    text = first_page_text(path)
    return Reading(None) if text is None else classify(text, tdcc)
