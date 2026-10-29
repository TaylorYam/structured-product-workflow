"""文字正規化：parser 與規則共用，不依賴任何一方。"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import NamedTuple

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


ANCHOR = 6  # 找相近段落時，目標每這麼多字一段，用各段在全文的位置投票決定對齊點
CONTEXT = 6  # 差異片段前後各帶的字數
SIMILAR = 0.6  # 相似度（difflib ratio）達到這麼多才算同一段文字被改過，否則視為不同的文字


def nearest(text: str, target: str, *, skip: Sequence[tuple[int, int]] = ()) -> Near | None:
    """全文 `text` 裡和 `target` 最接近的一段（例：被改了幾個字的固定警語）；沒有相似度達 SIMILAR 的段落時 None。

    `target` 每 ANCHOR 字一段，各段在全文出現的位置推得的起點投票，取票數最多的幾個起點，在附近找和 `target` 的相符範圍；
    頭尾沒對上的字數照 `target` 往外補（頭尾的字被改時也包含在內）。和 `skip`（例：已逐字相符的段落）重疊的不算。
    """
    votes: Counter[int] = Counter()
    for p in range(0, len(target) - ANCHOR + 1, ANCHOR):
        chunk, at = target[p : p + ANCHOR], text.find(target[p : p + ANCHOR])
        while at != -1:
            votes[at - p] += 1
            at = text.find(chunk, at + 1)
    margin, best = len(target) // 4, None
    for offset, count in votes.most_common(3):
        if count < 2 or any(offset < e and s < offset + len(target) for s, e in skip):
            continue
        lo, hi = max(0, offset - margin), min(len(text), offset + len(target) + margin)
        blocks = [b for b in SequenceMatcher(None, text[lo:hi], target, autojunk=False).get_matching_blocks() if b.size]
        first, last = blocks[0], blocks[-1]
        start = max(lo, lo + first.a - first.b)
        end = min(hi, lo + last.a + last.size + len(target) - last.b - last.size)
        ratio = SequenceMatcher(None, text[start:end], target, autojunk=False).ratio()
        if ratio >= SIMILAR and (best is None or ratio > best.ratio):
            best = Near(start, end, ratio)
    return best


class Near(NamedTuple):
    """`nearest` 找到的段落：全文的起訖位置與相似度（0～1）。"""

    start: int
    end: int
    ratio: float


def diff_parts(have: str, want: str) -> list[tuple[str, str]]:
    """`have` 要改成 `want` 的地方，每處一組（原文片段, 改後片段），前後各帶 CONTEXT 個沒變的字；相鄰的差異合成一處。
    兩段相似度不到 SIMILAR（不是同一段被改過）時沒有片段。"""
    matcher = SequenceMatcher(None, have, want, autojunk=False)
    if matcher.ratio() < SIMILAR:
        return []
    groups: list[list[int]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if groups and i1 - groups[-1][1] <= 2 * CONTEXT:
            groups[-1][1], groups[-1][3] = i2, j2
        else:
            groups.append([i1, i2, j1, j2])
    out = []
    for i1, i2, j1, j2 in groups:
        before, after = have[max(0, i1 - CONTEXT) : i1], have[i2 : i2 + CONTEXT]
        out.append((before + have[i1:i2] + after, before + want[j1:j2] + after))
    return out
