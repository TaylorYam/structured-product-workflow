"""本機 CA 真實樣本（Issue #274）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

斷言與探勘結論一致（docs/checker/rules/ca-check-rules.md §5）：5 份說明書與 5 份投資人須知都辨識為 CA 範本，全部通過並回填
（「受託投資」寫法在允許片語內、第三章受託機構多「綜合」不核對、投資人須知的相容字「年」正規化後照讀）。
參考條件表用 data/orders/核對表格式..xlsx（含 5 列 CA），加上初始評等表的評等欄；保證機構評等全部相符（#241）。
"""

from collections import Counter

import pytest

from fcn_checker.issuers import CA
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from real_data import CHECK_FORMAT, TERM_SHEETS, pdfs, rated_check_format, requires

pytestmark = [pytest.mark.real_samples, requires(TERM_SHEETS / "086*_TS.pdf", CHECK_FORMAT)]
PROBLEMS = (S.MISMATCH, S.REVIEW_REQUIRED, S.ERROR)


@pytest.fixture(scope="module")
def items(tmp_path_factory):
    sheet = rated_check_format(tmp_path_factory.mktemp("rated"))  # 加評等欄（#241）
    outcome = check_all(sheet, pdfs("086*.pdf"))
    return [i for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")], [
        i for i in outcome.items if i.term_sheet.name.endswith("_IIS.pdf")
    ]


def test_every_sample_is_recognised_as_ca(items):
    term_sheets, investor_sheets = items
    assert len(term_sheets) == 5 and len(investor_sheets) == 5
    assert all(i.report.template == CA.template_id for i in term_sheets)
    assert all(i.report.template == CA.iis.template_id for i in investor_sheets)


def test_every_sample_passes_and_is_filled(items):
    term_sheets, investor_sheets = items
    bad = Counter(
        (r.rule_id, r.reason_code)
        for i in term_sheets + investor_sheets
        for r in i.report.results
        if r.status in PROBLEMS
    )
    assert not bad, bad
    assert all(i.fills_sheet for i in term_sheets)
    # 保證機構評等（#241）：說明書 2 處、投資人須知 1 處，三家評等機構都和初始評等表相同
    for docs, n in ((term_sheets, 6), (investor_sheets, 3)):
        assert [sum(r.rule_id == "standard.credit_rating" for r in i.report.results) for i in docs] == [n] * 5


def test_template_variants_match_the_sample_table(items):
    """KO 觀察方式、記憶式、KI 型態與範本規格 §4.11 樣本總表一致。"""
    term_sheets, _ = items

    def actual(item, rule_id):
        return next((r.actual for r in item.report.results if r.rule_id == rule_id), None)

    found = Counter(
        (actual(i, "field.ko_observation"), actual(i, "field.ko_memory"), actual(i, "field.ki_type"))
        for i in term_sheets
    )
    assert found == Counter(
        {
            ("期末定日觀察", "記憶式", "無 KI"): 1,
            ("期末定日觀察", "非記憶式", "無 KI"): 1,
            ("期間每日觀察", "記憶式", "每日觀察"): 1,
            ("期間每日觀察", "非記憶式", "到期觀察"): 1,
            ("期間每日觀察", "記憶式", "無 KI"): 1,
        }
    )


def test_fixed_warning_and_allowed_phrases(items):
    """固定警語說明書 3 次（第一章第 2 項去【】也算）、投資人須知 1 次；「受託投資」都在允許片語內。"""
    term_sheets, investor_sheets = items
    for docs, n in ((term_sheets, 3), (investor_sheets, 1)):
        for i in docs:
            assert next(r.actual for r in i.report.results if r.rule_id == "standard.fixed_warning") == n
            assert next(r.status for r in i.report.results if r.rule_id == "standard.forbidden_wording") == S.PASS
