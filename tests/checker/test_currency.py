"""幣別代碼與價格表的標的交易幣別（Issue #227；docs/checker/rules/shared-check-rules.md §9）：合成文件經批量入口核對。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from fcn_checker.config import load_review_standard
from fcn_checker.ingestion import IngestionError
from fcn_checker.schema import CheckStatus as S
from harness import CONFIG, REVIEW_STANDARD
from harness import results as rule
from nomura_synth import Spec, check_item
from pdf_writer import Edit

CODE, EXCHANGE = "standard.currency_code", "standard.underlying_currency"


def statuses(report, rule_id: str) -> list[S]:
    return [x.status for x in rule(report, rule_id)]


def test_defaults_pass_in_both_documents(tmp_path):
    ts = check_item(tmp_path, Spec())
    for report in (ts.report, ts.partner.report):
        assert statuses(report, CODE) == [S.PASS]
    assert statuses(ts.report, EXCHANGE) == [S.PASS]


def test_investor_sheet_code_must_match_the_chinese_currency(tmp_path):
    """投資人須知「日圓(USD)」→ 不一致（驗收條件）；說明書不受影響。"""
    spec = Spec(currency_zh="日圓")
    ts = check_item(tmp_path, spec, edits=[Edit("日圓(JPY)", "日圓(USD)", "iis")])
    [x] = rule(ts.partner.report, CODE)
    assert (x.status, x.expected, x.actual) == (S.MISMATCH, "日圓(JPY)", "日圓(USD)")
    assert statuses(ts.report, CODE) == [S.PASS]


def test_price_table_currency_must_be_the_exchange_currency(tmp_path):
    """NOMURA 日股標的表一列改成「美元」→ 不一致（驗收條件）。"""
    spec = Spec(market="JT", count=1)
    ts = check_item(tmp_path, spec, edits=[Edit("日圓", "美元", "ts.art7.table")])
    [x] = rule(ts.report, EXCHANGE)
    ticker = spec.underlyings[0].ticker
    assert (x.status, x.expected, x.actual) == (S.MISMATCH, f"{ticker}：JPY", f"{ticker}：美元")


def test_unknown_exchange_code_goes_to_manual_review(tmp_path):
    std = CONFIG.review_standard
    config = replace(CONFIG, review_standard=replace(std, exchange_currency={"UN": "USD"}))
    ts = check_item(tmp_path, Spec(), config=config)
    [x] = rule(ts.report, EXCHANGE)
    assert (x.status, x.reason_code) == (S.REVIEW_REQUIRED, "exchange_unknown")


def test_listed_code_aliases_pass(tmp_path):
    """人民幣寫 CNY（GS 樣本）：審查標準 [currency_code_aliases] 列的寫法通過，沒列的不一致。"""
    spec = Spec(currency_zh="人民幣")
    ts = check_item(tmp_path, spec, edits=[Edit("人民幣(CNH)", "人民幣(CNY)", "ts")])
    assert statuses(ts.report, CODE) == [S.PASS]
    (other := tmp_path / "other").mkdir()
    ts = check_item(other, spec, edits=[Edit("人民幣(CNH)", "人民幣(CNX)", "ts")])
    assert statuses(ts.report, CODE) == [S.MISMATCH]


@pytest.mark.parametrize("table, line", [("exchange_currency", 'UN = "usd"'), ("currency_code_aliases", 'CNH = "CNY"')])
def test_config_codes_must_be_iso_shaped(tmp_path: Path, table, line):
    text = REVIEW_STANDARD.read_text(encoding="utf-8").replace(f"[{table}]\n", f"[{table}]\n{line}\n", 1)
    text = text.replace('UN = "USD"', "", 1) if table == "exchange_currency" else text.replace("CNH = [", "XXX = [", 1)
    path = tmp_path / "review_standard.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(IngestionError, match=table):
        load_review_standard(path)
