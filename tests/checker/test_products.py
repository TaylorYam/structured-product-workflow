"""自動核對的商品種類（#302）：上手編號對照設定檔的 `[auto_check_products]`，以及批量核對攔下沒開放的商品種類
（參考條件表 Product 欄；同不支援樣態：不跑規則、不能人工放行）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from barc_synth import Spec, build_pdf
from fcn_checker.ingestion import IngestionError
from fcn_checker.saving import run_batch
from fcn_checker.schema import CheckStatus
from fcn_checker.variants import load_auto_check_products
from harness import CONFIG, ISSUER_PREFIXES, load_config, with_iis
from reference_synth import build_reference_sheet, reference_row

REVIEW = CheckStatus.REVIEW_REQUIRED
EXPRESS_OPENED = '\nFCN_Express = ["BARC", "CA", "HSBC"]\n'


def prefixes_with(tmp_path: Path, old: str, new: str) -> Path:
    text = ISSUER_PREFIXES.read_text(encoding="utf-8")
    assert old in text
    p = tmp_path / "prefixes.toml"
    p.write_text(text.replace(old, new), encoding="utf-8")
    return p


# ---------------------------------------------------------------- 設定檔


def test_repo_table_opens_fcn_everywhere_and_express_and_super_bull_for_their_issuers():
    issuers = frozenset(CONFIG.issuer_prefixes.values())

    expected = {
        "FCN": issuers,
        "FCN_Express": frozenset({"BARC", "CA", "HSBC"}),
        "Super Bull": frozenset({"BNP", "CA", "HSBC"}),
    }  # #304～#306、#310～#312、#314
    assert CONFIG.auto_check_products == expected


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (EXPRESS_OPENED, '\nFCN_Express = ["FAKE"]\n', "FCN_Express 有上手編號對照沒有的上手：FAKE"),
        (EXPRESS_OPENED, '\nFCN_Express = "BARC"\n', "FCN_Express 須是上手代號的清單"),
        ("[auto_check_products]", "[products]", "[auto_check_products]不存在"),
    ],
)
def test_table_problems_fail_config_load(tmp_path, old, new, message):
    path = prefixes_with(tmp_path, old, new)
    with pytest.raises(IngestionError) as e:
        load_auto_check_products(path, CONFIG.issuer_prefixes.values())
    assert message in str(e.value)
    with pytest.raises(IngestionError):
        load_config(issuer_prefixes=path)


# ---------------------------------------------------------------- 批量核對


def run(tmp_path: Path, row_changes: dict, config=CONFIG):
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec, **row_changes)])
    outcome, _ = run_batch(config, sheet, with_iis([pdf]), tmp_path / "reports", root=tmp_path, now=None)
    return outcome


def product_result(item):
    [r] = [r for r in item.report.results if r.rule_id == "batch.variant"]
    return r


@pytest.mark.parametrize("product", ["FCN_Express", "BEN"])
def test_products_not_opened_for_the_issuer_stop_both_documents(tmp_path, product):
    config = load_config(issuer_prefixes=prefixes_with(tmp_path, EXPRESS_OPENED, "\nFCN_Express = []\n"))
    outcome = run(tmp_path, {"Product": product}, config)
    message = f"樣態：BARC 還沒開放「{product}」自動核對（參考條件表 Product）；請人工核對，不能人工放行"

    for item in outcome.items:
        assert (product_result(item).status, product_result(item).reason_code) == (REVIEW, "variant_unsupported")
        assert item.problem_messages == (message,)
        assert not item.identification.checked
        assert not any(r.rule_id.startswith(("field.", "doc.", "iis.")) for r in item.report.results)
        with pytest.raises(IngestionError):
            outcome.release(item)


def test_super_bull_goes_to_manual_review_with_the_repo_table(tmp_path):
    """Super Bull 還沒開放任何上手（#310）：照不支援樣態轉人工，不跑規則。"""
    outcome = run(tmp_path, {"Product": "Super Bull"})
    message = "樣態：BARC 還沒開放「Super Bull」自動核對（參考條件表 Product）；請人工核對，不能人工放行"

    assert [item.problem_messages for item in outcome.items] == [(message,)] * len(outcome.items)


@pytest.mark.parametrize("product", ["FCN", " fcn ", None])
def test_fcn_and_a_blank_product_are_checked_as_usual(tmp_path, product):
    outcome = run(tmp_path, {"Product": product})

    assert all(i.status == CheckStatus.PASS for i in outcome.items), [i.problem_messages for i in outcome.items]


def test_opening_a_product_for_the_issuer_lets_it_through(tmp_path):
    outcome = run(tmp_path, {"Product": "FCN_Express"})  # 設定檔開放 BARC（#304）

    assert not any(r.rule_id == "batch.variant" for i in outcome.items for r in i.report.results)
