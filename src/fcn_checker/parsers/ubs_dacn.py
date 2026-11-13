"""UBS DACN 中文產品說明書（docs/checker/templates/ubs-zh-dacn.md）：版面、第 1～2 頁、商品基本資料、相關機構事業概況、
一般交易事項同 UBS FCN 說明書（parsers/ubs.py），以 DACN 的條文寫法（`VARIANT`）照它讀出：八價格表與十五情境價格表多
`區間下限價` 欄（沒有 KI 的下限價欄）、配息表公式 `對應的配息金額=固定（變動）配息率x每單位面額`、變動配息率的年化後面也
乘 `X（nt/Nt）`、情境的平均年化報酬率不讀（依計息天數假設算出）。再加讀配息門檻、配息表保證期數、十四的保證期數與 nt 定義
的計息範圍。

依八的 `變動配息率：x%X（nt/Nt）` 與比 `區間下限價` 的 nt 定義辨識商品家族，名稱另當條件（`區間累計配息`／`Range Accrual`）；
只支援現行版範本：認得出是 UBS DACN、但現行版特徵任一項不成立時，辨識交出「不支援的範本版本」（範本規格 §1、§8）。
版本只看寫法、不看交易日早晚（同第一波 DACN，workflow #409）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

from ..schema import DetectionResult, FieldStatus, Line, ParsedField
from . import dacn, price_table, ubs
from .common import derived
from .layout import TextIndex, capture

TEMPLATE_ID = "ubs-zh-dacn-pd"
PARSER_VERSION = "1"
OLD_VERSION = "UBS DACN 說明書的寫法和現行版不同"
NAME_ZH_MARK = "提前出場區間累計配息連結"
NAME_EN_MARK = "Callable Variable Maturity Range Accrual Equity Linked"
# 計息比的價格（配息門檻）：價格表的欄名與 nt 定義的比較對象；全文有它就是本家族的標記（範本規格 §8），不符本範本時仍認領
# 文件、不退回 FCN 範本（#436）。UBS FCN 說明書全文沒有
BARRIER = "區間下限價"
PCT = ubs.PCT
X = r"[xX×]"
Q = r"[“”\"]"
# 八（與十五設定參數重複）的區間累計配息固定寫法（範本規格 §2.1，去空白後依序出現）
WORDINGS = (
    ("固定配息率", r"固定配息率[:：]" + PCT + r"。（年化配息率為" + PCT + r"）。"),
    ("變動配息率", r"變動配息率[:：]" + PCT + X + r"（nt/Nt）（年化配息率為" + PCT + X + r"（nt/Nt））。"),
    (
        "nt 定義",
        r"nt[:：]指在該配息觀察期間開始日至（i）該配息觀察期間結束日或（ii）"
        + Q
        + "提前出場日"
        + Q
        + r"孰先，(所有)?連結標的股票收盤價(皆)?高於或等於區間下限價的交易所營業日天數",
    ),
    (
        "Nt 定義",
        re.escape(
            "Nt：指在該配息觀察期間交易所營業日總天數，若提前出場事件發生時，Nt指在該配息觀察期間交易所預定營業日之天數，"
            "不適用中斷日之規定。"
        ),
    ),
    ("t 範圍", r"t係為\d+至\d+的數字，代表第\d+至第\d+個月。"),
)
NT = dict(WORDINGS)["nt 定義"]
# 商品家族（範本規格 §8 條件 2）：變動配息率乘 nt/Nt，且 nt 數的是收盤價高於或等於區間下限價的天數
FAMILY = (r"變動配息率[:：]" + PCT + X + r"（nt/Nt）", r"nt[:：]指.+?收盤價皆?高於或等於區間下限價的交易所營業日天數")
FORMULA = r"對應的配息金額=(固定|變動)配息率" + X + "每單位面額"
HEAD = BARRIER + r"[（(]即期初價格的" + PCT + r"[)）]"
ART14 = r"到期最低保證配息率[:：](第一)個月有固定配息"  # 範本規格 §4.2；其他寫法沒有樣本
MONTHS = {"第一": 1}  # 十四與投資人須知收益分配事項「…個月有（支付）固定配息」的期數字樣
# 價格表：區間下限價（配息門檻）、轉換價、提前出場價；沒有 KI 的下限價欄（「區間下限價」含「下限價」三個字，不能宣告）
PRICE_TABLE = replace(
    ubs.PRICE_TABLE,
    columns=(
        price_table.Column("barrier", BARRIER),
        price_table.Column("strike", "轉換價"),
        price_table.Column("ko", "提前出場價"),
    ),
)
VARIANT = ubs.Variant(
    PRICE_TABLE,
    FORMULA,
    r"配息率[:：]" + PCT + X + r"（nt/Nt）（年化配息率為" + PCT + X + r"（nt/Nt））",
    annualized_returns=False,
)


# ---------------------------------------------------------------- 辨識


def is_accrual(art8: str) -> bool:
    """商品家族（區間累計配息，範本規格 §8 條件 2）。"""
    return all(re.search(p, art8) for p in FAMILY)


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：UBS 說明書骨架與 DACN 名稱（條件 1）、區間累計配息（條件 2）都成立後，現行版特徵全部成立才是
    本範本；缺任一項 → 不支援的範本版本。全文有 `BARRIER` 的文件停在本範本（不退回 FCN 範本）。"""
    doc, failed, evidence = ubs.skeleton(lines, NAME_ZH_MARK, NAME_EN_MARK)
    arts = doc.items(doc.section("商品基本資料"))
    art8 = TextIndex(arts.get(8, [])).text
    if not is_accrual(art8):
        failed.append("商品基本資料八沒有區間累計配息的「變動配息率：x%X（nt/Nt）」與比「區間下限價」的 nt 定義")
    if failed:
        return DetectionResult(False, failed, evidence, claimed=BARRIER in TextIndex(doc.lines).text)
    missing = version_problems(art8, TextIndex(arts.get(12, [])))
    if missing:
        return DetectionResult(False, missing, evidence, unsupported=f"{OLD_VERSION}：{'；'.join(missing)}")
    return DetectionResult(True, [], evidence)


def version_problems(art8: str, art12: TextIndex) -> list[str]:
    """現行版特徵（範本規格 §8）中不成立的項目：八的區間累計配息五段固定寫法（依序）、價格表區間下限價表頭、配息表公式、
    十二讀得到交易日（只看寫法，不看早晚）。"""
    out, at = [], 0
    for where, pattern in WORDINGS:
        m = re.compile(pattern).search(art8, at)
        if m is None:
            out.append(f"商品基本資料八{where}的寫法和現行版不同")
        else:
            at = m.end()
    if not re.search(HEAD, art8):
        out.append(f"商品基本資料八價格表沒有「{BARRIER}(即期初價格的X%)」欄")
    first_row = r"第1個配息觀察期間" + (r"\|?" + ubs.D) * 3 + FORMULA  # 第 1 期那一列就是 DACN 寫法
    if not re.search(first_row, art8) or re.search(ubs.FCN.coupon_formula, art8):  # 任一列是 FCN 的順序都不算
        out.append("商品基本資料八配息表公式不是「對應的配息金額=固定（變動）配息率x每單位面額」")
    if not capture("trade_date", art12, r"交易日/期初訂價日[:：]" + ubs.D).ok:
        out.append("商品基本資料十二讀不到交易日")
    return out


# ---------------------------------------------------------------- 讀出


def read(lines: Sequence[Line]) -> ubs.UbsTermSheet:
    """照 UBS FCN 讀出（DACN 的條文寫法）後，加讀配息門檻、保證期數與計息範圍（範本規格 §3）。"""
    base = ubs.read(lines, VARIANT)
    doc = base.document
    arts = doc.items(doc.section("商品基本資料"))
    art8 = TextIndex(arts.get(8, []))
    f = dict(base.fields)
    nt = list(art8.finditer(NT))
    definition = art8.lines_for(nt[0].start(), nt[0].end()) if nt else ()
    f["coupon_barrier_pct"] = dacn.barrier_from_head(
        f["underlying_table"], "barrier", f"商品基本資料八價格表「{BARRIER}」表頭讀不到（即期初價格的X%）", definition
    )
    f["coupon_guaranteed_periods"] = guaranteed(f["coupon_table"])
    f["guaranteed_periods_art14"] = capture(
        "guaranteed_periods_art14", TextIndex(arts.get(14, [])), ART14, MONTHS.get, whole_match=True
    )
    f["accrual_scope"] = accrual_scope(art8, "accrual_scope", "八")
    f["accrual_scope_art15"] = accrual_scope(TextIndex(arts.get(15, [])), "accrual_scope_art15", "十五")
    f["ki_type"] = ki_type(art8, f["underlying_table"])
    return replace(base, fields=f)


def guaranteed(table: ParsedField) -> ParsedField:
    """配息表公式的保證期數 g：第 1～g 期是 `固定配息率`、其後都是 `變動配息率`（範本規格 §4.2）；不是這樣讀不到。"""
    name = "coupon_guaranteed_periods"
    if not table.ok:
        return derived(name, table)
    if table.value["kind"] != "D":
        return ParsedField.missing(name, "商品基本資料八的配息表不是配息觀察期間表（沒有固定／變動配息率公式）")
    fixed = [r["fixed"] for r in table.value["rows"]]
    g = next((i for i, x in enumerate(fixed) if not x), len(fixed))
    if g == 0 or any(fixed[g:]):
        return ParsedField(
            name,
            FieldStatus.INVALID,
            None,
            list(table.evidence),
            note="配息表「固定配息率」的期別不是從第 1 期起連續、其後都是「變動配息率」",
        )
    return derived(name, table, g)


def ki_type(art8: TextIndex, table: ParsedField) -> ParsedField:
    """KI 型態：同 UBS FCN（`ubs.ki_type`），但 DACN 價格表不宣告下限價欄（`區間下限價` 含這三個字，範本規格 §5.1），有觸及
    下限事件時不比價格表有沒有下限價欄、只看定義句與到期日給付；有 KI 的 DACN 沒有樣本，由型態檢查轉人工（核對規則 §3.1）。"""
    pf = ubs.ki_type(art8, table)
    if pf.status != FieldStatus.INVALID or not list(art8.finditer(ubs.KI_DEFINITION)):
        return pf
    return ubs.ki_type(art8, ParsedField.missing("underlying_table"))


def accrual_scope(ti: TextIndex, name: str, article: str) -> ParsedField:
    """商品基本資料 `article`（八；十五設定參數重複）nt 定義的計息範圍（範本規格 §4.5）：`所有連結標的股票收盤價皆…` →
    `all`；`連結標的股票收盤價…`（沒有所有、皆，單一標的的寫法）→ `single`；只有其一或讀不到 → 不合法／缺漏。"""
    hits = list(ti.finditer(NT))
    if len(hits) != 1:
        note = f"商品基本資料{article}的 nt 定義出現多次" if hits else f"商品基本資料{article}找不到 nt 定義"
        return ParsedField.missing(name, note)
    m = hits[0]
    lines = ti.lines_for(m.start(), m.end())
    if bool(m[1]) != bool(m[2]):
        return ParsedField.invalid(name, lines, "nt 定義只寫了「所有」或「皆」其中一個")
    return ParsedField.present(name, "all" if m[1] else "single", lines)
