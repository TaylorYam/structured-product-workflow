"""回填欄位（TS、IIS、ISIN Code、發行日、比價日_1～12；期初定價 VWAP 時另加各標的價格欄）的整段流程，所有上手共用（ADR 0008）。

- 每格決策：表上空白 → 回填；已有相同值（含空值寫法）→ 相同；已有不同值 → 不一致，保留原值。
- TS、IIS 兩欄的值不取自說明書，一律是打勾寫法（`checked_value`）：寫入就代表兩份都通過或人工放行（Issue #127）。
- 期初定價為 VWAP 的商品，各標的四個價格欄也是回填欄位，但已有不同值時改為覆寫（Issue #122）。
- 只寫入呼叫端交來的說明書：批量入口依每份的類別決定（整份 PASS 或人工放行，`BatchItem.fillable`）。
- 每個決策帶一個目標（`backfill_values.Target`，標準欄位）；寫進「回填後」的決策由 `values` 轉成型別化的回填值（#444），
  資料庫寫回照值寫表。核對結果檔（result_file.py，Excel adapter）照決策的格子排版，參考條件表原檔不動。
- 決策的顯示標籤（`BackfillAction.label`）只在這裡定義，PANEL 使用。

比價日填法：

- D（期間每日觀察）：填 `比價日_{Non-Call}`（開始）與 `比價日_{期數}`（最後一期），其他格填空值寫法；
  Non-Call 等於期數時只有一格。
- P（每期定日觀察）：`比價日_{Non-Call}`～`比價日_{期數}` 每格填該期比價日，其他格填空值寫法。
- 填出來最晚的比價日必須等於說明書的最終比價日，否則轉人工覆核、不回填。

格式設定沒有回填欄位的欄名、或參考條件表缺少該欄時，回填規則轉人工覆核並寫出缺的欄名，不產生決策。
回填規則的輸入與其他共用規則相同（`Context`）：表上的值、Excel 欄名與儲存格位置都從 `ctx.order` 讀。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from .backfill_values import (
    AUTOCALL_SLOTS,
    CHECKED_MARKS,
    ISIN,
    ISSUE_DATE,
    LOOKBACK_END_DATE,
    SLOTS,
    BackfillValue,
    CheckedMark,
    ProductTerm,
    Target,
    UnderlyingPrice,
)
from .fields import PRICE_COLUMNS
from .orders.reference import OrderRecord, price_key
from .rules.kit import Context, doc_review, result
from .rules.reference import is_vwap, sheet_slots, standard_field
from .schema import CheckResult, FieldStatus, Item, OrderValue, ParsedField
from .schema import CheckStatus as S
from .standard_fields import AutocallSchedule, not_provided

COLUMN_MISSING = "backfill_column_missing"
COMPARE_DATES = "比價日"  # 比價日_1～12 合起來核對，項目用這個名稱


class BackfillAction(StrEnum):
    FILL = "fill"  # 表上空白，整份核對通過後回填
    MATCH = "match"  # 已有相同值
    MISMATCH = "mismatch"  # 已有不同值，保留原值
    OVERWRITE = "overwrite"  # 期初定價 VWAP 的價格欄已有不同值，整份核對通過後以說明書覆寫

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS = {
    BackfillAction.FILL: "空白，核對通過後回填",
    BackfillAction.MATCH: "相同",
    BackfillAction.MISMATCH: "不一致，保留原值",
    BackfillAction.OVERWRITE: "VWAP，核對通過後以說明書覆寫",
}
WRITE_ACTIONS = (BackfillAction.FILL, BackfillAction.OVERWRITE)  # 儲存時寫進「回填後」的決策
PRICES = "各標的價格"  # VWAP 價格欄合起來核對，項目用這個名稱
CHECKED_FIELDS = tuple(m.field for m in CHECKED_MARKS)  # TS、IIS 欄


@dataclass(frozen=True)
class CellDecision:
    """回填欄位的一格。"""

    column: str  # Excel 欄名（畫面與回填紀錄給人看）
    cell: str  # 儲存格位置，例 F4（核對結果檔與畫面）
    sheet_value: Any
    expected: Any  # 日期、空值寫法或 ISIN
    action: BackfillAction
    target: Target | None = None  # 寫到哪個標準欄位（#444）

    @property
    def field(self) -> str | None:
        """標準欄位（例 `isin`、`autocall_date_3`）。"""
        return None if self.target is None else self.target.field


def values(decisions: Sequence[CellDecision], empty: str) -> tuple[BackfillValue, ...]:
    """要寫進「回填後」的決策（回填與 VWAP 覆寫）轉成回填值，順序不變；空值寫法 `empty` 轉成 None。
    TS、IIS 打勾不是回填值（資料庫改成鎖住）。"""
    return tuple(
        BackfillValue(d.target, None if d.expected == empty else d.expected, d.column)
        for d in decisions
        if d.action in WRITE_ACTIONS and d.target is not None and not isinstance(d.target, CheckedMark)
    )


# ---------------------------------------------------------------- 每格決策


def _missing_columns(row: OrderRecord, keys: Sequence[str]) -> list[str]:
    out = []
    for key in keys:
        header = row.header(key)
        if header is None:
            out.append(f"參考條件表格式設定沒有標準欄位 {key} 的欄名")
        elif not row.has(key):
            out.append(f"參考條件表缺少「{header}」欄")
    return out


def _column_missing(rid: str, key: str, missing: list[str], ovs: list[OrderValue | None], item: Item) -> CheckResult:
    message = "無法回填：" + "；".join(missing)
    return result(rid, key, S.REVIEW_REQUIRED, ov=ovs, reason=COLUMN_MISSING, message=message, item=item)


def _decide(row: OrderRecord, target: Target, expected: Any) -> CellDecision:
    """呼叫前已確認格式設定與參考條件表都有這一欄。"""
    key = target.field
    ov = row.get(key)
    value = ov.value if ov else None
    if value is None:
        action = BackfillAction.FILL
    elif value == expected:
        action = BackfillAction.MATCH
    else:
        action = BackfillAction.MISMATCH
    return CellDecision(row.header(key) or key, row.cells[key], value, expected, action, target)


def _fill_message(decisions: list[CellDecision]) -> str:
    n = sum(d.action == BackfillAction.FILL for d in decisions)
    return f"表上空白 {n} 格，整份核對通過後回填" if n else ""


def expected_slots(sched: AutocallSchedule, tenor: int | None, empty: str) -> list[Any] | str:
    """12 格比價日的期望值（不需要填的期別為空值寫法 `empty`）；無法對應格子時回傳原因。"""
    if tenor is not None and sched.periods != tenor:
        return f"說明書提前出場表有 {sched.periods} 期，與天期 {tenor} 個月不同，比價日無法對應到格子"
    if sched.periods > SLOTS:
        return f"說明書有 {sched.periods} 期，超過參考條件表的 {SLOTS} 格比價日"
    lowest = (
        0 if sched.observation == "D" else 1
    )  # D 可為 0（沒有 Non-Call）：比價日_1 = 第 1 期期始日（FCN_Express，#301）
    if not lowest <= sched.first_callable <= sched.periods:
        return f"第一個可提前出場的期別（{sched.first_callable}）不在第 {lowest}～{sched.periods} 期之間"
    if sched.listed:
        callable_ = sorted(sched.dates)
    elif sched.observation == "D":
        callable_ = sorted({max(sched.first_callable, 1), sched.periods})
    else:
        callable_ = list(range(sched.first_callable, sched.periods + 1))
    missing = [n for n in callable_ if n not in sched.dates]
    if missing:
        return "說明書抓不到以下期別的比價日：" + "、".join(f"第 {n} 期" for n in missing)
    return [sched.dates[n] if n in callable_ else empty for n in range(1, SLOTS + 1)]


# ---------------------------------------------------------------- 回填規則


def _single(
    rid: str, target: ProductTerm, what: str, name: str, row: OrderRecord, pf: ParsedField
) -> tuple[CheckResult, list[CellDecision]]:
    """只有一格的回填欄位：表上值與說明書值比對並決定這一格的處理。項目為該欄（沒有這欄時用 `name`）。"""
    key = target.field
    ov: OrderValue | None = row.get(key)
    item = Item.column(name, [ov])
    missing = _missing_columns(row, [key])
    if missing:
        return _column_missing(rid, key, missing, [ov], item), []
    if not pf.ok:
        return doc_review(rid, key, pf, ov.value if ov else None, [ov], item=item), []
    d = _decide(row, target, pf.value)
    ok = d.action != BackfillAction.MISMATCH
    return (
        result(
            rid,
            key,
            S.PASS if ok else S.MISMATCH,
            expected=d.sheet_value,
            actual=pf.value,
            pf=pf,
            ov=[ov],
            reason="" if ok else "value_mismatch",
            message=_fill_message([d]) or ("" if ok else f"表上{what}與說明書不同，保留原值"),
            item=item,
        ),
        [d],
    )


def isin(ctx: Context) -> tuple[CheckResult, list[CellDecision]]:
    return _single("backfill.isin", ISIN, " ISIN ", "ISIN Code", ctx.order, standard_field(ctx, ISIN.field))


def issue_date(ctx: Context) -> tuple[CheckResult, list[CellDecision]]:
    """說明書發行日取自標準欄位 `issue_date`（BARC 第一章 §13(3)、HSBC 第一章 §15(2)）。"""
    pf = standard_field(ctx, ISSUE_DATE.field)
    return _single("backfill.issue_date", ISSUE_DATE, "發行日", "發行日", ctx.order, pf)


def compare_dates(ctx: Context) -> tuple[CheckResult, list[CellDecision]]:
    rid, key, sched = "backfill.compare_dates", "compare_dates", standard_field(ctx, "autocall_schedule")
    row = ctx.order
    keys = [slot.field for slot in AUTOCALL_SLOTS]
    ovs = [row.autocall_date(slot.slot) for slot in AUTOCALL_SLOTS]
    item = Item.group(COMPARE_DATES, ovs)
    missing = _missing_columns(row, keys)
    if missing:
        return _column_missing(rid, key, missing, ovs, item), []
    if not sched.ok:
        return doc_review(rid, key, sched, None, ovs, item=item), []
    tenor = standard_field(ctx, "tenor_months")
    slots = expected_slots(sched.value, tenor.value if tenor.ok else None, ctx.fmt.empty_value)
    if isinstance(slots, str):
        return (
            result(
                rid,
                key,
                S.REVIEW_REQUIRED,
                pf=sched,
                ov=ovs,
                reason="compare_dates_unmapped",
                message=slots,
                item=item,
            ),
            [],
        )
    decisions = [_decide(row, slot, e) for slot, e in zip(AUTOCALL_SLOTS, slots, strict=True)]
    final = standard_field(ctx, "final_valuation_date")
    if not final.ok:
        return doc_review(rid, key, final, None, ovs, item=item), decisions
    latest = max(d for d in slots if isinstance(d, dt.date))
    if latest != final.value:
        return (
            result(
                rid,
                key,
                S.REVIEW_REQUIRED,
                expected=final.value,
                actual=latest,
                pf=sched,
                ov=ovs,
                reason="compare_dates_max_mismatch",
                message=f"說明書最晚的比價日 {latest.isoformat()} 不等於最終比價日 {final.value.isoformat()}，不回填",
                item=item,
            ),
            decisions,
        )
    bad = [d for d in decisions if d.action == BackfillAction.MISMATCH]
    rule = (
        "評價日的月數與天期才填"
        if sched.value.listed
        else "D：填第一個可提前出場期與最後一期"
        if sched.value.observation == "D"
        else "P：第一個可提前出場期起每期都填"
    )
    return (
        result(
            rid,
            key,
            S.MISMATCH if bad else S.PASS,
            expected={d.column: d.sheet_value for d in bad} or None,
            actual={d.column: d.expected for d in bad} or [d.expected for d in decisions],
            pf=sched,
            ov=ovs,
            reason="value_mismatch" if bad else "",
            message="；".join(x for x in (rule, _fill_message(decisions)) if x)
            + ("；表上值與說明書不同的格子保留原值" if bad else ""),
            item=item,
        ),
        decisions,
    )


def lookback_end_date(ctx: Context) -> tuple[CheckResult | None, list[CellDecision]]:
    """回顧結束日（BEN lookback，#376）：上手沒交這欄（沒有 lookback 範本）時沒有結果。說明書有回顧觀察期間時同其他單格回填
    欄位（表上沒有這欄 → 轉人工覆核）；說明書沒有（不是 lookback）時表上要空白（或空值寫法），有值 → 不一致，表上沒有這欄
    不影響。已算出回顧期初價格的商品寫回時不改值（`sp_workflow.product_terms.backfill`）。"""
    rid, key, name = "backfill.lookback_end_date", LOOKBACK_END_DATE.field, "回顧結束日"
    pf, row = standard_field(ctx, key), ctx.order
    if pf == not_provided(key):
        return None, []
    if pf.status != FieldStatus.NOT_APPLICABLE:
        return _single(rid, LOOKBACK_END_DATE, name, name, row, pf)
    ov = row.get(key)
    if ov is None or ov.value in (None, ctx.fmt.empty_value):
        return None, []
    message = "說明書沒有回顧觀察期間（不是 lookback），表上回顧結束日要空白；保留原值"
    item = Item.column(name, [ov])
    return (
        result(
            rid, key, S.MISMATCH, expected=ov.value, pf=pf, ov=[ov], reason="value_mismatch", message=message, item=item
        ),
        [],
    )


def underlying_prices(ctx: Context) -> tuple[CheckResult | None, list[CellDecision]]:
    """期初定價為 VWAP：UL_1～UL_5 的四個價格欄一律以說明書價格表覆寫。

    說明書價格表上有的標的寫說明書的值（無 KI 時下限價為空值寫法）；沒有的標的四欄都寫空值寫法。
    說明書明確判定 KO 價不適用時 KO 價也寫空值寫法（Non-Call = 天期時說明書交出 KO 價 = 期初價格，照寫）。
    不是 VWAP 時沒有結果也沒有決策（價格欄由 `field.underlying_prices` 比對）。
    """
    if not is_vwap(ctx):
        return None, []
    rid, key, table = "backfill.underlying_prices", "underlying_prices", standard_field(ctx, "underlying_prices")
    row, empty = ctx.order, ctx.fmt.empty_value
    n = max(row.underlying_slots, len(table.value) if table.ok else 0)  # 表上的標的數（UL_1～UL_5）
    keys = [price_key(i, col) for i in range(1, n + 1) for col in PRICE_COLUMNS]
    ovs = [row.get(k) for k in keys]
    item = Item.group(PRICES, ovs)
    missing = _missing_columns(row, keys)
    if missing:
        return _column_missing(rid, key, missing, ovs, item), []
    if not table.ok:
        return doc_review(rid, key, table, None, ovs, item=item), []
    by_slot = dict(zip(sheet_slots(ctx, len(table.value)), table.value, strict=True))  # 表上第幾檔 → 說明書價格列
    decisions = []
    for i in range(1, n + 1):
        prices = by_slot[i].prices if i in by_slot else {}
        for col in PRICE_COLUMNS:
            d = _decide(row, UnderlyingPrice(i, col), prices.get(col, empty))
            if d.action == BackfillAction.MISMATCH:
                d = replace(d, action=BackfillAction.OVERWRITE)
            decisions.append(d)
    overwritten = sum(d.action == BackfillAction.OVERWRITE for d in decisions)
    message = "；".join(
        x
        for x in (
            "期初定價 VWAP：表上價格不比對，以說明書價格表為準",
            _fill_message(decisions),
            f"表上 {overwritten} 格與說明書不同，整份核對通過後覆寫" if overwritten else "",
        )
        if x
    )
    actual = {d.column: d.expected for d in decisions}
    return result(rid, key, S.PASS, actual=actual, pf=table, ov=ovs, message=message, item=item), decisions


def checked_marks(ctx: Context) -> tuple[CheckResult, list[CellDecision]]:
    """TS、IIS：說明書與投資人須知核對沒問題的打勾（打勾寫法 `checked_value`，Issue #127）。

    值不取自說明書：兩格都是打勾寫法；只有兩份都通過或人工放行的商品才寫入（批量入口的 `fills_sheet`）。
    TS、IIS 不是必要欄：表上沒有的欄不產生決策，核對結果檔「回填後」補上該欄並打勾（Issue #199）。
    """
    rid, key, mark, row = "backfill.checked", "checked", ctx.fmt.checked_value, ctx.order
    ovs = [row.get(k) for k in CHECKED_FIELDS]
    item = Item.group("、".join(row.header(k) or k for k in CHECKED_FIELDS), ovs)
    undeclared = [k for k in CHECKED_FIELDS if row.header(k) is None]  # 格式設定沒有欄名
    if undeclared:
        return _column_missing(rid, key, _missing_columns(row, undeclared), ovs, item), []
    decisions = [_decide(row, m, mark) for m in CHECKED_MARKS if row.has(m.field)]
    not_on_sheet = [row.header(k) for k in CHECKED_FIELDS if not row.has(k)]
    bad = [d for d in decisions if d.action == BackfillAction.MISMATCH]
    n = sum(d.action == BackfillAction.FILL for d in decisions)
    message = "；".join(
        x
        for x in (
            f"表上空白 {n} 格，說明書與投資人須知都通過或人工放行後填 {mark}" if n else "",
            f"表上已有 {mark} 以外的值，保留原值" if bad else "",
            f"表上沒有{'、'.join(not_on_sheet)}欄，核對結果檔「回填後」補上並填 {mark}" if not_on_sheet else "",
        )
        if x
    )
    return (
        result(
            rid,
            key,
            S.MISMATCH if bad else S.PASS,
            expected={d.column: d.sheet_value for d in bad} or None,
            actual={d.column: d.expected for d in bad} or [d.expected for d in decisions],
            ov=ovs,
            reason="value_mismatch" if bad else "",
            message=message,
            item=item,
        ),
        decisions,
    )
