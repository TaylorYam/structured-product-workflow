"""Natixis FCN_Stepdown 中文投資人須知（docs/checker/templates/natixis-zh-stepdown-iis.md，workflow #545）。

版面、相關機構、商品簡介、KO／KI 句型、費用表同 Natixis FCN 投資人須知（parsers/natixis_iis.py），先照它讀出，再改讀 KO 價格
定義：Stepdown 每期 KO% 不同，投資人須知只參照說明書第一章第 14 項，不交出 KO %。

依貳、商品事項的「於相關配息觀察期間(t)之自動提前到期觀察日」辨識商品家族（範本規格 §2）；有這個標記、但骨架或英文名稱
不成立（例：早期版面第 1 行標題與刊印日期分兩行，範本規格 §3）時停在本範本、判範本不符，不退回 DACN、FCN 範本。
"""

from __future__ import annotations

from collections.abc import Sequence

from ..schema import DetectionResult, Line, ParsedField
from ..text import squash
from . import iis, natixis, natixis_iis
from .layout import TextIndex

TEMPLATE_ID = "natixis-zh-stepdown-iis"
PARSER_VERSION = "1"
FAMILY = "於相關配息觀察期間(t)之自動提前到期觀察日"
KO_DEF_TABLE = "下表所列之期初進場價格"  # NS04、NS05：投資人須知沒有這張表（範本規格 §1.1）


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §2）：Natixis 投資人須知骨架（`natixis_iis.skeleton`）、英文名稱（FCN 的名稱）與 Stepdown 標記都
    成立才是本範本；有標記、其他條件不成立的文件停在本範本、判範本不符（不退回 DACN、FCN 範本）。"""
    sk = natixis_iis.skeleton(lines)
    failed = list(sk.failed)
    if not (sk.en.startswith("Natixis–") and squash(natixis.NAME_EN_MARK) in sk.en):
        failed.append(f"英文名稱沒有「Natixis –」與「{natixis.NAME_EN_MARK}」")
    if FAMILY not in natixis_iis.product_text(sk.text):
        return DetectionResult(False, [*failed, f"貳、商品事項沒有「{FAMILY}」（不是 Stepdown）"], sk.evidence)
    if failed:
        return DetectionResult(False, failed, sk.evidence, claimed=True)
    return DetectionResult(True, [], sk.evidence)


def ko_definition(ti: TextIndex) -> ParsedField:
    """貳、商品事項的 KO 價格定義（範本規格 §1.1）：一句 → 交出寫法（規則只接受參照說明書第 14 項）；「下表所列」與參照說明書
    兩句 → 歧義（轉人工覆核，待使用者決定）；找不到或其他多句 → 缺漏。"""
    sec = iis.section(ti, natixis_iis.PRODUCT)
    if sec is None:
        return ParsedField.missing("ko_definition", "找不到「貳、商品事項」到「叁、投資風險」段落")
    st = TextIndex(sec)
    found = list(st.finditer(natixis_iis.KO_DEF))
    lines = [ln for m in found for ln in st.lines_for(m.start(), m.end())]
    values = [m[1] for m in found]
    if len(found) == 1:
        return ParsedField.present("ko_definition", values[0], lines)
    if sorted(values) == sorted([KO_DEF_TABLE, natixis_iis.KO_DEF_INITIAL]):
        note = (
            f"有兩句 KO 價格定義：「{KO_DEF_TABLE}」（投資人須知沒有這張表）與「{natixis_iis.KO_DEF_INITIAL}」，"
            "請人工確認"
        )
        return ParsedField.ambiguous("ko_definition", values, lines, note)
    return ParsedField.missing("ko_definition", "找不到或有多處「自動提前到期贖回價格為…期初進場價格」")


def read(lines: Sequence[Line]) -> iis.IisSheet:
    """先照 Natixis FCN 投資人須知讀出，再改讀 KO 價格定義；不交出 KO %（Stepdown 每期不同，說明書第 14 項才有）。"""
    base = natixis_iis.read(lines)
    fields = {k: v for k, v in base.fields.items() if k != "ko_pct"}
    fields["ko_definition"] = ko_definition(base.full_text)
    return iis.IisSheet(fields, base.full_text, base.provided - {"ko_pct"})
