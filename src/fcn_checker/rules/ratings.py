"""發行機構／保證機構信用評等（#240，docs/checker/rules/shared-check-rules.md §10）：說明書與投資人須知都核對。

基準是參考條件表的評等欄（資料庫來源從評等表帶出交易日當時的評等，ADR 0013：核對不直接讀資料庫）。
文件每處評等段落（parsers/ratings.py 的 `StatedRatings`）逐家評等機構比對：

- 只比文件有寫的評等機構；DBRS、R&I 等不追蹤的評等機構 parser 不交出。文件沒寫的不算錯。
- 有寫但和參考條件表不同 → 不一致，項目寫出機構、評等機構與位置，錯訊寫出兩邊的值。
  參考條件表這家評等機構空白（評等表沒有這家評等）而文件有寫 → 需人工覆核，寫出機構與評等機構
  （2026-10-26 使用者決定：表上沒有評等不能判對錯）。
- 評等表只有現在的評等、不保存歷史（使用者決定）：舊文件寫的是後來調整前的評等時判不一致，由作業人員判斷。
- 參考條件表三家都空白（評等表沒有這個機構，或交易日當時還沒有評等）→ 需人工覆核，寫出上手與機構。
- 參考條件表沒有評等欄（舊版 Excel）→ 需人工覆核（每個機構一筆），不讓整批出錯，也不算通過。
- 段落找不到或讀不出來 → 需人工覆核（parser 寫出原因）。範本沒有保證機構（`absent`）→ 不核對。

只有上手註冊表標了 `Issuer.ratings` 的上手才跑（#240 先做 HSBC、MS、DBS、Natixis、UBS，其他上手 #241～#243）。
"""

from __future__ import annotations

from ..parsers.ratings import FIELDS, StatedRatings
from ..rating_formats import AGENCIES, GUARANTOR, ISSUER, canonical, rating_key
from ..schema import CheckResult, FieldStatus, Item, OrderValue
from ..schema import CheckStatus as S
from . import kit
from .kit import Check, Context

RULE_ID = "standard.credit_rating"
ROLES = dict(zip(FIELDS, (ISSUER, GUARANTOR), strict=True))  # 標準欄位 → 機構


def run(ctx: Context) -> list[CheckResult]:
    out: list[CheckResult] = []
    for field, role in ROLES.items():
        pf = kit.standard_field(ctx, field)
        if pf.status == FieldStatus.NOT_APPLICABLE:  # 範本沒有保證機構
            continue
        if not pf.ok:
            out.append(kit.doc_review(RULE_ID, field, pf, item=Item.sheet(f"{role}信用評等"), document=ctx.document))
            continue
        out.extend(_role(ctx, field, role, pf.value))
    return out


def _role(ctx: Context, field: str, role: str, places: tuple[StatedRatings, ...]) -> list[CheckResult]:
    """一個機構（發行機構或保證機構）：各處段落逐家評等機構比對參考條件表。"""
    keys = {agency: rating_key(role, agency) for agency in AGENCIES}
    ovs = {agency: ctx.sheet_field(key) for agency, key in keys.items()}
    who = f"{ctx.issuer} {role}"
    evidence = [e for p in places for e in p.evidence]
    if not all(ctx.order.has(key) for key in keys.values()):
        check = Check(RULE_ID, field, Item.sheet(f"{role}信用評等"), ctx.document)
        return [
            check.result(
                S.REVIEW_REQUIRED,
                evidence=evidence,
                reason="order_missing",
                message=f"{ctx.sheet_source}沒有{role}評等欄（舊版參考條件表），{who}的信用評等請人工核對",
            )
        ]
    table = {agency: _rating(agency, ov, ctx.fmt.empty_value) for agency, ov in ovs.items()}
    if not any(table.values()):
        check = Check(
            RULE_ID, field, Item.sheet(f"{role}信用評等", list(ovs.values())), ctx.document, ov=tuple(ovs.values())
        )
        return [
            check.result(
                S.REVIEW_REQUIRED,
                evidence=evidence,
                reason="rating_entity_missing",
                message=f"評等表沒有 {who}在交易日當時的評等（{ctx.sheet_source}的{role}評等欄都是空白），請人工核對；"
                "新的機構請先在「維護」→「發行機構評等…」新增",
            )
        ]
    out: list[CheckResult] = []
    for place in places:
        if place.problem:
            check = Check(RULE_ID, field, Item.sheet(f"{role}信用評等（{place.where}）"), ctx.document)
            out.append(
                check.result(
                    S.REVIEW_REQUIRED,
                    evidence=place.evidence,
                    reason="document_invalid" if place.evidence else "document_missing",
                    message=f"{ctx.document}{place.where}的{role}信用評等讀不出來：{place.problem}",
                )
            )
            continue
        for agency, stated in place.ratings:
            ov = ovs[agency]
            item = Item.sheet(f"{role}{agency}評等（{place.where}）", [ov])
            check = Check(RULE_ID, field, item, ctx.document, ov=(ov,))
            if table[agency] is None:  # 評等表沒有這家評等：不能判對錯
                out.append(
                    check.result(
                        S.REVIEW_REQUIRED,
                        actual=stated,
                        evidence=place.evidence,
                        reason="rating_missing",
                        message=f"評等表沒有 {who}的{agency}評等（{ctx.sheet_source}這格空白），"
                        f"{ctx.document}{place.where}寫 {stated}，請人工核對",
                    )
                )
                continue
            out.append(check.compare(table[agency], stated, evidence=place.evidence))
    return out


def _rating(agency: str, ov: OrderValue | None, empty: str) -> str | None:
    """參考條件表一格評等：空白或空值寫法是沒有評等；手打的寫法和文件那邊一樣統一（`A－`、`a+` → `A+`）。"""
    if ov is None or ov.value is None:
        return None
    text = canonical(agency, str(ov.value))
    return None if text in ("", empty) else text
