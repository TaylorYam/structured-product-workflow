"""上手註冊表的屬性（Issue #436）：上手底下掛範本，同一家之內由專到泛辨識。

- 同代號有多個範本時，每個商品家族的合成說明書與投資人須知在該上手剛好命中自己的範本（取代以前「FCN 母 parser 要拒絕
  家族文件」的測試：母 parser 不再寫反向條件）。
- 三份要一致的登記：註冊表的商品家族範本、`config/issuer_prefixes.toml` 的 `[auto_check_products]`、審查標準的
  `[issuer.<x>.product_names.<P>]`。
- `with_calendar` 搬到上手後，對 CA Super Bull、CA BEN 是 no-op（它只在沒有 Non-Call 時補第一個觀察日）。
"""

from __future__ import annotations

import importlib
import tomllib
from dataclasses import replace
from pathlib import Path

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import CA_BEN, CA_SUPER_BULL, REGISTRY, by_code, detect, detect_iis
from fcn_checker.products import FCN, FCN_EXPRESS
from harness import CONFIG, iis_path

ROOT = Path(__file__).resolve().parents[2]

# 每家有多個範本的上手：（合成器模組, Spec 類別名稱, 應命中的範本 ID）；FCN 用各合成器的 `Spec`
FAMILY_SPECS = {
    "HSBC": [("hsbc_synth", "Spec"), ("hsbc_synth", "SbSpec"), ("hsbc_synth", "BenSpec")],
    "MS": [("ms_synth", "Spec"), ("ms_ben_synth", "BenSpec")],
    "BNP": [("bnp_synth", "Spec"), ("bnp_synth", "SbSpec"), ("bnp_synth", "DacnSpec")],
    "SG": [("sg_synth", "Spec"), ("sg_synth", "DacnSpec")],
    "NOMURA": [("nomura_synth", "Spec"), ("nomura_synth", "DacnSpec")],
    "NATIXIS": [("natixis_synth", "Spec"), ("natixis_synth", "DacnSpec"), ("natixis_synth", "StepdownSpec")],
    "BARC": [("barc_synth", "Spec"), ("barc_synth", "StepdownSpec"), ("barc_synth", "DacnSpec")],
    "CA": [("ca_synth", "Spec"), ("ca_synth", "SbSpec"), ("ca_synth", "BenSpec")],
    "DBS": [("dbs_synth", "Spec"), ("dbs_synth", "DacnSpec")],
}


def _lines(path: Path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


def test_every_issuer_with_several_templates_is_covered():
    several = {i.code for i in REGISTRY if len(i.templates) > 1}
    assert several == set(FAMILY_SPECS)
    for code, specs in FAMILY_SPECS.items():
        assert len(specs) == len(by_code(code).templates), code


def test_issuers_are_registered_once_and_fcn_template_comes_last():
    codes = [i.code for i in REGISTRY]
    assert len(codes) == len(set(codes))
    for issuer in REGISTRY:
        *families, general = issuer.templates
        assert FCN in general.products, issuer.code
        assert all(FCN not in t.products for t in families), issuer.code


@pytest.mark.parametrize("code", sorted(FAMILY_SPECS))
def test_each_family_document_hits_exactly_its_own_template(tmp_path, code):
    """同代號的每個商品家族：合成說明書與投資人須知在這家上手剛好命中自己的範本，在整個註冊表也只有這一個命中。"""
    issuer = by_code(code)
    expected = [t.template_id for t in reversed(issuer.templates)]  # FAMILY_SPECS 依 FCN、家族的順序列
    for (module, spec_name), template_id in zip(FAMILY_SPECS[code], expected, strict=True):
        synth = importlib.import_module(module)
        spec = getattr(synth, spec_name)()
        folder = tmp_path / spec_name
        folder.mkdir()
        pdf = synth.build_pdf(folder / f"{spec.product_code}_TS.pdf", spec)
        for fn, path in ((detect, pdf), (detect_iis, iis_path(pdf))):
            lines = _lines(path)
            got, result = fn(lines, (issuer,))
            assert got is not None and got.template_id == template_id, (code, spec_name, path.name, result.message)
            whole, _ = fn(lines)
            assert whole is got, (code, spec_name, path.name)
            hits = [
                t.template_id for t in issuer.templates if (t.detect if fn is detect else t.iis.detect)(lines).matched
            ]
            assert hits[0] == template_id, (code, spec_name, path.name, hits)


def _toml(name: str) -> dict:
    return tomllib.loads((ROOT / "config" / name).read_text(encoding="utf-8"))


def test_family_templates_are_registered_in_both_tomls():
    """每個登記的商品家族範本，在 `[auto_check_products]` 開放給該上手，審查標準也有該上手該商品的名稱樣板。"""
    opened = _toml("issuer_prefixes.toml")["auto_check_products"]
    names = _toml("review_standard.toml")["issuer"]
    for issuer in REGISTRY:
        for template in issuer.templates:
            for product in template.products - {FCN, FCN_EXPRESS}:
                assert issuer.code in opened.get(product, ()), (issuer.code, product)
                assert product in names[issuer.code.lower()].get("product_names", {}), (issuer.code, product)


@pytest.mark.parametrize(("spec_name", "template"), [("SbSpec", CA_SUPER_BULL), ("BenSpec", CA_BEN)])
def test_ca_calendar_step_leaves_super_bull_and_ben_unchanged(tmp_path, spec_name, template):
    """CA 的 `with_calendar` 掛在上手、套在每個範本的讀出結果上：Super Bull、BEN 的 Non-Call ≥ 1，原樣交回。"""
    ca_synth = importlib.import_module("ca_synth")
    spec = getattr(ca_synth, spec_name)()
    pdf = ca_synth.build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    ts = template.read(_lines(pdf))
    assert by_code("CA").with_calendar(ts, CONFIG.exchange_calendar) is ts


@pytest.mark.real_samples
@pytest.mark.parametrize("family", ["super-bull", "ben"])
def test_ca_calendar_step_leaves_real_super_bull_and_ben_unchanged(family):
    """本機 CA Super Bull、BEN 真實樣本（CI 略過）：辨識到的說明書讀出結果經上手的 `with_calendar` 後原樣交回。"""
    from real_data import BEN, SUPER_BULL

    folder = SUPER_BULL if family == "super-bull" else BEN
    paths = sorted(folder.glob("086*_TS.pdf"))
    if not paths:
        pytest.skip(f"沒有 CA {family} 真實樣本")
    seen = 0
    for path in paths:
        lines = _lines(path)
        template, _ = detect(lines)
        if template not in (CA_SUPER_BULL, CA_BEN):
            continue
        ts = template.read(lines)
        assert by_code("CA").with_calendar(ts, CONFIG.exchange_calendar) is ts
        seen += 1
    assert seen > 0


# 各家族範本的標記（以前 FCN parser 的反向條件）：FCN 合成文件哪一種、標記寫法。Nomura 投資人須知的標記就是家族條件本身
# （有標記就命中或版本不支援），不會有「有標記、不符家族範本」的文件，不列
CLAIM_MARKS = [
    ("NOMURA", "nomura_synth", "pd", "實際日數："),
    ("SG", "sg_synth", "pd", "n(i)(i="),
    ("SG", "sg_synth", "iis", "[n(i)/N(i)]"),
    ("NATIXIS", "natixis_synth", "pd", "配息界限價格"),
    ("NATIXIS", "natixis_synth", "iis", "「變動配息金額」"),
    ("DBS", "dbs_synth", "pd", "其區間下限價格"),
    ("DBS", "dbs_synth", "iis", "其區間下限價格"),
    ("BARC", "barc_synth", "pd", "配息門檻"),
    ("BARC", "barc_synth", "iis", "配息門檻"),
    ("BNP", "bnp_synth", "pd", "定界價格"),
    ("BNP", "bnp_synth", "iis", "定界價格"),
]


@pytest.mark.parametrize(("code", "module", "kind", "mark"), CLAIM_MARKS)
def test_family_mark_keeps_the_document_from_falling_back_to_fcn(tmp_path, code, module, kind, mark):
    """FCN 文件加上家族標記、但不符家族範本：FCN 範本本身仍命中（母 parser 不寫反向條件），但同一家停在家族範本
    （`claimed`），整個註冊表沒有命中、範本未知——和 #436 以前 FCN parser 自己排除的結果相同。"""
    synth = importlib.import_module(module)
    spec = synth.Spec()
    pdf = synth.build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    lines = _lines(pdf if kind == "pd" else iis_path(pdf))
    issuer = by_code(code)
    *families, general = issuer.templates
    detector = (lambda t: t.detect) if kind == "pd" else (lambda t: t.iis.detect)
    find = detect if kind == "pd" else detect_iis
    assert detector(general)(lines).matched
    # 把標記加在某一行後面，找到「落在家族條件看的段落、FCN 仍命中」的那一行
    for i, line in enumerate(lines):
        marked = [*lines[:i], replace(line, text=line.text + mark), *lines[i + 1 :]]
        family = [detector(t)(marked) for t in families]
        if any(d.claimed for d in family) and detector(general)(marked).matched:
            break
    else:
        pytest.fail(f"{code} {kind}：找不到能放標記的行")
    assert not any(d.matched or d.unsupported for d in family)
    got, result = find(marked)
    assert got is None and result.reason_code == "template_unknown", result.message
