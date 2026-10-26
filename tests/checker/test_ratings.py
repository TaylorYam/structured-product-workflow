"""信用評等核對（#240）：說明書與投資人須知寫的發行機構評等 = 參考條件表交易日當時的評等（rules/ratings.py）。

黑箱：各上手合成說明書＋投資人須知（評等段落照真實寫法，值虛構）＋參考條件表一列，經批量入口核對。
另有評等段落讀法（parsers/ratings.py 的 `parse`）的直接測試，涵蓋真實文件的全形／半形括號、彎引號與換行。
"""

from __future__ import annotations

from typing import Any

import pytest

import barc_synth
import dbs_synth
import hsbc_synth
import ms_synth
import natixis_synth
import ubs_synth
from fcn_checker.parsers.ratings import parse
from fcn_checker.rating_formats import FITCH, MOODYS, SP, canonical
from fcn_checker.rules.ratings import RULE_ID
from fcn_checker.schema import CheckReport
from fcn_checker.schema import CheckStatus as S
from harness import CONFIG, check_all, iis_path
from harness import field_problems as problems
from pdf_writer import Edit
from reference_synth import OLD_REFERENCE_HEADERS, REFERENCE_HEADERS, Ratings, build_reference_sheet, reference_row

# 上手 → （合成器, 參考條件表列, 說明書幾處評等段落）
ISSUERS: dict[str, tuple[Any, Any, int]] = {
    "HSBC": (hsbc_synth, reference_row, 2),  # 第一章第 3 項、第二章 (7)
    "MS": (ms_synth, reference_row, 2),  # 第一章第 3 項、第二章 (7)
    "DBS": (dbs_synth, dbs_synth.row, 2),  # 商品基本資料、相關機構事業概況 (7)
    "NATIXIS": (natixis_synth, natixis_synth.row, 3),  # 封面、第一章第 3 項、第二章 (7)
    "UBS": (ubs_synth, ubs_synth.row, 2),  # 商品基本資料三、相關機構事業概況一
}
AGENCIES = ("標準普爾", "穆迪", "惠譽")


def check(
    tmp_path,
    code: str,
    *,
    ratings: Ratings | None = None,
    edits=(),
    headers=REFERENCE_HEADERS,
    **overrides: Any,
) -> dict[str, CheckReport]:
    """合成說明書＋投資人須知＋參考條件表一列（`overrides` 以 Excel 欄名覆寫），回傳 {"TS"/"IIS": CheckReport}。"""
    synth, row, _ = ISSUERS[code]
    spec = synth.Spec() if ratings is None else synth.Spec().with_(ratings=ratings)
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
        assert len(found) == count * 3, kind  # 每處三家評等機構各一筆
        assert {r.status for r in found} == {S.PASS}
        assert {r.item.name.split("（")[0] for r in found} == {f"發行機構{a}評等" for a in AGENCIES}
        assert all(r.document_evidence for r in found)


@pytest.mark.parametrize("code", ISSUERS)
def test_one_agency_different_from_the_reference_sheet_is_a_mismatch(tmp_path, code) -> None:
    reports = check(tmp_path, code, **{"發行機構評等_惠譽": "A-"})  # 文件寫 AA
    for kind in ("TS", "IIS"):
        found = rating_results(reports[kind])
        bad = [r for r in found if r.status != S.PASS]
        assert bad and all(r.status == S.MISMATCH for r in bad), kind
        assert {r.item.name.split("（")[0] for r in bad} == {"發行機構惠譽評等"}
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


@pytest.mark.parametrize("code", ["HSBC", "NATIXIS"])
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


@pytest.mark.parametrize("code", ["UBS", "HSBC"])
def test_entity_missing_from_the_rating_table_needs_review(tmp_path, code) -> None:
    blank = {f"發行機構評等_{a}": None for a in AGENCIES}
    reports = check(tmp_path, code, **blank)
    for kind in ("TS", "IIS"):
        [result] = rating_results(reports[kind])
        assert result.status == S.REVIEW_REQUIRED
        assert f"評等表沒有 {code} 發行機構" in result.message
        assert {(rid, st) for rid, _, st in problems(reports[kind])} == {(RULE_ID, S.REVIEW_REQUIRED)}


@pytest.mark.parametrize("code", ISSUERS)
def test_old_reference_sheet_without_rating_columns_needs_review(tmp_path, code) -> None:
    """舊 Excel 參考條件表沒有評等欄：整批照常核對，評等轉人工覆核（不出錯、不算通過）。"""
    reports = check(tmp_path, code, headers=OLD_REFERENCE_HEADERS)
    for kind in ("TS", "IIS"):
        [result] = rating_results(reports[kind])
        assert result.status == S.REVIEW_REQUIRED
        assert "沒有發行機構評等欄（舊版參考條件表）" in result.message
        assert {(rid, st) for rid, _, st in problems(reports[kind])} == {(RULE_ID, S.REVIEW_REQUIRED)}


def test_unreadable_rating_needs_review(tmp_path) -> None:
    edit = Edit("惠譽評等AA (高級優先債務)。為避免", "惠譽評等未定 (高級優先債務)。為避免", "ts.art3")
    reports = check(tmp_path, "NATIXIS", edits=[edit])
    found = rating_results(reports["TS"])
    review = [r for r in found if r.status == S.REVIEW_REQUIRED]
    assert [r.item.name for r in review] == ["發行機構信用評等（第一章第 3 項）"]
    assert "惠譽找不到評等" in review[0].message
    assert {r.status for r in found if r not in review} == {S.PASS}  # 其他兩處照常比


def test_issuers_not_yet_supported_get_no_rating_results(tmp_path) -> None:
    """還沒做評等核對的上手（#241～#243）不跑這條規則，舊 Excel 也不會因此轉人工覆核。"""
    report = barc_synth.check(tmp_path, headers=OLD_REFERENCE_HEADERS)
    assert report.status == S.PASS, problems(report)
    assert not rating_results(report)


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
        ("長期債務信用評等為虛構評等", "段落裡找不到標準普爾、穆迪或惠譽"),
    ],
)
def test_parse_refuses_to_guess(body, problem) -> None:
    assert parse(body) == ((), problem)
