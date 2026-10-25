"""受託機構設立日期、全文受託機構名稱、保本率敘述（Issue #226）：各上手共用的審查標準規則。

合成文件經公開批量入口核對（驗收條件的三種改字）；寫法的各分支直接打規則（rule_fixtures）。商品、標的與價格皆虛構。
"""

from __future__ import annotations

import datetime as dt

import pytest

import bnp_synth
import dbs_synth
import natixis_synth
from fcn_checker.ingestion import IngestionError
from fcn_checker.rules.review_standard import review_standard_rules
from fcn_checker.schema import ParsedField
from harness import CONFIG, MISMATCH, PASS, REVIEW, REVIEW_STANDARD, load_config
from pdf_writer import Edit
from rule_fixtures import context, term_sheet, with_text

NAME = CONFIG.review_standard.distributor_name
ESTABLISHED = "standard.distributor_established"
NAMES = "standard.distributor_names"
PROTECTION = "standard.principal_protection"


def of(report, rule_id: str) -> list:
    return [r for r in report.results if r.rule_id == rule_id]


def rules(*texts: str) -> list:
    """只有全文（每行一頁）的說明書跑一次審查標準規則。"""
    trade = ParsedField.missing("trade_date", "")
    return review_standard_rules(context(with_text(term_sheet(), *texts)), reference_trade=trade)


# ---------------------------------------------------------------- 驗收：合成文件


def test_synthetic_documents_pass(tmp_path):
    item = dbs_synth.check_item(tmp_path)
    [established] = of(item.report, ESTABLISHED)
    assert established.status == PASS and established.actual == dt.date(2000, 11, 20)
    for report in (item.report, item.partner.report):
        [names] = of(report, NAMES)
        assert names.status == PASS and names.actual == 0
        protection = of(report, PROTECTION)
        assert {r.field for r in protection} == {"principal_protection_rate", "principal_protection_conditions"}
        assert all(r.status == PASS for r in protection)
    assert of(item.partner.report, ESTABLISHED) == [], "投資人須知沒有設立日期"


def test_established_date_written_as_the_wrong_day_is_a_mismatch(tmp_path):
    """少數 DBS 說明書寫成 2000年8月2日。"""
    edit = Edit("(3) 設立日期：2000年11月20日", "(3) 設立日期：2000年8月2日", "ts.org")
    [res] = of(dbs_synth.check(tmp_path, edits=[edit]), ESTABLISHED)
    assert res.status == MISMATCH and res.actual == dt.date(2000, 8, 2) and res.expected == dt.date(2000, 11, 20)
    assert "2000年8月2日" in res.document_evidence[0].text


def test_one_character_off_in_a_warning_is_a_mismatch_with_page_and_text(tmp_path):
    wrong = NAME.replace("證券", "証券")
    edit = Edit(f"({NAME})所保證", f"({wrong})所保證", "ts.warn")
    report = natixis_synth.check(tmp_path, edits=[edit])
    [res] = of(report, NAMES)
    assert res.status == MISMATCH and res.actual == 1 and res.reason_code == "distributor_name_text"
    page = res.document_evidence[0].page
    assert f"第 {page} 頁「{wrong}」" in res.message
    assert wrong in "".join(e.text for e in res.document_evidence).replace(" ", "")


@pytest.mark.parametrize(
    ("value", "status", "reason"),
    [("100%。", MISMATCH, "principal_protected"), ("部分保本。", REVIEW, "wording_not_listed")],
)
def test_principal_protection_rate_must_say_not_protected(tmp_path, value, status, reason):
    """說明書與投資人須知都核對：保本率寫成 100% 判不一致；沒看過的寫法轉人工覆核。"""
    edit = Edit("計價貨幣本金保本率：不保本。", f"計價貨幣本金保本率：{value}", "")
    for item in bnp_synth.check_pair(tmp_path, edits=[edit]):
        [res] = [r for r in of(item.report, PROTECTION) if r.field == "principal_protection_rate"]
        assert (res.status, res.reason_code) == (status, reason)
        assert res.actual.startswith(value.rstrip("。"))


# ---------------------------------------------------------------- 設立日期


def _established(*texts: str) -> list:
    return [r for r in rules(*texts) if r.rule_id == ESTABLISHED]


def test_established_date_belongs_to_the_block_naming_the_distributor():
    """段落以最後一個「名稱」切開：前一家機構的地址、負責人不算進受託機構的段落（反之亦然）。"""
    found = _established(
        "(1)事業名稱：虛構總代理人(2)設立日期：2022年4月20日",
        f"(1)事業名稱：{NAME}(2)營業所在地：台北市(3)設立日期：西元2000 年11 月20 日(4)負責人姓名：林晋輝",
        "(1)事業名稱：虛構報價機構(2)設立日期：1925年8月7日",
    )
    assert [(r.status, r.actual) for r in found] == [(PASS, dt.date(2000, 11, 20))]
    assert found[0].document_evidence[0].page == 2


def test_established_date_ignores_the_section_title_and_reads_compatibility_characters():
    found = _established(
        "受託機構：設立日期及簡介",
        f"事業名稱：{NAME}設立日期：2000年11月20日",  # 「立」是 CJK 相容字
    )
    assert [(r.status, r.actual) for r in found] == [(PASS, dt.date(2000, 11, 20))]


def test_established_date_written_another_way_or_missing_requires_review():
    [odd] = _established(f"事業名稱：{NAME}設立日期：民國89年11月20日")
    assert (odd.status, odd.reason_code) == (REVIEW, "document_invalid")
    [missing] = _established(f"事業名稱：{NAME}")
    assert (missing.status, missing.reason_code) == (REVIEW, "document_missing")


# ---------------------------------------------------------------- 全文受託機構名稱


def _names(*texts: str):
    [res] = [r for r in rules(*texts) if r.rule_id == NAMES]
    return res


def test_every_name_listed_in_the_review_standard_passes_with_role_brackets():
    res = _names(
        f"本商品雖經{NAME}（受託機構）審查",
        f"而非由{NAME}(受託或銷售機構)所保證",
        "受託或銷售機構：玉山證券，電話",
        "玉山綜合證券股份有限公司(含國際證券業務分公司)",
    )
    assert res.status == PASS and res.actual == 0 and "共 4 處" in res.message


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("玉山綜合證券綜合股份有限公司依法不得承諾擔保投資本金", "玉山綜合證券綜合股份有限公司"),
        ("由玉山證券股份有限公司負責", "玉山證券股份有限公司"),  # 簡稱後面又接公司名稱
        ("玉山綜合証券股份有限公司（受託機構）", "玉山綜合証券股份有限公司"),
        ("玉山銀行。", "玉山銀行"),
    ],
)
def test_other_spellings_are_mismatches_listing_page_and_text(text, shown):
    res = _names(f"{NAME}審查", text)
    assert res.status == MISMATCH and res.actual == 1
    assert f"第 2 頁「{shown}」" in res.message
    assert [e.page for e in res.document_evidence] == [2]


# ---------------------------------------------------------------- 保本率敘述


def _protection(*texts: str) -> dict:
    return {r.field: r for r in rules(*texts) if r.rule_id == PROTECTION}


@pytest.mark.parametrize(
    "rate",
    [
        "不保本。本商品為不保障投資本金",
        "本商品不保本。",
        "無，本商品為不保障本金",
        "不適用8.主要給付",
        "0%。",
        "0.00%。",
    ],
)
def test_listed_non_protected_wordings_pass(rate):
    found = _protection(f"計價貨幣本金保本率：{rate}", "投資本金達成100％保本之各項條件：不適用，本商品不保本")
    assert all(r.status == PASS for r in found.values()), found


def test_label_variants_without_colon_or_with_events_are_read():
    """JPM 投資人須知沒有冒號；MS 說明書寫「各項事件」。"""
    found = _protection("原計價幣別本金保本率不保本●", "原計價幣別投資本金達成保本之各項事件：不適用。")
    assert {f: r.status for f, r in found.items()} == {
        "principal_protection_rate": PASS,
        "principal_protection_conditions": PASS,
    }


@pytest.mark.parametrize(
    ("rate", "status"),
    [("100%。", MISMATCH), ("0.5%。", MISMATCH), ("0.0%。", REVIEW), ("無條件保本。", REVIEW)],
)
def test_protected_rates_mismatch_and_unlisted_wordings_require_review(rate, status):
    found = _protection(f"計價貨幣本金保本率：{rate}", "投資本金達成保本率之各項條件：不適用。")
    assert found["principal_protection_rate"].status == status


def test_a_missing_item_requires_review():
    found = _protection("計價貨幣本金保本率：不保本。")
    assert found["principal_protection_conditions"].status == REVIEW
    assert found["principal_protection_conditions"].reason_code == "document_missing"


# ---------------------------------------------------------------- 審查標準設定


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("established = 2000-11-20", 'established = "2000/11/20"', "distributor.established 須是日期"),
        (
            'non_protected = ["不保本", "本商品不保本", "無", "不適用", "0%", "0.00%"]',
            "non_protected = []",
            "principal_protection.non_protected 須列出至少一個",
        ),
    ],
)
def test_review_standard_values_are_checked_when_loaded(tmp_path, old, new, message):
    text = REVIEW_STANDARD.read_text(encoding="utf-8")
    assert text.count(old) == 1
    path = tmp_path / "review_standard.toml"
    path.write_text(text.replace(old, new), encoding="utf-8")
    with pytest.raises(IngestionError) as raised:
        load_config(review_standard=path)
    assert raised.value.reason_code == "config_invalid" and message in str(raised.value)
