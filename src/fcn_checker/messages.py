"""錯訊：作業人員看到的每條問題，都由這裡產生一段看得懂的中文（Issue #71）。

只寫哪裡對不起來、兩邊各是多少，不判定是說明書錯還是參考條件表錯，也不出現 rule_id、reason_code 等程式代碼。
每條核對結果建立時就帶著項目（`Item`：中文名稱與預期值出處，Issue #91），這裡只依出處組句：

- 參考條件表且兩邊不同：「<項目>對不起來：參考條件表 <值>／說明書 <值>」；多格（例：比價日）逐格列出 Excel 欄名。
- 投資人須知：文件那一邊寫「投資人須知」（結果記下的文件種類 `CheckResult.document`）；預期值來自同商品說明書且兩邊不同時寫
  「<項目>對不起來：說明書 <值>／投資人須知 <值>」（ADR 0007）。
- 其他（含由參考條件表推算的值）：「<項目>：<規則的中文說明>」，有雙方值時附上「（參考條件表／審查標準／預期 <值>／說明書 <值>）」；
  不比對值的項目（配對、範本、讀檔、寫檔、參考條件表表頭）只寫說明。
- 規則沒寫說明時，依原因與狀態給中文預設。

核對結果檔「錯誤清單」的錯訊與 PANEL 結果明細都用 `problem_message`；錯誤清單另用 `problems` 帶每句錯訊的證據頁碼（Issue #342）
與「應改為」（`fix_message`，Issue #339）。

「應改為」只在對的值有明確來源時寫：不一致、且預期值出自審查標準或說明書其他位置的文字或日期（規則另給 `CheckResult.fix` 時用它）。
預期值出自參考條件表或同商品說明書的不寫：兩邊誰錯要人判斷，錯訊已列出兩邊的值。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from decimal import Decimal
from typing import Any, NamedTuple

from .schema import CheckResult, CheckStatus, DocKind, ItemSource, column_label
from .text import diff_parts, squash

STATUS_ZH = {
    CheckStatus.PASS: "通過",
    CheckStatus.MISMATCH: "不一致",
    CheckStatus.REVIEW_REQUIRED: "需人工覆核",
    CheckStatus.NOT_APPLICABLE: "不適用",
    CheckStatus.ERROR: "執行錯誤",
}

# 雙方值裡另一邊的稱呼
SOURCE_ZH = {
    ItemSource.REFERENCE: "參考條件表",
    ItemSource.REFERENCE_DERIVED: "參考條件表",
    ItemSource.STANDARD: "審查標準",
    ItemSource.EXPECTED: "預期",
    ItemSource.TERM_SHEET: DocKind.TERM_SHEET.value,
}

REASON_ZH = {
    "value_mismatch": "兩邊的值不同",
    "document_inconsistent": "說明書不同地方的值不一致",
    "document_missing": "說明書抓不到此欄位",
    "document_ambiguous": "說明書出現多個不同的值",
    "document_invalid": "說明書的值無法辨識",
    "date_order": "日期順序不對",
    "period_count": "期數不同",
    "occurrence_count": "出現次數不對",
    "split_suspected": "同一檔標的各價格都差同一比例，疑為股票分割調整後的價格",
    "non_call_product": "說明書推得的 Non-Call 不符合參考條件表的商品種類",
    "unexpected_error": "執行時發生非預期錯誤",
}
STATUS_DEFAULT = {
    CheckStatus.MISMATCH: "兩邊的值不同",
    CheckStatus.REVIEW_REQUIRED: "需要人工確認",
    CheckStatus.ERROR: "執行錯誤",
}
MAX_VALUE = 80  # 值太長（例：整段固定警語）只顯示前段，完整值見核對紀錄
SHORT_TEXT = 30  # 「應改為」：去空白後這麼長以內的文字直接寫完整的正確值，更長的只列差異片段
MAX_FIX_PARTS = 5  # 差異片段超過這麼多處（幾乎整段不同）時改寫完整的正確值
FIX_SOURCES = (ItemSource.STANDARD, ItemSource.EXPECTED)  # 「應改為」只寫這兩種出處的預期值


def show(v: Any) -> str:
    """值的顯示：日期 ISO、清單以頓號分隔、None 為「—」；太長的值截斷。"""
    if v is None or v == "":
        text = "—"
    elif isinstance(v, bool):
        text = "是" if v else "否"
    elif isinstance(v, (dt.date, Decimal, int, float)):
        text = v.isoformat() if isinstance(v, dt.date) else str(v)
    elif isinstance(v, (list, tuple)):
        text = "、".join(show(x) for x in v)
    elif isinstance(v, dict):
        text = "；".join(f"{k}：{show(x)}" for k, x in v.items())
    else:
        text = str(v)
    text = text.replace("\n", " ")
    return text if len(text) <= MAX_VALUE else text[:MAX_VALUE] + "…"


def _simple(v: Any) -> bool:
    """可以直接列出的值（文字、數字、日期及其清單）；表格列（dict）、排程等結構不列，避免出現內部欄位代碼。"""
    if isinstance(v, (list, tuple)):
        return all(_simple(x) for x in v)
    return v is None or isinstance(v, (str, int, float, Decimal, dt.date))


def _shows_values(r: CheckResult, detail: str) -> bool:
    """不比對值的項目，以及說明裡已寫出雙方值的，不另附值。"""
    if r.item.source == ItemSource.NONE:
        return False
    if not all(_simple(v) for v in (r.expected, r.actual)):
        return False
    return not all(show(v) in detail for v in (r.expected, r.actual))


def _detail(r: CheckResult) -> str:
    default = REASON_ZH.get(r.reason_code) or STATUS_DEFAULT.get(r.status, "需要人工確認")
    return r.message or default.replace(DocKind.TERM_SHEET.value, r.document.value)


def problem_message(r: CheckResult) -> str:
    """一條問題的中文錯訊；文件那一邊依結果記下的文件種類（說明書或投資人須知）稱呼。"""
    where, source, document = r.item.name, r.item.source, r.document.value
    if source in (ItemSource.REFERENCE, ItemSource.TERM_SHEET) and r.status == CheckStatus.MISMATCH:
        other = SOURCE_ZH[source]
        if isinstance(r.expected, dict) and isinstance(r.actual, dict):  # 多格（例：比價日）逐格列出
            return "；".join(
                f"{column_label(str(k))}對不起來：{other} {show(v)}／{document} {show(r.actual.get(k))}"
                for k, v in r.expected.items()
            )
        return f"{where}對不起來：{other} {show(r.expected)}／{document} {show(r.actual)}"
    detail = _detail(r)
    has_both = r.expected is not None and r.actual is not None
    values = ""
    if has_both and _shows_values(r, detail):
        values = f"（{SOURCE_ZH[source]} {show(r.expected)}／{document} {show(r.actual)}）"
    head = "" if detail.startswith(where) else f"{where}："  # 說明已經以項目開頭時不重複
    return f"{head}{detail}{values}"


def fix_message(r: CheckResult) -> str:
    """錯誤清單「應改為」：不一致、且預期值出自審查標準或說明書其他位置時，寫出正確的文字或日期；其他空白。

    規則另給 `CheckResult.fix` 時用它；文字見 `text_fix`，日期寫 ISO；數字（頁數、次數、金額）與表格等結構不寫。
    """
    if r.status != CheckStatus.MISMATCH:
        return ""
    if r.fix:
        return r.fix
    if r.item.source not in FIX_SOURCES:
        return ""
    if isinstance(r.expected, dt.date):
        return r.expected.isoformat()
    if isinstance(r.expected, str) and r.expected.strip():
        return text_fix(r.expected, r.actual if isinstance(r.actual, str) else "")
    return ""


def text_fix(want: str, have: str) -> str:
    """把 `have` 改成 `want` 要怎麼改：短文字（或沒有原文可比）寫完整的 `want`；長文字每處差異一行
    「…原文片段… → …改後片段…」（忽略空白，前後帶幾個字方便在 PDF 找到），差異太多處或幾乎整段不同時寫完整的 `want`。"""
    whole = " ".join(want.split())
    if len(squash(want)) <= SHORT_TEXT or not have.strip():
        return whole
    parts = diff_parts(squash(have), squash(want))
    if not parts or len(parts) > MAX_FIX_PARTS:
        return whole
    return "\n".join(f"…{old}… → …{new}…" for old, new in parts)


class Problem(NamedTuple):
    """一句錯訊與它的證據頁碼（由小到大，沒有證據時為空）及「應改為」（沒有時空白）。"""

    message: str
    pages: tuple[int, ...]
    fix: str = ""


def problems(results: Iterable[CheckResult]) -> tuple[Problem, ...]:
    """有問題的結果的錯訊：同一句只列一次（依第一次出現的順序），各結果的證據頁碼合併，「應改為」去重後每段一行。"""
    pages: dict[str, set[int]] = {}
    fixes: dict[str, list[str]] = {}
    for r in results:
        if r.status.is_problem:
            message = problem_message(r)
            pages.setdefault(message, set()).update(e.page for e in r.document_evidence)
            found = fixes.setdefault(message, [])
            if (fix := fix_message(r)) and fix not in found:
                found.append(fix)
    return tuple(Problem(message, tuple(sorted(p)), "\n".join(fixes[message])) for message, p in pages.items())
