"""連結標的公司介紹（Issue #225）：說明書「連結標的之相關說明」每一檔標的都有介紹，而且不是亂碼。

- `doc.underlying_notes`：介紹段數 = 標的檔數（`underlyings`）。少了判不一致：每段都寫了代碼時寫出缺哪幾檔，否則列出
  找到的段落；多了（可能切段不對）轉人工覆核。
- `doc.underlying_note`：每段一筆。疑似抽字失敗（U+FFFD、私用區字元、`(cid:`、中文字比例偏低）轉人工覆核；
  沒有一句以句號結尾、至少 8 個中文字的敘述（只有名稱、代碼）判不一致。介紹寫得對不對不核對。
"""

from __future__ import annotations

import re

from ..schema import CheckResult
from ..schema import CheckStatus as S
from ..standard_fields import UnderlyingNote
from ..text import CJK, squash
from .kit import Check, IssuerContext, doc_check, standard_field

_BROKEN = re.compile(r"[\ufffd\ue000-\uf8ff]|\(cid:")  # U+FFFD、私用區字元、pdfminer 認不出的字
MIN_SENTENCE = 8  # 一句敘述至少的中文字數
MIN_CJK_RATIO = 0.3  # 中文字占全段（去空白）的最低比例，低於此疑似亂碼


def _head(note: UnderlyingNote) -> str:
    return note.text[:16]


def garbled(text: str) -> bool:
    return bool(_BROKEN.search(text)) or len(CJK.findall(text)) < MIN_CJK_RATIO * len(text)


def has_sentence(text: str) -> bool:
    """至少一句以「。」結尾、中文字 ≥ `MIN_SENTENCE` 的敘述。"""
    return any(len(CJK.findall(s)) >= MIN_SENTENCE for s in text.split("。")[:-1])


def run(ctx: IssuerContext) -> list[CheckResult]:
    pf, uls = standard_field(ctx, "underlying_notes"), standard_field(ctx, "underlyings")
    count = doc_check(ctx, "doc.underlying_notes", "underlying_notes", "連結標的公司介紹段數", pf, uls)
    if not pf.ok:
        return [count.review(pf)]
    notes: tuple[UnderlyingNote, ...] = pf.value
    return [_count(count, notes, uls.value) if uls.ok else count.review(uls), *_contents(ctx, notes)]


def _missing(notes: tuple[UnderlyingNote, ...], uls: list[str]) -> list[str] | None:
    """沒有介紹的標的：每段都寫了某檔的彭博代碼（段落已去空白：`ABCUW`，前面不接英數字）才對得出來，否則 None。"""
    codes = {u: re.compile(rf"(?<![A-Za-z0-9]){re.escape(squash(u))}") for u in uls}
    if not all(any(c.search(x.text) for c in codes.values()) for x in notes):
        return None
    missing = [u for u, c in codes.items() if not any(c.search(x.text) for x in notes)]
    return missing if len(missing) == len(uls) - len(notes) else None


def _count(count: Check, notes: tuple[UnderlyingNote, ...], uls: list[str]) -> CheckResult:
    n, k = len(uls), len(notes)
    shown = "、".join(f"「{_head(x)}…」" for x in notes) or "無"
    missing = _missing(notes, uls) if 0 < k < n else None
    if k > n:
        return count.result(
            S.REVIEW_REQUIRED,
            expected=n,
            actual=k,
            reason="note_count",
            message=f"公司介紹 {k} 段多於標的 {n} 檔，請人工確認段落：{shown}",
        )
    fail = f"缺 {'、'.join(missing)}" if missing else shown
    return count.compare(n, k, reason="note_count", fail_message=f"標的 {n} 檔、公司介紹只有 {k} 段：{fail}")


def _contents(ctx: IssuerContext, notes: tuple[UnderlyingNote, ...]) -> list[CheckResult]:
    out = []
    for i, note in enumerate(notes, 1):
        check = doc_check(ctx, "doc.underlying_note", "underlying_notes", f"第 {i} 段標的公司介紹")
        if garbled(note.text):
            out.append(
                check.result(
                    S.REVIEW_REQUIRED,
                    expected="中文敘述",
                    actual=_head(note),
                    reason="garbled_text",
                    message=f"第 {i} 段公司介紹疑似亂碼或抽字失敗，請人工確認",
                    evidence=note.evidence,
                )
            )
            continue
        out.append(
            check.compare(
                "中文敘述",
                _head(note),
                ok=has_sentence(note.text),
                evidence=note.evidence,
                reason="note_empty",
                fail_message=f"第 {i} 段只有名稱或代碼，沒有公司介紹",
                tolerance=f"至少一句以「。」結尾、{MIN_SENTENCE} 個中文字以上的敘述；內容正確性不核對",
            )
        )
    return out
