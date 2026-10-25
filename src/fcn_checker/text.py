"""文字正規化：parser 與規則共用，不依賴任何一方。"""

from __future__ import annotations

import re
import unicodedata

CJK = re.compile(r"[\u4e00-\u9fff]")  # 中日韓統一表意文字（中文字）
_FULL_BRACKETS = str.maketrans({"(": "（", ")": "）"})


def squash(text: str) -> str:
    """移除所有空白（含換行）。"""
    return re.sub(r"\s+", "", text)


def full_brackets(text: str) -> str:
    """半形括號換成全形，比對時括號全半形不計。"""
    return text.translate(_FULL_BRACKETS)


def compat_standard(text: str) -> str:
    """CJK 相容字（U+F900–U+FAFF）轉成標準字（例：U+F9DD → 利、U+F98E → 年）；其他字元不動，字數不變。"""
    return "".join(unicodedata.normalize("NFKC", c) if 0xF900 <= ord(c) <= 0xFAFF else c for c in text)
