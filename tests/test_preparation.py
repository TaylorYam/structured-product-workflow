"""準備批次：只透過 prepare_batch 驗證可觀察的結果（批次資料夾、準備清單、商品資料夾不被改動）。"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import openpyxl
import pytest

from sp_workflow import preparation
from sp_workflow.preparation import PREPARED, SKIPPED, PreparationError, prepare_batch
from synth import (
    english_ts_lines,
    iis_lines,
    make_pdf,
    make_product_folder,
    snapshot,
    ts_lines,
)

NOW = dt.datetime(2026, 10, 19, 10, 30, 0)
A = "029199990001"
B = "325199990002"
C = "147199990003"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "FCN"
    r.mkdir()
    return r


@pytest.fixture
def out(tmp_path: Path) -> Path:
    o = tmp_path / "out"
    o.mkdir()
    return o


def run(text: str, root: Path, out: Path):
    """每次準備都確認商品資料夾（根目錄底下）的原始檔沒被改動。"""
    before = snapshot(root) if root.is_dir() else None
    receipt = prepare_batch(text, root, out, now=NOW)
    if before is not None:
        assert snapshot(root) == before
    return receipt


def outcome(receipt, tdcc: str):
    [o] = [o for o in receipt.outcomes if o.tdcc == tdcc]
    return o


def read_list(path: Path) -> list[dict[str, object]]:
    ws = openpyxl.load_workbook(path).active
    rows = list(ws.iter_rows(values_only=True))
    header = rows[0]
    return [dict(zip(header, r, strict=True)) for r in rows[1:]]


def batch_files(receipt) -> list[str]:
    return sorted(p.name for p in receipt.batch_dir.iterdir() if p.suffix == ".pdf")


# ---- 主要流程 ----


def test_prepares_standard_named_pdfs_in_one_flat_batch_folder(root: Path, out: Path) -> None:
    fa = make_product_folder(root, A)
    make_product_folder(root, B)

    receipt = run(f"{A}\n{B}", root, out)

    assert receipt.batch_dir == out / "待核對_20261019-103000"
    assert batch_files(receipt) == [f"{A}_IIS.pdf", f"{A}_TS.pdf", f"{B}_IIS.pdf", f"{B}_TS.pdf"]
    assert (receipt.batch_dir / f"{A}_TS.pdf").read_bytes() == (fa / f"XS0000000000_{A}_CN TS.pdf").read_bytes()
    assert (receipt.batch_dir / f"{A}_IIS.pdf").read_bytes() == (fa / f"XS0000000000_{A}_CN IIS.pdf").read_bytes()
    assert [o.status for o in receipt.outcomes] == [PREPARED, PREPARED]


def test_identifies_by_content_not_file_name(root: Path, out: Path) -> None:
    """CA 的 TS_ 開頭檔是英文版，中文說明書是 PS_ 開頭。"""
    folder = root / A
    make_pdf(folder / f"TS_Fictional_{A}.pdf", english_ts_lines(A))
    make_pdf(folder / f"PS_Fictional_{A}.pdf", ts_lines(A))
    make_pdf(folder / f"IIS_Fictional_{A}.pdf", iis_lines(A))

    receipt = run(A, root, out)

    assert outcome(receipt, A).status == PREPARED
    assert (receipt.batch_dir / f"{A}_TS.pdf").read_bytes() == (folder / f"PS_Fictional_{A}.pdf").read_bytes()


def test_title_must_be_near_the_top_of_page_one(root: Path, out: Path) -> None:
    """高齡聲明書第一頁很後面才提到中文產品說明書，不能被當成說明書（make_product_folder 已放一份）。"""
    make_product_folder(root, A)

    receipt = run(A, root, out)

    assert outcome(receipt, A).status == PREPARED


def test_finds_product_folder_at_any_depth(root: Path, out: Path) -> None:
    make_product_folder(root, A, sub="2026/10/BARC")

    receipt = run(A, root, out)

    assert outcome(receipt, A).status == PREPARED
    assert outcome(receipt, A).folder == root / "2026/10/BARC" / A


def test_accepts_upper_case_pdf_extension(root: Path, out: Path) -> None:
    folder = root / A
    make_pdf(folder / "CHINESE TS.PDF", ts_lines(A))
    make_pdf(folder / "CHINESE IIS.PDF", iis_lines(A))

    assert outcome(run(A, root, out), A).status == PREPARED


# ---- 商品代號 ----


def test_term_sheet_without_folder_tdcc_on_cover_is_not_a_candidate(root: Path, out: Path) -> None:
    folder = root / A
    make_pdf(folder / "ts.pdf", ts_lines(None))
    make_pdf(folder / "iis.pdf", iis_lines(A))

    receipt = run(A, root, out)

    o = outcome(receipt, A)
    assert o.status == SKIPPED
    assert "找不到中文產品說明書" in o.reason
    assert batch_files(receipt) == []


def test_term_sheet_of_another_product_is_not_a_candidate(root: Path, out: Path) -> None:
    folder = root / A
    make_pdf(folder / "ts.pdf", ts_lines(B))
    make_pdf(folder / "iis.pdf", iis_lines(A))
    (folder / "ts.docx").write_bytes(b"fake docx")

    reason = outcome(run(A, root, out), A).reason
    assert reason == "找不到中文產品說明書（ts.pdf 封面商品代號不符）"  # 有 PDF 只是代號不符，不能說成只有 Word


def test_investor_sheet_code_split_by_a_line_break_still_counts_as_another_product(root: Path, out: Path) -> None:
    folder = root / A
    make_pdf(folder / "ts.pdf", ts_lines(A))
    make_pdf(folder / "iis.pdf", ["第1頁，共4頁", "中文投資人須知", "TDCC商品代號325199", "990002"])

    assert "找不到中文投資人須知（iis.pdf 封面商品代號不符）" in outcome(run(A, root, out), A).reason


def test_pdf_is_used_when_word_copies_also_exist(root: Path, out: Path) -> None:
    folder = make_product_folder(root, A)
    (folder / f"XS0000000000_{A}_CN TS.docx").write_bytes(b"fake docx")
    (folder / f"XS0000000000_{A}_CN IIS.docx").write_bytes(b"fake docx")

    receipt = run(A, root, out)

    assert outcome(receipt, A).status == PREPARED
    assert batch_files(receipt) == [f"{A}_IIS.pdf", f"{A}_TS.pdf"]


def test_investor_sheet_without_any_code_is_accepted(root: Path, out: Path) -> None:
    """UBS、HSBC 的投資人須知整份沒有 TDCC。"""
    folder = root / A
    make_pdf(folder / "ts.pdf", ts_lines(A))
    make_pdf(folder / "iis.pdf", iis_lines(None))

    assert outcome(run(A, root, out), A).status == PREPARED


def test_investor_sheet_of_another_product_is_not_a_candidate(root: Path, out: Path) -> None:
    folder = root / A
    make_pdf(folder / "ts.pdf", ts_lines(A))
    make_pdf(folder / "iis.pdf", iis_lines(B))

    o = outcome(run(A, root, out), A)
    assert o.status == SKIPPED
    assert "找不到中文投資人須知" in o.reason


# ---- 跳過整個商品 ----


def test_two_term_sheet_candidates_skip_the_whole_product(root: Path, out: Path) -> None:
    folder = make_product_folder(root, A)
    make_pdf(folder / "revised TS.pdf", ts_lines(A))

    receipt = run(A, root, out)

    o = outcome(receipt, A)
    assert o.status == SKIPPED
    assert "中文產品說明書有 2 份候選" in o.reason
    assert "revised TS.pdf" in o.reason and f"XS0000000000_{A}_CN TS.pdf" in o.reason
    assert batch_files(receipt) == []


def test_missing_investor_sheet_skips_the_whole_product(root: Path, out: Path) -> None:
    folder = root / A
    make_pdf(folder / "ts.pdf", ts_lines(A))

    receipt = run(A, root, out)

    assert outcome(receipt, A).status == SKIPPED
    assert "找不到中文投資人須知" in outcome(receipt, A).reason
    assert batch_files(receipt) == []


def test_word_only_document_is_skipped_until_conversion_is_supported(root: Path, out: Path) -> None:
    folder = root / C
    make_pdf(folder / "iis.pdf", iis_lines(C))
    (folder / "2591320_ATS_Fixed Coupon.docx").write_bytes(b"fake docx")
    (folder / "2591320_TS_Fixed Coupon.DOC").write_bytes(b"fake doc")

    o = outcome(run(C, root, out), C)
    assert o.status == SKIPPED
    assert "只有 Word，尚未支援轉檔" in o.reason


def test_files_in_sub_folders_of_a_product_folder_are_ignored(root: Path, out: Path) -> None:
    folder = root / A
    make_pdf(folder / "ts.pdf", ts_lines(A))
    make_pdf(folder / "old" / "iis.pdf", iis_lines(A))

    assert "找不到中文投資人須知" in outcome(run(A, root, out), A).reason


def test_unreadable_pdf_is_not_a_candidate(root: Path, out: Path) -> None:
    folder = make_product_folder(root, A)
    (folder / "broken.pdf").write_bytes(b"not a pdf")

    assert outcome(run(A, root, out), A).status == PREPARED


def test_missing_product_folder(root: Path, out: Path) -> None:
    make_product_folder(root, A)

    receipt = run(f"{A}\n{B}", root, out)

    assert outcome(receipt, A).status == PREPARED
    o = outcome(receipt, B)
    assert o.status == SKIPPED
    assert o.reason == "找不到商品資料夾"


def test_duplicate_product_folders_are_skipped_with_their_paths(root: Path, out: Path) -> None:
    make_product_folder(root, A, sub="2026")
    make_product_folder(root, A, sub="舊資料")

    o = outcome(run(A, root, out), A)
    assert o.status == SKIPPED
    assert "找到 2 個同名商品資料夾" in o.reason
    assert str(root / "2026" / A) in o.reason and str(root / "舊資料" / A) in o.reason


# ---- 輸入 ----


def test_input_accepts_common_separators_and_merges_duplicates(root: Path, out: Path) -> None:
    for t in (A, B, C):
        make_product_folder(root, t)

    receipt = run(f" {A}, {B}、{C}\n{A}\t{B} ", root, out)

    assert [o.tdcc for o in receipt.outcomes] == [A, B, C]


def test_tokens_that_are_not_twelve_digits_are_listed(root: Path, out: Path) -> None:
    make_product_folder(root, A)

    full_width = "０２９１９９９９０００１"
    receipt = run(f"{A} 12345 ABC123456789 {full_width}", root, out)

    assert outcome(receipt, A).status == PREPARED
    assert outcome(receipt, "12345").reason == "不是 12 碼數字"
    assert outcome(receipt, "ABC123456789").status == SKIPPED
    assert outcome(receipt, full_width).reason == "不是 12 碼數字"


@pytest.mark.parametrize("text", ["", "  \n ,、 "])
def test_empty_input_is_rejected(root: Path, out: Path, text: str) -> None:
    with pytest.raises(PreparationError, match="沒有輸入 TDCC"):
        run(text, root, out)


def test_missing_root_or_output_folder_is_rejected(root: Path, out: Path, tmp_path: Path) -> None:
    with pytest.raises(PreparationError, match="根目錄不存在"):
        run(A, tmp_path / "nope", out)
    with pytest.raises(PreparationError, match="輸出位置不存在"):
        run(A, root, tmp_path / "nope")


def test_existing_batch_folder_is_never_overwritten(root: Path, out: Path) -> None:
    make_product_folder(root, A)
    (out / "待核對_20261019-103000").mkdir()

    with pytest.raises(PreparationError, match="批次資料夾已存在"):
        run(A, root, out)
    assert list((out / "待核對_20261019-103000").iterdir()) == []


# ---- 準備清單與原始檔 ----


def test_preparation_list_records_every_document(root: Path, out: Path) -> None:
    fa = make_product_folder(root, A)
    make_pdf(root / B / "ts.pdf", ts_lines(B))

    receipt = run(f"{A} {C} 999", root, out)

    assert receipt.list_path == receipt.batch_dir / "準備清單.xlsx"
    rows = read_list(receipt.list_path)
    assert list(rows[0]) == [
        "TDCC",
        "種類",
        "來源檔",
        "商品資料夾",
        "由 Word 轉檔",
        "輸出檔名",
        "SHA-256",
        "狀態",
        "原因",
    ]
    ts_row, iis_row, c_row, bad_row = rows
    assert ts_row["TDCC"] == A  # 文字，保留前導零
    assert ts_row["種類"] == "TS"
    assert ts_row["來源檔"] == str(fa / f"XS0000000000_{A}_CN TS.pdf")
    assert ts_row["商品資料夾"] == str(fa)
    assert ts_row["由 Word 轉檔"] == "否"
    assert ts_row["輸出檔名"] == f"{A}_TS.pdf"
    assert ts_row["SHA-256"] == hashlib.sha256((receipt.batch_dir / f"{A}_TS.pdf").read_bytes()).hexdigest()
    assert ts_row["狀態"] == PREPARED
    assert iis_row["種類"] == "IIS" and iis_row["輸出檔名"] == f"{A}_IIS.pdf"
    assert (c_row["TDCC"], c_row["狀態"], c_row["原因"]) == (C, SKIPPED, "找不到商品資料夾")
    assert (bad_row["TDCC"], bad_row["原因"]) == ("999", "不是 12 碼數字")


def test_skipped_product_rows_keep_the_candidate_found(root: Path, out: Path) -> None:
    make_pdf(root / B / "ts.pdf", ts_lines(B))

    receipt = run(B, root, out)

    ts_row, iis_row = read_list(receipt.list_path)
    assert ts_row["來源檔"] == str(root / B / "ts.pdf")
    assert ts_row["輸出檔名"] is None and ts_row["SHA-256"] is None
    assert ts_row["狀態"] == SKIPPED and "找不到中文投資人須知" in ts_row["原因"]
    assert iis_row["來源檔"] is None


def test_product_folders_are_never_modified(root: Path, out: Path) -> None:
    make_product_folder(root, A)
    folder = make_product_folder(root, B)
    make_pdf(folder / "revised TS.pdf", ts_lines(B))
    before = snapshot(root)

    run(f"{A} {B} {C}", root, out)

    assert snapshot(root) == before


def test_copy_failure_skips_the_product_without_leaving_half_a_pair(
    root: Path, out: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_product_folder(root, A)
    make_product_folder(root, B)
    real_copy = preparation.shutil.copyfile

    def flaky_copy(src, dst):
        if str(dst).endswith(f"{A}_IIS.pdf"):
            raise PermissionError("檔案被鎖住")
        return real_copy(src, dst)

    monkeypatch.setattr(preparation.shutil, "copyfile", flaky_copy)

    receipt = run(f"{A} {B}", root, out)

    assert outcome(receipt, A).status == SKIPPED
    assert "讀取或複製失敗" in outcome(receipt, A).reason
    assert outcome(receipt, B).status == PREPARED
    assert batch_files(receipt) == [f"{B}_IIS.pdf", f"{B}_TS.pdf"]
    assert receipt.list_path.exists()
