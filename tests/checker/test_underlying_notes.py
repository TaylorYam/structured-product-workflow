"""連結標的公司介紹（Issue #225）：說明書「連結標的之相關說明」每檔標的都有介紹、不是亂碼。

合成說明書經公開批量入口核對；各上手的段落切法（編號、名稱欄、段落）都有合成版面。商品、標的與介紹皆虛構。
"""

from __future__ import annotations

import importlib

import pytest

import hsbc_synth
from fcn_checker.parsers.underlying_notes import NameColumn, underlying_notes
from fcn_checker.schema import Line
from harness import MISMATCH, PASS, REVIEW, check_sheet
from pdf_writer import Edit
from reference_synth import build_reference_sheet, reference_row, ul_note

ISSUERS = ("barc", "bbva", "bnp", "ca", "citi", "dbs", "gs", "jpm", "ms", "natixis", "nomura", "sg", "ubs")
GARBLED = "(cid:12)(cid:34)(cid:56)(cid:78)(cid:90)(cid:11)"


def synth(name: str):
    return importlib.import_module(f"{name}_synth")


def notes(report) -> list:
    return [r for r in report.results if r.rule_id == "doc.underlying_notes"]


def per_note(report) -> list:
    return [r for r in report.results if r.rule_id == "doc.underlying_note"]


def check_hsbc(tmp_path, edits=()):
    s = hsbc_synth.Spec()
    pdf = hsbc_synth.build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s, edits=edits)
    return check_sheet(pdf, build_reference_sheet(tmp_path / "order.xlsx", [reference_row(s)]))


@pytest.mark.parametrize("name", ISSUERS)
def test_every_underlying_has_a_note(tmp_path, name):
    report = synth(name).check(tmp_path)
    [count] = notes(report)
    n = len(synth(name).Spec().underlyings)
    assert count.status == PASS and count.expected == count.actual == n
    assert [r.status for r in per_note(report)] == [PASS] * n


@pytest.mark.parametrize("name", ["ca", "gs", "jpm", "natixis", "ubs"])
def test_an_english_only_name_starts_its_own_note(tmp_path, name):
    """名稱欄只寫英文（左欄那一行沒有中文）的標的也自成一段，不併進上一檔（workflow #324）。"""
    m = synth(name)
    n = len(m.Spec().underlyings)
    u = m.Spec().underlyings[1]
    report = m.check(tmp_path, edits=[Edit(u.name, "Acme Robotics Corp", "ts.notes")])
    [count] = notes(report)
    assert count.status == PASS and count.actual == n
    assert [r.status for r in per_note(report)] == [PASS] * n


def test_a_wrapped_english_name_stays_in_its_note():
    """英文名稱換行：第二行在上一個左欄行正下方（中間夾著介紹欄的行）是同一檔，下一個英文名稱才開新的一段（workflow #324）。"""

    def line(x: float, y: float, text: str) -> Line:
        return Line(1, x, y, x + 100, y + 10, text)

    lines = [
        line(45, 80, "連結標的之相關說明"),
        line(45, 100, "Acme Robotics"),
        line(200, 100, "艾克米公司為虛構公司，從事虛構產品之研發、製造與銷售，"),
        line(45, 112, "Holdings Corp"),
        line(200, 112, "並提供相關服務。該公司服務全球客戶。"),
        line(45, 140, "Zeta Quantum Inc"),
        line(200, 140, "Zeta 為虛構公司，從事虛構產品之研發、製造與銷售，並提供相關服務。"),
    ]
    pf = underlying_notes(lines, NameColumn())
    first, second = pf.value
    assert "HoldingsCorp" in first.text and second.text.startswith("ZetaQuantum")


def test_hsbc_notes_are_split_by_paragraph(tmp_path):
    report = check_hsbc(tmp_path)
    [count] = notes(report)
    assert count.status == PASS and count.actual == len(hsbc_synth.Spec().underlyings)


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("citi", "連結標的2：{name}"),  # 編號
        ("jpm", "{name}"),  # 名稱欄
        ("ms", "(2)連結標的2："),  # 編號與介紹同一段
    ],
)
def test_a_missing_note_is_a_mismatch(tmp_path, name, label):
    m = synth(name)
    u = m.Spec().underlyings[1]
    edits = [Edit(ul_note(u), "", "ts.notes"), Edit(label.format(name=u.name), "", "ts.notes")]
    [count] = notes(m.check(tmp_path, edits=edits))
    n = len(m.Spec().underlyings)
    assert count.status == MISMATCH and count.expected == n and count.actual == n - 1
    assert f"標的 {n} 檔、公司介紹只有 {n - 1} 段" in count.message


def test_a_missing_note_names_the_underlying_when_every_note_has_its_ticker(tmp_path):
    """Nomura 每段都有 `彭博代碼：…`：寫出缺的是哪一檔。"""
    m = synth("nomura")
    s = m.Spec()
    u = s.underlyings[1]
    edits = [
        Edit(f"(2) 連結標的2：{u.name}", ""),
        Edit(ul_note(u), ""),
        Edit(f"彭博代碼：{u.ticker} Equity/交易所：{u.exchange}/相關交易所：所有交易所", ""),
    ]
    [count] = notes(m.check(tmp_path, edits=edits))
    assert count.status == MISMATCH and count.message.endswith(f"缺 {u.ticker}")


def test_a_missing_hsbc_paragraph_is_a_mismatch(tmp_path):
    u = hsbc_synth.Spec().underlyings[0]
    [count] = notes(check_hsbc(tmp_path, [Edit(ul_note(u), "", "ts.notes")]))
    assert count.status == MISMATCH and count.actual == len(hsbc_synth.Spec().underlyings) - 1


@pytest.mark.parametrize("name", ["barc", "nomura"])
def test_a_note_with_only_its_name_is_a_mismatch(tmp_path, name):
    """編號與名稱還在、介紹不見（Nomura 真實樣本：只有名稱與彭博代碼那一行）：段數相同，該段判不一致。"""
    m = synth(name)
    u = m.Spec().underlyings[0]
    report = m.check(tmp_path, edits=[Edit(ul_note(u), "")])
    assert notes(report)[0].status == PASS
    first, *rest = per_note(report)
    assert first.status == MISMATCH and first.reason_code == "note_empty" and first.document_evidence
    assert {r.status for r in rest} == {PASS}


@pytest.mark.parametrize("name", ["dbs", "ubs"])
def test_garbled_note_needs_review(tmp_path, name):
    m = synth(name)
    u = m.Spec().underlyings[0]
    report = m.check(tmp_path, edits=[Edit(ul_note(u), GARBLED, "ts.notes")])
    first = per_note(report)[0]
    assert first.status == REVIEW and first.reason_code == "garbled_text"
    assert notes(report)[0].status == PASS


def test_missing_section_needs_review(tmp_path):
    m = synth("citi")
    report = m.check(tmp_path, edits=[Edit("連結標的之相關說明或評等資料：", "第10項說明（不核對）")])
    [count] = notes(report)
    assert count.status == REVIEW and "找不到「連結標的之相關說明」" in count.message
    assert per_note(report) == []
