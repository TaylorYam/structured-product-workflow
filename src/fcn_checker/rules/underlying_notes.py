"""連結標的公司介紹（Issue #225）：說明書「連結標的之相關說明」每一檔標的都有介紹，而且不是亂碼。

- `doc.underlying_notes`：介紹段數 = 標的檔數（`underlyings`）。少了判不一致：每段都寫了代碼時寫出缺哪幾檔，否則列出
  找到的段落；多了（可能切段不對）轉人工覆核。
- `doc.underlying_note`：每段一筆。疑似抽字失敗（U+FFFD、私用區字元、`(cid:`、中文字比例偏低）轉人工覆核；
  沒有一句以句號結尾、至少 8 個中文字的敘述（只有名稱、代碼）判不一致。介紹寫得對不對不核對。
"""

from __future__ import annotations

import re

from ..parsers.underlying_notes import UnderlyingNote
from ..schema import CheckResult, Item
from ..schema import CheckStatus as S
from .kit import Check, Context, standard_field

_CJK = re.compile(r"[一-鿿]")
_BROKEN = re.compile(r"[�-]|\(cid:")
MIN_SENTENCE = 8  # 一句敘述至少的中文字數
MIN_CJK_RATIO = 0.3  # 中文字占全段（去空白）的最低比例，低於此疑似亂碼


def _head(note: UnderlyingNote) -> str:
    return note.text[:16]


def garbled(text: str) -> bool:
    return bool(_BROKEN.search(text)) or len(_CJK.findall(text)) < MIN_CJK_RATIO * len(text)


def has_sentence(text: str) -> bool:
    """至少一句以「。」結尾、中文字 ≥ `MIN_SENTENCE` 的敘述。"""
    return any(len(_CJK.findall(s)) >= MIN_SENTENCE for s in text.split("。")[:-1])


def run(ctx: Context) -> list[CheckResult]:
    pf, uls = standard_field(ctx, "underlying_notes"), standard_field(ctx, "underlyings")
    count = Check("doc.underlying_notes", "underlying_notes", Item.expected("連結標的公司介紹段數"), ctx.document)
    count = count.needs(pf, uls)
    if not pf.ok:
        return [count.review(pf)]
    notes: tuple[UnderlyingNote, ...] = pf.value
    return [_count(count, notes, uls.value) if uls.ok else count.review(uls), *_contents(ctx, notes)]


def _missing(notes: tuple[UnderlyingNote, ...], uls: list[str]) -> list[str] | None:
    """沒有介紹的標的：每段都寫了某檔的代碼（不含交易所尾碼）才對得出來，否則 None。"""
    roots = {u: u.split()[0] for u in uls}
    if not all(any(r in x.text for r in roots.values()) for x in notes):
        return None
    missing = [u for u, r in roots.items() if not any(r in x.text for x in notes)]
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


def _contents(ctx: Context, notes: tuple[UnderlyingNote, ...]) -> list[CheckResult]:
    out = []
    for i, note in enumerate(notes, 1):
        check = Check("doc.underlying_note", "underlying_notes", Item.expected(f"第 {i} 段標的公司介紹"), ctx.document)
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
