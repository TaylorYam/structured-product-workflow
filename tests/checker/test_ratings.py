"""信用評等核對（#240～#243）：說明書與投資人須知寫的發行機構／保證機構評等 = 參考條件表交易日當時的評等（rules/ratings.py）。

黑箱：各上手合成說明書＋投資人須知（評等段落照真實寫法，值虛構）＋參考條件表一列，經批量入口核對；14 家都登記在下面兩張表。
只寫保證機構評等的上手（CA、JPM、Nomura、SG，#241）發行機構是工具公司，文件寫「無單獨信用評等」等，不核對發行機構評等；
沒寫明或發行機構那段寫了評等時轉人工覆核。
發行機構與保證機構都寫的上手（Citi、GS、BNP、BBVA，#242）兩個都比，各自對應參考條件表的發行機構、保證機構評等欄。
另有評等段落讀法（parsers/ratings.py 的 `parse`）的直接測試，涵蓋真實文件的全形／半形括號、彎引號與換行。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

import barc_synth
import bbva_synth
import bnp_synth
import ca_synth
import citi_synth
import dbs_synth
import gs_synth
import hsbc_synth
import jpm_synth
import ms_synth
import natixis_synth
import nomura_synth
import sg_synth
import ubs_synth
from fcn_checker.issuers import REGISTRY
from fcn_checker.parsers.ratings import parse
from fcn_checker.rating_formats import FITCH, GUARANTOR, ISSUER, MOODYS, SP, canonical
from fcn_checker.rules.ratings import RULE_ID
from fcn_checker.schema import CheckReport
from fcn_checker.schema import CheckStatus as S
from harness import CONFIG, check_all, iis_path
from harness import field_problems as problems
from pdf_writer import Edit
from reference_synth import OLD_REFERENCE_HEADERS, REFERENCE_HEADERS, Ratings, build_reference_sheet, reference_row

# 上手 → （合成器, 參考條件表列, 說明書幾處評等段落, 核對的機構）
ISSUERS: dict[str, tuple[Any, Any, int, str]] = {
    "BARC": (barc_synth, reference_row, 2, ISSUER),  # 第一章第 3 項、第二章 (7)；評等寫在評等機構前面（#243）
    "HSBC": (hsbc_synth, reference_row, 2, ISSUER),  # 第一章第 3 項、第二章 (7)
    "MS": (ms_synth, reference_row, 2, ISSUER),  # 第一章第 3 項、第二章 (7)
    "DBS": (dbs_synth, dbs_synth.row, 2, ISSUER),  # 商品基本資料、相關機構事業概況 (7)
    "NATIXIS": (natixis_synth, natixis_synth.row, 3, ISSUER),  # 封面、第一章第 3 項、第二章 (7)
    "UBS": (ubs_synth, ubs_synth.row, 2, ISSUER),  # 商品基本資料三、相關機構事業概況一
    # 只寫保證機構評等（#241）：第一章第 3 項、第二章保證機構 (7)
    "CA": (ca_synth, ca_synth.row, 2, GUARANTOR),
    "JPM": (jpm_synth, jpm_synth.row, 2, GUARANTOR),
    "NOMURA": (nomura_synth, nomura_synth.row, 2, GUARANTOR),
    "SG": (sg_synth, reference_row, 2, GUARANTOR),
}
# 發行機構與保證機構都寫的上手（#242）→ （合成器, 參考條件表列, 說明書發行機構幾處, 說明書保證機構幾處）；投資人須知各一處
BOTH: dict[str, tuple[Any, Any, int, int]] = {
    "CITI": (citi_synth, citi_synth.row, 2, 2),  # 第一章第 3 項、第二章 (7)
    "GS": (gs_synth, gs_synth.row, 2, 2),  # 第一章第 3 項、第二章 (7)／(vii)
    "BNP": (bnp_synth, reference_row, 2, 2),  # 第一章第 3 項、第二章 (7)
    "BBVA": (bbva_synth, bbva_synth.row, 2, 2),  # 第一章第 3 項、第二章 (g)
}
AGENCIES = ("標準普爾", "穆迪", "惠譽")


def role_of(code: str) -> str:
    return ISSUERS[code][3]


def check(
    tmp_path,
    code: str,
    *,
    ratings: Ratings | None = None,
    guarantor_ratings: Ratings | None = None,
    edits=(),
    headers=REFERENCE_HEADERS,
    **overrides: Any,
) -> dict[str, CheckReport]:
    """合成說明書＋投資人須知＋參考條件表一列（`overrides` 以 Excel 欄名覆寫），回傳 {"TS"/"IIS": CheckReport}。
    `ratings` 是發行機構評等（只寫保證機構評等的上手給值時，說明書改寫發行機構評等），`guarantor_ratings` 是保證機構評等。"""
    synth, row, *_ = ISSUERS[code] if code in ISSUERS else BOTH[code]
    spec = synth.Spec()
    if ratings is not None:
        spec = spec.with_(ratings=ratings)
    if guarantor_ratings is not None:
        spec = spec.with_(guarantor_ratings=guarantor_ratings)
    pdf = synth.build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(spec, **overrides)], headers)
    outcome = check_all(sheet, [pdf], CONFIG)
    by_file = {i.term_sheet: i.report for i in outcome.items}
    return {"TS": by_file[pdf], "IIS": by_file[iis_path(pdf)]}


def rating_results(report: CheckReport):
    return [r for r in report.results if r.rule_id == RULE_ID]


@pytest.mark.parametrize("code", ISSUERS)
def test_stated_ratings_equal_to_the_reference_sheet_pass(tmp_path, code) -> None:
    reports = check(tmp_path, code)
    places = ISSUERS[code][2]
    for kind, count in (("TS", places), ("IIS", 1)):
        report = reports[kind]
        assert report.status == S.PASS, (kind, problems(report))
        found = rating_results(report)
        assert len(found) == count * 3, kind  # 每處三家評等機構各一筆；只寫保證機構的上手沒有發行機構的結果
        assert {r.status for r in found} == {S.PASS}
        assert {r.item.name.split("（")[0] for r in found} == {f"{role_of(code)}{a}評等" for a in AGENCIES}
        assert all(r.document_evidence for r in found)


@pytest.mark.parametrize("code", ISSUERS)
def test_one_agency_different_from_the_reference_sheet_is_a_mismatch(tmp_path, code) -> None:
    role = role_of(code)
    reports = check(tmp_path, code, **{f"{role}評等_惠譽": "A-"})  # 文件寫 AA
    for kind in ("TS", "IIS"):
        found = rating_results(reports[kind])
        bad = [r for r in found if r.status != S.PASS]
        assert bad and all(r.status == S.MISMATCH for r in bad), kind
        assert {r.item.name.split("（")[0] for r in bad} == {f"{role}惠譽評等"}
        assert len(bad) == (ISSUERS[code][2] if kind == "TS" else 1), "每處都報"
        assert all((r.expected, r.actual) == ("A-", "AA") for r in bad)
        assert {(rid, st) for rid, _, st in problems(reports[kind])} == {(RULE_ID, S.MISMATCH)}


def test_mismatch_message_names_the_entity_agency_and_both_values(tmp_path) -> None:
    from fcn_checker.messages import problem_message

    reports = check(tmp_path, "MS", **{"發行機構評等_穆迪": "A1"})
    messages = [problem_message(r) for r in rating_results(reports["TS"]) if r.status == S.MISMATCH]
    assert messages == [
        "發行機構穆迪評等（第一章第 3 項）對不起來：參考條件表 A1／說明書 Aa3",
        "發行機構穆迪評等（第二章 (7) 信用評等）對不起來：參考條件表 A1／說明書 Aa3",
    ]
    iis = [problem_message(r) for r in rating_results(reports["IIS"]) if r.status == S.MISMATCH]
    assert iis == ["發行機構穆迪評等（商品簡介 4)）對不起來：參考條件表 A1／投資人須知 Aa3"]


@pytest.mark.parametrize("code", ["HSBC", "NATIXIS", "BARC"])
def test_agencies_the_document_does_not_state_are_not_compared(tmp_path, code) -> None:
    """文件只寫標普與穆迪：參考條件表惠譽和文件不同也不報（只比文件有寫的）。"""
    reports = check(tmp_path, code, ratings=Ratings(fitch=None), **{"發行機構評等_惠譽": "BBB"})
    for kind in ("TS", "IIS"):
        found = rating_results(reports[kind])
        assert found and {r.status for r in found} == {S.PASS}, kind
        assert not [r for r in found if "惠譽" in r.item.name]


def test_untracked_agencies_are_ignored(tmp_path) -> None:
    """文件多寫 DBRS：不比，也不影響後面讀其他評等機構。"""
    edit = Edit("評定為AA。", "評定為AA、經DBRS評定為AA(high)。", "ts.art3")
    reports = check(tmp_path, "MS", edits=[edit])
    found = rating_results(reports["TS"])
    assert len(found) == 6 and {r.status for r in found} == {S.PASS}


def test_ratings_typed_by_hand_in_the_reference_sheet_are_normalised(tmp_path) -> None:
    """作業人員在 Excel 手打的評等（全形加減號、小寫、空白）和文件那邊一樣統一寫法後再比（文件 A+／Aa3／AA）。"""
    typed = {"發行機構評等_標準普爾": "a ＋", "發行機構評等_穆迪": " AA3", "發行機構評等_惠譽": "aa"}
    reports = check(tmp_path, "MS", **typed)
    for kind in ("TS", "IIS"):
        found = rating_results(reports[kind])
        assert found and {r.status for r in found} == {S.PASS}, (kind, problems(reports[kind]))


@pytest.mark.parametrize(
    ("agency", "typed", "expected"),
    [(SP, "A－", "A-"), (SP, "a+", "A+"), (FITCH, " bbb ＋", "BBB+"), (MOODYS, "aa3", "Aa3"), (MOODYS, "BAA1", "Baa1")],
)
def test_canonical_rating_spelling(agency, typed, expected) -> None:
    assert canonical(agency, typed) == expected


def test_a_stated_agency_blank_in_the_reference_sheet_needs_review(tmp_path) -> None:
    """評等表沒有這家評等（參考條件表空白）、文件卻寫了：需人工覆核，寫出機構與評等機構（2026-10-26 使用者決定）。"""
    reports = check(tmp_path, "DBS", **{"發行機構評等_穆迪": None})
    bad = [r for r in rating_results(reports["TS"]) if r.status != S.PASS]
    assert [(r.status, r.reason_code, r.actual) for r in bad] == [(S.REVIEW_REQUIRED, "rating_missing", "Aa3")] * 2
    assert all("評等表沒有 DBS 發行機構的穆迪評等" in r.message and "這格空白" in r.message for r in bad)
    assert {(rid, st) for rid, _, st in problems(reports["TS"])} == {(RULE_ID, S.REVIEW_REQUIRED)}


@pytest.mark.parametrize("code", ["UBS", "HSBC", "SG"])
def test_entity_missing_from_the_rating_table_needs_review(tmp_path, code) -> None:
    role = role_of(code)
    blank = {f"{role}評等_{a}": None for a in AGENCIES}
    reports = check(tmp_path, code, **blank)
    for kind in ("TS", "IIS"):
        [result] = rating_results(reports[kind])
        assert result.status == S.REVIEW_REQUIRED
        assert f"評等表沒有 {code} {role}" in result.message
        assert {(rid, st) for rid, _, st in problems(reports[kind])} == {(RULE_ID, S.REVIEW_REQUIRED)}


@pytest.mark.parametrize("code", ISSUERS)
def test_old_reference_sheet_without_rating_columns_needs_review(tmp_path, code) -> None:
    """舊 Excel 參考條件表沒有評等欄：整批照常核對，評等轉人工覆核（不出錯、不算通過）。"""
    reports = check(tmp_path, code, headers=OLD_REFERENCE_HEADERS)
    for kind in ("TS", "IIS"):
        [result] = rating_results(reports[kind])
        assert result.status == S.REVIEW_REQUIRED
        assert f"沒有{role_of(code)}評等欄（舊版參考條件表）" in result.message
        assert {(rid, st) for rid, _, st in problems(reports[kind])} == {(RULE_ID, S.REVIEW_REQUIRED)}


UNRATED_ISSUERS = ("CA", "JPM", "NOMURA", "SG")  # 發行機構沒有評等、只寫保證機構評等（#241）


@pytest.mark.parametrize("code", UNRATED_ISSUERS)
def test_issuer_stated_as_unrated_is_not_checked(tmp_path, code) -> None:
    """文件寫明發行機構（工具公司）沒有評等（「無單獨信用評等」「未獲評等」「不適用」「無」，評等項目只寫保證機構）：
    不核對發行機構評等；參考條件表發行機構評等欄有值（例：手動填錯）也不比、不轉人工覆核。"""
    issuer_columns = {f"{ISSUER}評等_{a}": "BBB" for a in AGENCIES}
    reports = check(tmp_path, code, **issuer_columns)
    for kind in ("TS", "IIS"):
        report = reports[kind]
        assert report.status == S.PASS, (kind, problems(report))
        assert all(r.item.name.startswith(GUARANTOR) for r in rating_results(report))


@pytest.mark.parametrize("code", UNRATED_ISSUERS)
def test_issuer_stating_a_rating_needs_review(tmp_path, code) -> None:
    """說明書第二章發行機構那段改寫評等（不再寫發行機構沒有評等）：發行機構評等轉人工覆核、寫出位置，不當成沒有評等放過；
    保證機構評等照常比。"""
    reports = check(tmp_path, code, ratings=Ratings())
    found = rating_results(reports["TS"])
    [review] = [r for r in found if r.status != S.PASS]
    assert (review.status, review.item.name) == (S.REVIEW_REQUIRED, f"{ISSUER}信用評等")
    assert "發行機構 (" in review.message and "找不到「發行機構沒有評等」的寫法" in review.message
    assert len(found) == 1 + 2 * 3 and all(r.item.name.startswith(GUARANTOR) for r in found if r is not review)
    assert reports["IIS"].status == S.PASS, problems(reports["IIS"])


def test_issuer_paragraph_naming_an_agency_needs_review() -> None:
    """找到「發行機構沒有評等」的寫法、但那段文字裡有評等機構名稱：不合法，轉人工覆核（不猜是不是有評等）。"""
    from fcn_checker.parsers.ratings import RatingPlace, fields
    from fcn_checker.schema import FieldStatus, Line

    text = "3.發行機構：虛構公司（穆迪A2）。保證機構名稱及其長期債務信用評等：虛構銀行。"
    lines = [Line(1, 0, 0, 100, 10, text)]
    place = RatingPlace("第一章第 3 項", r"3\.發行機構：[^。]+。保證機構名稱及其長期債務信用評等：")
    pf = fields(lines, issuer_unrated=[place])["issuer_ratings"]
    assert pf.status == FieldStatus.INVALID
    assert pf.note.startswith("第一章第 3 項寫了發行機構的評等機構") and pf.evidence
    ok = fields([Line(1, 0, 0, 100, 10, text.replace("（穆迪A2）", ""))], issuer_unrated=[place])["issuer_ratings"]
    assert ok.status == FieldStatus.NOT_APPLICABLE and "第一章第 3 項" in ok.note
    with pytest.raises(ValueError):
        fields(lines)  # 發行機構評等段落與「沒有評等」的寫法要剛好給一個


def test_unreadable_rating_needs_review(tmp_path) -> None:
    edit = Edit("惠譽評等AA (高級優先債務)。為避免", "惠譽評等未定 (高級優先債務)。為避免", "ts.art3")
    reports = check(tmp_path, "NATIXIS", edits=[edit])
    found = rating_results(reports["TS"])
    review = [r for r in found if r.status == S.REVIEW_REQUIRED]
    assert [r.item.name for r in review] == ["發行機構信用評等（第一章第 3 項）"]
    assert "惠譽找不到評等" in review[0].message
    assert {r.status for r in found if r not in review} == {S.PASS}  # 其他兩處照常比


def test_all_registered_issuers_check_ratings() -> None:
    """14 家上手都做評等核對（#243），這裡的合成測試也都登記了。"""
    assert {i.code for i in REGISTRY if i.ratings} == {i.code for i in REGISTRY} == set(ISSUERS) | set(BOTH)


def test_issuers_without_the_rating_check_get_no_rating_results(tmp_path) -> None:
    """上線時評等核對還沒做的新上手（`Issuer.ratings` 未標）不跑這條規則，舊 Excel 也不會因此轉人工覆核。"""
    config = CONFIG.with_registry((barc_synth.barc_adapter(),))  # 測試用 adapter 沒標 ratings
    spec = barc_synth.Spec()
    pdf = barc_synth.build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)], OLD_REFERENCE_HEADERS)
    items = check_all(sheet, [pdf], config).items
    assert len(items) == 2  # 說明書與投資人須知
    for item in items:
        assert item.report.status == S.PASS, problems(item.report)
        assert not rating_results(item.report)


# ---------- 發行機構與保證機構都寫的上手（#242）----------


def by_role(results, role: str):
    return [r for r in results if r.item.name.startswith(role)]


@pytest.mark.parametrize("code", BOTH)
def test_issuer_and_guarantor_ratings_equal_to_the_reference_sheet_pass(tmp_path, code) -> None:
    """兩個機構各自比對參考條件表的發行機構、保證機構評等欄；GS 多寫的 DBRS、R&I 與無評等的高盛亞洲不影響。"""
    synth, _, issuer_places, guarantor_places = BOTH[code]
    spec = synth.Spec()
    reports = check(tmp_path, code)
    for kind, places in (("TS", (issuer_places, guarantor_places)), ("IIS", (1, 1))):
        report = reports[kind]
        assert report.status == S.PASS, (kind, problems(report))
        found = rating_results(report)
        assert {r.status for r in found} == {S.PASS}, kind
        for role, ratings, count in zip(
            ("發行機構", "保證機構"), (spec.ratings, spec.guarantor_ratings), places, strict=True
        ):
            mine = by_role(found, role)
            stated = {a for a, _ in ratings.stated()}
            assert len(mine) == count * len(stated), (kind, role)  # 每處文件有寫的評等機構各一筆
            assert {r.item.name.split("（")[0] for r in mine} == {f"{role}{a}評等" for a in stated}
            assert all(r.document_evidence for r in mine)


@pytest.mark.parametrize("code", BOTH)
@pytest.mark.parametrize("role", ["發行機構", "保證機構"])
def test_only_the_entity_that_differs_is_a_mismatch(tmp_path, code, role) -> None:
    """只有其中一個機構的評等和參考條件表不同：只有那個機構的項目判不一致，另一個照常通過。"""
    synth, _, issuer_places, guarantor_places = BOTH[code]
    spec = synth.Spec()
    stated = (spec.ratings if role == "發行機構" else spec.guarantor_ratings).sp
    reports = check(tmp_path, code, **{f"{role}評等_標準普爾": "BBB-"})
    for kind in ("TS", "IIS"):
        found = rating_results(reports[kind])
        bad = [r for r in found if r.status != S.PASS]
        places = (issuer_places if role == "發行機構" else guarantor_places) if kind == "TS" else 1
        assert [r.item.name.split("（")[0] for r in bad] == [f"{role}標準普爾評等"] * places, kind
        assert all(r.status == S.MISMATCH and (r.expected, r.actual) == ("BBB-", stated) for r in bad)
        other = "保證機構" if role == "發行機構" else "發行機構"
        assert by_role(found, other) and {r.status for r in by_role(found, other)} == {S.PASS}


@pytest.mark.parametrize("code", BOTH)
def test_agencies_the_document_does_not_state_are_not_compared_for_the_guarantor(tmp_path, code) -> None:
    """文件沒寫保證機構的惠譽評等（例：BNP 實際如此）：參考條件表惠譽和其他值也不報。"""
    synth = BOTH[code][0]
    unstated = replace(synth.Spec().guarantor_ratings, fitch=None)
    reports = check(tmp_path, code, guarantor_ratings=unstated, **{"保證機構評等_惠譽": "BBB"})
    for kind in ("TS", "IIS"):
        found = rating_results(reports[kind])
        assert found and {r.status for r in found} == {S.PASS}, (kind, problems(reports[kind]))
        assert not [r for r in by_role(found, "保證機構") if "惠譽" in r.item.name]


@pytest.mark.parametrize("code", BOTH)
def test_guarantor_missing_from_the_rating_table_needs_review(tmp_path, code) -> None:
    blank = {f"保證機構評等_{a}": None for a in AGENCIES}
    reports = check(tmp_path, code, **blank)
    for kind in ("TS", "IIS"):
        found = rating_results(reports[kind])
        [result] = by_role(found, "保證機構")
        assert result.status == S.REVIEW_REQUIRED
        assert f"評等表沒有 {code} 保證機構" in result.message
        assert {r.status for r in by_role(found, "發行機構")} == {S.PASS}


@pytest.mark.parametrize("code", BOTH)
def test_old_reference_sheet_needs_review_for_both_entities(tmp_path, code) -> None:
    reports = check(tmp_path, code, headers=OLD_REFERENCE_HEADERS)
    for kind in ("TS", "IIS"):
        found = rating_results(reports[kind])
        assert [r.status for r in found] == [S.REVIEW_REQUIRED] * 2, kind
        assert [r.message.split("沒有")[1][:4] for r in found] == ["發行機構", "保證機構"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (  # GS 發行機構
            "「A+」（標準普爾）、「A1」（穆迪）及「AA-」（惠譽）",
            {"標準普爾": "A+", "穆迪": "A1", "惠譽": "AA-"},
        ),
        (  # BARC（#243）：評等機構名稱後面還有「長期債務信用評等」
            "A+（標準普爾長期債務信用評等）/A1（穆迪長期債務信用評等）/AA-（惠譽長期債務信用評等）",
            {"標準普爾": "A+", "穆迪": "A1", "惠譽": "AA-"},
        ),
        (  # GS 保證機構（說明書）：DBRS、R&I 忽略
            "「BBB+」（標準普爾）、「A2」（穆迪）、「A」（惠譽）、「A（high）」（DBRS）及"
            "「A」（RatingandInvestmentInformation,Inc.（“R&I”））",
            {"標準普爾": "BBB+", "穆迪": "A2", "惠譽": "A"},
        ),
        (  # GS 保證機構（投資人須知）：R&I 只寫全名
            "「BBB+」（標準普爾）、「A2」（穆迪）、「A」（惠譽）、「A（high）」（DBRS）及「A」（RatingandInvestmentInformation,Inc.）",
            {"標準普爾": "BBB+", "穆迪": "A2", "惠譽": "A"},
        ),
    ],
)
def test_parse_reads_ratings_written_before_the_agency(body, expected) -> None:
    ratings, problem = parse(body, rating_first=True)
    assert problem == ""
    assert dict(ratings) == expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("穆迪A2/標準普爾A/惠譽AA-", {"穆迪": "A2", "標準普爾": "A", "惠譽": "AA-"}),  # Citi 投資人須知
        ("信用評等為標準普爾（S&P）A+", {"標準普爾": "A+"}),  # BNP 發行機構
        ("穆迪（Moody’s）為A1/標準普爾（S&P）為A+", {"穆迪": "A1", "標準普爾": "A+"}),  # BNP 投資人須知
        ("標準普爾信用評等A+、穆迪評等A2、惠譽評等A", {"標準普爾": "A+", "穆迪": "A2", "惠譽": "A"}),  # BBVA
        (  # GS 第二章 (vii)：評等在後面
            "經標準普爾（Standard&Poor’s）評為「BBB+」，經穆迪（Moody’s）評為「A2」，經惠譽（Fitch）評為「A」，"
            "經DBRS評定為「A（high）」及經R&I評為「A」",
            {"標準普爾": "BBB+", "穆迪": "A2", "惠譽": "A"},
        ),
    ],
)
def test_parse_reads_the_issuer_and_guarantor_wordings(body, expected) -> None:
    assert parse(body) == (tuple(expected.items()), "")


# ---------- 評等段落讀法 ----------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (  # HSBC：全形與半形括號、彎引號、`[`、最後一家前用「與」
            "[穆迪（Moody’s）信評Aa3/標準普爾(Standard&Poor’s)信評AA-與惠譽(Fitch)信評AA)",
            {"穆迪": "Aa3", "標準普爾": "AA-", "惠譽": "AA"},
        ),
        (  # HSBC 第二章：名稱後面還有公司名稱與英文全名
            "穆迪投資服務公司（Moody’sInvestorsService）給予香港商甲銀行長期信用評等為Aa3；惠譽國際（FitchCorporation）"
            "給與AA的等級；標準普爾公司（StandardandPoor’sCorporation）則給與AA-的等級",
            {"穆迪": "Aa3", "惠譽": "AA", "標準普爾": "AA-"},
        ),
        ("經穆迪(Moody's)評定為Aa3、經標準普爾(S&P)評定為A+及經惠譽(Fitch)評定為AA", None),
        ("標準普爾評等A+/穆迪評等A2/惠譽評等A+（高級優先債務）", {"標準普爾": "A+", "穆迪": "A2", "惠譽": "A+"}),
        ("穆迪（Moody's）Aa2，標準普爾（Standard&Poor's）為A+，惠譽（FitchRatings）AA", None),
        ("標準普爾評等A+、DBRS評等AA（high）、R&I評等A+", {"標準普爾": "A+"}),  # 不追蹤的評等機構不交出
        ("標準普爾評等A－", {"標準普爾": "A-"}),  # 全形減號
        ("Moody＇s評等Aa2/Standard&Poor＇s評等A+", {"穆迪": "Aa2", "標準普爾": "A+"}),  # 只寫英文、全形撇號
        ("穆迪A1/標準普爾A+/惠譽AA-", {"穆迪": "A1", "標準普爾": "A+", "惠譽": "AA-"}),  # CA：名稱直接接評等
        (  # JPM：英文名稱在括號裡、評等在後面
            "標準普爾（Standard&Poor's）AA-、穆迪（Moody’s）Aa2、惠譽（Fitch）AA",
            {"標準普爾": "AA-", "穆迪": "Aa2", "惠譽": "AA"},
        ),
        (  # Nomura：英文名稱寫在評等後面（#241）
            "穆迪Baa1（Moody's）、標準普爾BBB+（Standard&Poor's）、惠譽A-（Fitch）",
            {"穆迪": "Baa1", "標準普爾": "BBB+", "惠譽": "A-"},
        ),
        ("標準普爾A，穆迪A1，惠譽A+", {"標準普爾": "A", "穆迪": "A1", "惠譽": "A+"}),  # SG 第二章
    ],
)
def test_parse_reads_agency_and_rating_pairs(body, expected) -> None:
    ratings, problem = parse(body)
    assert problem == ""
    if expected is not None:
        assert dict(ratings) == expected
    else:
        assert {a for a, _ in ratings} == set(AGENCIES)


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ("穆迪（Moody's）/標準普爾評等A+", "穆迪找不到評等"),
        ("穆迪評等A1或A2", "穆迪有多個評等（A1、A2）"),
        ("標準普爾評等Aa2", "標準普爾評等「Aa2」不是標準普爾的評等寫法"),
        ("惠譽評等AA，Fitch評等AA-，標準普爾評等A", "惠譽寫了兩個不同的評等（AA、AA-）"),
        ("穆迪Baa1（Moody's）A1", "穆迪寫了兩個不同的評等（Baa1、A1）"),  # 評等後面的英文名稱後面又有評等
        ("長期債務信用評等為虛構評等", "段落裡找不到標準普爾、穆迪或惠譽"),
    ],
)
def test_parse_refuses_to_guess(body, problem) -> None:
    assert parse(body) == ((), problem)
