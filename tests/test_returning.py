"""放回資料夾：只透過 return_to_folders 驗證可觀察的結果（商品資料夾多了哪些檔、放回清單、批次資料夾不變）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import openpyxl
import pytest

from sp_workflow.preparation import prepare_batch
from sp_workflow.returning import (
    ALREADY_THERE,
    NOT_CHECKED_REASON,
    NOT_RETURNED,
    PENDING_ALREADY_THERE,
    PENDING_RETURNED,
    RETURNED,
    ReturnError,
    return_to_folders,
)
from synth import make_pdf, make_product_folder, snapshot, ts_lines

PREPARED_AT = dt.datetime(2026, 10, 19, 10, 30, 0)
RETURNED_AT = dt.datetime(2026, 10, 20, 9, 0, 0)
A = "029199990001"
B = "325199990002"
C = "147199990003"


def make_result_file(path: Path, passed: list[str | int], failed: list[str] = ()) -> Path:
    """合成核對結果檔：「回填後」第 1 列最新交易日、第 2 列表頭；「錯誤清單」列沒通過的 PDF。

    failed 的每一項是 PDF 檔名（例：`<TDCC>_IIS.pdf`），只寫 TDCC 時當成說明書沒通過。
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "回填後"
    ws.append(["最新交易日", "2026-10-13"])
    ws.append(["TS/IIS完成檢核", "交易日期", "Product", "TDCC Code", "ISIN Code"])
    for tdcc in passed:
        ws.append(["V", "2026-10-13", "FCN", tdcc, "XS0000000000"])
    errors = wb.create_sheet("錯誤清單")
    errors.append(["TDCC Code", "PDF 檔名", "PDF 頁碼", "錯訊"])  # 核對工具現行格式：一條錯訊一列
    for item in failed:
        name = item if item.lower().endswith(".pdf") else f"{item}_TS.pdf"
        errors.append([name[:12], name, 9, "UL_1 進場價對不起來：參考條件表 123.4500／說明書 123.4000"])
    wb.save(path)
    return path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "FCN"
    r.mkdir()
    return r


@pytest.fixture
def batch(root: Path, tmp_path: Path) -> Path:
    """A、B 兩個商品已準備好的批次資料夾。"""
    for tdcc in (A, B):
        make_product_folder(root, tdcc)
    out = tmp_path / "out"
    out.mkdir()
    return prepare_batch(f"{A} {B}", root, out, now=PREPARED_AT).batch_dir


def run(batch: Path, result: Path):
    """每次放回都確認批次資料夾裡的 PDF 與準備清單沒被改動。"""
    before = {k: v for k, v in snapshot(batch).items() if not k.startswith("放回清單")}
    receipt = return_to_folders(batch, result, now=RETURNED_AT)
    assert {k: v for k, v in snapshot(batch).items() if not k.startswith("放回清單")} == before
    return receipt


def outcome(receipt, tdcc: str):
    [o] = [o for o in receipt.outcomes if o.tdcc == tdcc]
    return o


def read_list(path: Path) -> list[dict[str, object]]:
    rows = list(openpyxl.load_workbook(path).active.iter_rows(values_only=True))
    return [dict(zip(rows[0], r, strict=True)) for r in rows[1:]]


# ---- 主要流程 ----


def test_copies_passed_products_back_to_their_product_folders(root: Path, batch: Path, tmp_path: Path) -> None:
    result = make_result_file(tmp_path / "核對結果.xlsx", [A], failed=[B])

    receipt = run(batch, result)

    o = outcome(receipt, A)
    assert (o.ts, o.iis) == (RETURNED, RETURNED)
    assert (root / A / f"{A}_TS.pdf").read_bytes() == (batch / f"{A}_TS.pdf").read_bytes()
    assert (root / A / f"{A}_IIS.pdf").read_bytes() == (batch / f"{A}_IIS.pdf").read_bytes()
    assert not (root / B / f"{B}_TS.pdf").exists()  # 沒通過的不用標準名


def test_only_rows_marked_v_are_returned(root: Path, batch: Path, tmp_path: Path) -> None:
    result = make_result_file(tmp_path / "核對結果.xlsx", [A, B])
    wb = openpyxl.load_workbook(result)
    wb["回填後"]["A4"] = None  # B 那列沒有打 V
    wb.save(result)

    receipt = run(batch, result)

    assert outcome(receipt, B).reason == NOT_CHECKED_REASON  # 不在回填後、也不在錯誤清單：還沒核對
    assert not (root / B / f"{B}_TS.pdf").exists()
    assert not (root / B / f"{B}_TS_待確認.pdf").exists()


def test_tdcc_stored_as_number_still_matches(root: Path, batch: Path, tmp_path: Path) -> None:
    result = make_result_file(tmp_path / "核對結果.xlsx", [int(A)])  # Excel 掉了前導零

    receipt = run(batch, result)

    assert outcome(receipt, A).ts == RETURNED
    assert (root / A / f"{A}_TS.pdf").exists()


def test_other_files_in_product_folder_are_untouched(root: Path, batch: Path, tmp_path: Path) -> None:
    before = snapshot(root / A)

    run(batch, make_result_file(tmp_path / "核對結果.xlsx", [A]))

    after = snapshot(root / A)
    assert set(after) - set(before) == {f"{A}_TS.pdf", f"{A}_IIS.pdf"}
    assert {k: after[k] for k in before} == before


# ---- 已在、衝突與重跑 ----


def test_identical_file_already_in_folder_counts_as_already_there(root: Path, batch: Path, tmp_path: Path) -> None:
    """UBS 上手本來就給標準檔名，內容和批次裡的一樣。"""
    (root / A / f"{A}_TS.pdf").write_bytes((batch / f"{A}_TS.pdf").read_bytes())

    o = outcome(run(batch, make_result_file(tmp_path / "r.xlsx", [A])), A)

    assert (o.ts, o.iis) == (ALREADY_THERE, RETURNED)


def test_different_file_with_same_name_blocks_the_whole_product(root: Path, batch: Path, tmp_path: Path) -> None:
    make_pdf(root / A / f"{A}_IIS.pdf", ["同事放的另一份"])
    before = snapshot(root / A)

    o = outcome(run(batch, make_result_file(tmp_path / "r.xlsx", [A])), A)

    assert (o.ts, o.iis) == (NOT_RETURNED, NOT_RETURNED)
    assert f"商品資料夾已有內容不同的 {A}_IIS.pdf" in o.reason
    assert snapshot(root / A) == before  # TS 也不放


def test_returning_again_skips_what_is_already_there(root: Path, batch: Path, tmp_path: Path) -> None:
    run(batch, make_result_file(tmp_path / "first.xlsx", [A]))

    receipt = return_to_folders(
        batch, make_result_file(tmp_path / "second.xlsx", [A, B]), now=RETURNED_AT + dt.timedelta(hours=1)
    )

    assert (outcome(receipt, A).ts, outcome(receipt, A).iis) == (ALREADY_THERE, ALREADY_THERE)
    assert (outcome(receipt, B).ts, outcome(receipt, B).iis) == (RETURNED, RETURNED)


# ---- 不放回 ----


def test_tdcc_not_in_this_batch_is_not_returned(batch: Path, tmp_path: Path) -> None:
    o = outcome(run(batch, make_result_file(tmp_path / "r.xlsx", [C])), C)

    assert (o.ts, o.iis) == (NOT_RETURNED, NOT_RETURNED)
    assert o.reason == "這批準備清單沒有這個商品"


def test_product_skipped_during_preparation_is_not_returned(root: Path, tmp_path: Path) -> None:
    make_pdf(root / C / "ts.pdf", ts_lines(C))  # 缺投資人須知，準備時跳過
    out = tmp_path / "out"
    out.mkdir()
    batch = prepare_batch(C, root, out, now=PREPARED_AT).batch_dir

    o = outcome(run(batch, make_result_file(tmp_path / "r.xlsx", [C])), C)

    assert o.ts == NOT_RETURNED
    assert o.reason == "準備時已跳過這個商品"


def test_changed_batch_file_is_not_returned(root: Path, batch: Path, tmp_path: Path) -> None:
    make_pdf(batch / f"{A}_TS.pdf", ["被換掉的檔"])
    result = make_result_file(tmp_path / "r.xlsx", [A])

    receipt = return_to_folders(batch, result, now=RETURNED_AT)

    o = outcome(receipt, A)
    assert (o.ts, o.iis) == (NOT_RETURNED, NOT_RETURNED)
    assert f"批次資料夾裡的 {A}_TS.pdf 和準備清單的指紋不符" in o.reason
    assert not (root / A / f"{A}_IIS.pdf").exists()


def test_missing_batch_file_is_not_returned(root: Path, batch: Path, tmp_path: Path) -> None:
    (batch / f"{A}_IIS.pdf").unlink()

    o = outcome(return_to_folders(batch, make_result_file(tmp_path / "r.xlsx", [A]), now=RETURNED_AT), A)

    assert o.iis == NOT_RETURNED
    assert f"批次資料夾裡找不到 {A}_IIS.pdf" in o.reason
    assert not (root / A / f"{A}_TS.pdf").exists()


def test_missing_product_folder_is_not_returned(root: Path, batch: Path, tmp_path: Path) -> None:
    for p in (root / A).iterdir():
        p.unlink()
    (root / A).rmdir()

    o = outcome(run(batch, make_result_file(tmp_path / "r.xlsx", [A])), A)

    assert o.ts == NOT_RETURNED
    assert "商品資料夾不存在" in o.reason


def test_failure_halfway_through_a_copy_leaves_nothing_behind(
    root: Path, batch: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """公槽斷線：IIS 寫了一半就失敗，商品資料夾不能留下半個檔或只有 TS。"""
    from sp_workflow import returning

    real_copy = returning.shutil.copyfile

    def broken_copy(src, dst):
        if f"{A}_IIS.pdf" in str(dst):
            Path(dst).write_bytes(b"half")
            raise OSError("網路中斷")
        return real_copy(src, dst)

    monkeypatch.setattr(returning.shutil, "copyfile", broken_copy)
    before = snapshot(root / A)

    o = outcome(run(batch, make_result_file(tmp_path / "r.xlsx", [A])), A)

    assert (o.ts, o.iis) == (NOT_RETURNED, NOT_RETURNED)
    assert snapshot(root / A) == before


def test_copy_failure_does_not_leave_half_a_pair(
    root: Path, batch: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sp_workflow import returning

    real_copy = returning.shutil.copyfile

    def flaky_copy(src, dst):
        if f"{A}_IIS.pdf" in str(dst):
            raise PermissionError("檔案被鎖住")
        return real_copy(src, dst)

    monkeypatch.setattr(returning.shutil, "copyfile", flaky_copy)

    o = outcome(run(batch, make_result_file(tmp_path / "r.xlsx", [A, B])), A)

    assert (o.ts, o.iis) == (NOT_RETURNED, NOT_RETURNED)
    assert "複製失敗" in o.reason
    assert not (root / A / f"{A}_TS.pdf").exists()


# ---- 整批停止 ----


def test_result_file_without_backfill_sheet_stops_everything(root: Path, batch: Path, tmp_path: Path) -> None:
    result = make_result_file(tmp_path / "r.xlsx", [A])
    wb = openpyxl.load_workbook(result)
    wb["回填後"].title = "其他"
    wb.save(result)

    with pytest.raises(ReturnError, match="找不到「回填後」工作表"):
        return_to_folders(batch, result, now=RETURNED_AT)
    assert not (root / A / f"{A}_TS.pdf").exists()


def test_result_file_without_required_columns_stops_everything(batch: Path, tmp_path: Path) -> None:
    result = make_result_file(tmp_path / "r.xlsx", [A])
    wb = openpyxl.load_workbook(result)
    wb["回填後"]["D2"] = "代號"
    wb.save(result)

    with pytest.raises(ReturnError, match="TDCC Code"):
        return_to_folders(batch, result, now=RETURNED_AT)


def test_folder_without_preparation_list_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ReturnError, match="準備清單"):
        return_to_folders(tmp_path, make_result_file(tmp_path / "r.xlsx", [A]), now=RETURNED_AT)


def test_broken_preparation_list_is_rejected_with_a_readable_message(batch: Path, tmp_path: Path) -> None:
    wb = openpyxl.load_workbook(batch / "準備清單.xlsx")
    wb.active["B2"] = "XX"  # 種類
    wb.save(batch / "準備清單.xlsx")

    with pytest.raises(ReturnError, match="準備清單第 2 列的種類看不懂"):
        return_to_folders(batch, make_result_file(tmp_path / "r.xlsx", [A]), now=RETURNED_AT)


def test_unreadable_result_file_is_rejected(batch: Path, tmp_path: Path) -> None:
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"not excel")

    with pytest.raises(ReturnError, match="讀不到核對結果檔"):
        return_to_folders(batch, broken, now=RETURNED_AT)


def test_existing_return_list_is_never_overwritten(batch: Path, tmp_path: Path) -> None:
    (batch / "放回清單_20261020-090000.xlsx").write_bytes(b"old")

    with pytest.raises(ReturnError, match="已存在"):
        return_to_folders(batch, make_result_file(tmp_path / "r.xlsx", [A]), now=RETURNED_AT)
    assert (batch / "放回清單_20261020-090000.xlsx").read_bytes() == b"old"


# ---- 放回清單 ----


def test_return_list_records_each_product(batch: Path, tmp_path: Path) -> None:
    receipt = run(batch, make_result_file(tmp_path / "r.xlsx", [A, C]))

    assert receipt.list_path == batch / "放回清單_20261020-090000.xlsx"
    rows = read_list(receipt.list_path)
    assert list(rows[0]) == ["TDCC", "TS", "IIS", "原因"]
    assert rows == [
        {"TDCC": A, "TS": RETURNED, "IIS": RETURNED, "原因": None},
        {"TDCC": C, "TS": NOT_RETURNED, "IIS": NOT_RETURNED, "原因": "這批準備清單沒有這個商品"},
        {"TDCC": B, "TS": NOT_RETURNED, "IIS": NOT_RETURNED, "原因": NOT_CHECKED_REASON},  # 準備了但還沒核對
    ]


# ---- 沒通過的以「_待確認」放回（#17）----


def test_failed_document_is_returned_with_pending_suffix(root: Path, batch: Path, tmp_path: Path) -> None:
    receipt = run(batch, make_result_file(tmp_path / "r.xlsx", [A], failed=[B]))

    o = outcome(receipt, B)
    assert (o.ts, o.iis) == (PENDING_RETURNED, RETURNED)  # 只有沒過的那份加待確認
    assert (root / B / f"{B}_TS_待確認.pdf").read_bytes() == (batch / f"{B}_TS.pdf").read_bytes()
    assert (root / B / f"{B}_IIS.pdf").read_bytes() == (batch / f"{B}_IIS.pdf").read_bytes()
    assert not (root / B / f"{B}_TS.pdf").exists()
    assert o.reason == (
        "核對沒通過（TS）：UL_1 進場價對不起來：參考條件表 123.4500／說明書 123.4000（第 9 頁）"
        f"；錯訊寫在 {B}_錯誤訊息_20261020-090000.xlsx"
    )


def test_both_failed_documents_get_the_suffix(root: Path, batch: Path, tmp_path: Path) -> None:
    result = make_result_file(tmp_path / "r.xlsx", [], failed=[f"{B}_TS.pdf", f"{B}_IIS.pdf"])

    o = outcome(run(batch, result), B)

    assert (o.ts, o.iis) == (PENDING_RETURNED, PENDING_RETURNED)
    assert (root / B / f"{B}_IIS_待確認.pdf").exists()
    assert "核對沒通過（TS）" in o.reason and "核對沒通過（IIS）" in o.reason


def test_returning_a_pending_file_again_counts_as_already_there(root: Path, batch: Path, tmp_path: Path) -> None:
    run(batch, make_result_file(tmp_path / "first.xlsx", [], failed=[B]))

    receipt = return_to_folders(
        batch, make_result_file(tmp_path / "second.xlsx", [], failed=[B]), now=RETURNED_AT + dt.timedelta(hours=1)
    )

    assert (outcome(receipt, B).ts, outcome(receipt, B).iis) == (PENDING_ALREADY_THERE, ALREADY_THERE)


def test_different_pending_file_with_same_name_blocks_the_product(root: Path, batch: Path, tmp_path: Path) -> None:
    make_pdf(root / B / f"{B}_TS_待確認.pdf", ["上一版的說明書"])
    before = snapshot(root / B)

    o = outcome(run(batch, make_result_file(tmp_path / "r.xlsx", [], failed=[B])), B)

    assert (o.ts, o.iis) == (NOT_RETURNED, NOT_RETURNED)
    assert f"商品資料夾已有內容不同的 {B}_TS_待確認.pdf" in o.reason
    assert snapshot(root / B) == before


def test_old_pending_file_is_kept_and_reported_when_the_product_passes(root: Path, batch: Path, tmp_path: Path) -> None:
    run(batch, make_result_file(tmp_path / "first.xlsx", [], failed=[B]))

    receipt = return_to_folders(
        batch, make_result_file(tmp_path / "second.xlsx", [B]), now=RETURNED_AT + dt.timedelta(hours=1)
    )

    o = outcome(receipt, B)
    assert (o.ts, o.iis) == (RETURNED, ALREADY_THERE)
    assert (root / B / f"{B}_TS.pdf").exists()
    assert (root / B / f"{B}_TS_待確認.pdf").exists()  # 不自動刪
    assert f"資料夾仍有舊的 {B}_TS_待確認.pdf，請確認後刪除" in o.reason


def test_failed_product_not_in_this_batch_is_not_returned(batch: Path, tmp_path: Path) -> None:
    o = outcome(run(batch, make_result_file(tmp_path / "r.xlsx", [], failed=[C])), C)

    assert (o.ts, o.iis, o.reason) == (NOT_RETURNED, NOT_RETURNED, "這批準備清單沒有這個商品")


def test_multi_line_error_messages_are_joined_on_one_line(root: Path, batch: Path, tmp_path: Path) -> None:
    result = make_result_file(tmp_path / "r.xlsx", [], failed=[B])
    wb = openpyxl.load_workbook(result)
    wb["錯誤清單"]["D2"] = "UL_1 進場價對不起來\n年利率對不起來"
    wb.save(result)

    o = outcome(run(batch, result), B)

    assert o.reason.startswith("核對沒通過（TS）：UL_1 進場價對不起來（第 9 頁）／年利率對不起來（第 9 頁）；")


def test_error_list_file_name_is_matched_case_insensitively(root: Path, batch: Path, tmp_path: Path) -> None:
    o = outcome(run(batch, make_result_file(tmp_path / "r.xlsx", [], failed=[f"{B}_TS.PDF"])), B)

    assert o.ts == PENDING_RETURNED


def test_unknown_file_name_on_error_list_is_reported(root: Path, batch: Path, tmp_path: Path) -> None:
    before = snapshot(root / B)

    o = outcome(run(batch, make_result_file(tmp_path / "r.xlsx", [], failed=[f"{B}_TS_revised.pdf"])), B)

    assert (o.ts, o.iis) == (NOT_RETURNED, NOT_RETURNED)
    assert o.reason == f"錯誤清單上的檔名對不到這批的檔：{B}_ts_revised.pdf"
    assert snapshot(root / B) == before


def test_conflict_keeps_the_check_errors_in_the_reason(root: Path, batch: Path, tmp_path: Path) -> None:
    make_pdf(root / B / f"{B}_TS_待確認.pdf", ["上一版的說明書"])

    o = outcome(run(batch, make_result_file(tmp_path / "r.xlsx", [], failed=[B])), B)

    assert "商品資料夾已有內容不同的" in o.reason and "核對沒通過（TS）" in o.reason


def test_existing_standard_file_of_a_failed_document_is_flagged(root: Path, batch: Path, tmp_path: Path) -> None:
    """UBS 上手本來就給標準檔名：沒通過時資料夾裡那份標準檔名不是確認版。"""
    (root / B / f"{B}_TS.pdf").write_bytes((batch / f"{B}_TS.pdf").read_bytes())

    o = outcome(run(batch, make_result_file(tmp_path / "r.xlsx", [], failed=[B])), B)

    assert o.ts == PENDING_RETURNED
    assert f"資料夾已有標準檔名的 {B}_TS.pdf，不是這次核對確認的版本" in o.reason


def test_outcome_flags_for_the_window(root: Path, batch: Path, tmp_path: Path) -> None:
    receipt = run(batch, make_result_file(tmp_path / "r.xlsx", [A], failed=[B]))

    assert (outcome(receipt, A).pending, outcome(receipt, A).blocked) == (False, False)
    assert (outcome(receipt, B).pending, outcome(receipt, B).blocked) == (True, False)


def test_result_file_without_error_sheet_stops_everything(root: Path, batch: Path, tmp_path: Path) -> None:
    result = make_result_file(tmp_path / "r.xlsx", [A])
    wb = openpyxl.load_workbook(result)
    del wb["錯誤清單"]
    wb.save(result)

    with pytest.raises(ReturnError, match="找不到「錯誤清單」工作表"):
        return_to_folders(batch, result, now=RETURNED_AT)
    assert not (root / A / f"{A}_TS.pdf").exists()


def test_error_sheet_without_required_columns_stops_everything(batch: Path, tmp_path: Path) -> None:
    result = make_result_file(tmp_path / "r.xlsx", [A])
    wb = openpyxl.load_workbook(result)
    wb["錯誤清單"]["B1"] = "檔名"
    wb["錯誤清單"]["A1"] = "代號"
    wb.save(result)

    with pytest.raises(ReturnError, match="TDCC Code、PDF 檔名"):
        return_to_folders(batch, result, now=RETURNED_AT)


# ---- 錯訊檔（#19）----


def read_error_book(path: Path) -> list[tuple]:
    return list(openpyxl.load_workbook(path).active.iter_rows(values_only=True))


def test_error_book_is_written_next_to_the_pending_file(root: Path, batch: Path, tmp_path: Path) -> None:
    result = make_result_file(tmp_path / "r.xlsx", [], failed=[B, f"{B}_IIS.pdf"])
    wb = openpyxl.load_workbook(result)
    wb["錯誤清單"]["D2"] = "UL_1 進場價對不起來\n年利率對不起來"
    wb.save(result)

    run(batch, result)

    book = root / B / f"{B}_錯誤訊息_20261020-090000.xlsx"
    assert read_error_book(book) == [
        ("TDCC", "種類", "放回檔名", "PDF 頁碼", "錯訊"),
        (B, "TS", f"{B}_TS_待確認.pdf", "9", "UL_1 進場價對不起來"),
        (B, "TS", f"{B}_TS_待確認.pdf", "9", "年利率對不起來"),
        (B, "IIS", f"{B}_IIS_待確認.pdf", "9", "UL_1 進場價對不起來：參考條件表 123.4500／說明書 123.4000"),
    ]


def test_returning_again_does_not_write_another_error_book(root: Path, batch: Path, tmp_path: Path) -> None:
    run(batch, make_result_file(tmp_path / "first.xlsx", [], failed=[B]))

    receipt = return_to_folders(
        batch, make_result_file(tmp_path / "second.xlsx", [], failed=[B]), now=RETURNED_AT + dt.timedelta(hours=1)
    )

    assert sorted(p.name for p in (root / B).glob("*_錯誤訊息_*.xlsx")) == [f"{B}_錯誤訊息_20261020-090000.xlsx"]
    assert "錯訊寫在" not in outcome(receipt, B).reason
    assert "舊的錯訊檔" not in outcome(receipt, B).reason  # 同樣的失敗重放，錯訊檔仍是現行的，不提醒


def test_passed_and_unchecked_products_get_no_error_book(root: Path, batch: Path, tmp_path: Path) -> None:
    run(batch, make_result_file(tmp_path / "r.xlsx", [A]))

    assert not list(root.rglob("*_錯誤訊息_*.xlsx"))


def test_existing_error_book_is_not_overwritten(root: Path, batch: Path, tmp_path: Path) -> None:
    book = root / B / f"{B}_錯誤訊息_20261020-090000.xlsx"
    book.write_bytes(b"old")

    o = outcome(run(batch, make_result_file(tmp_path / "r.xlsx", [], failed=[B])), B)

    assert o.ts == PENDING_RETURNED  # 待確認檔照樣放回
    assert book.read_bytes() == b"old"
    assert f"錯訊檔已存在，沒有覆蓋：{book.name}" in o.reason


def test_error_book_write_failure_keeps_the_returned_files(
    root: Path, batch: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_save(self, filename):
        if "錯誤訊息" in str(filename):
            raise PermissionError("資料夾唯讀")
        return real_save(self, filename)

    real_save = openpyxl.Workbook.save
    monkeypatch.setattr(openpyxl.Workbook, "save", broken_save)

    o = outcome(run(batch, make_result_file(tmp_path / "r.xlsx", [], failed=[B])), B)

    assert o.ts == PENDING_RETURNED
    assert (root / B / f"{B}_TS_待確認.pdf").exists()
    assert "錯訊檔寫不進去" in o.reason
    assert not list((root / B).glob("*.spw-tmp"))


def test_old_error_book_is_kept_and_reported_when_the_product_passes(root: Path, batch: Path, tmp_path: Path) -> None:
    run(batch, make_result_file(tmp_path / "first.xlsx", [], failed=[B]))

    receipt = return_to_folders(
        batch, make_result_file(tmp_path / "second.xlsx", [B]), now=RETURNED_AT + dt.timedelta(hours=1)
    )

    o = outcome(receipt, B)
    assert (root / B / f"{B}_錯誤訊息_20261020-090000.xlsx").exists()
    assert f"資料夾仍有舊的錯訊檔 {B}_錯誤訊息_20261020-090000.xlsx，請確認後刪除" in o.reason


def test_old_error_book_is_reported_when_one_document_now_passes(root: Path, batch: Path, tmp_path: Path) -> None:
    run(batch, make_result_file(tmp_path / "first.xlsx", [], failed=[B, f"{B}_IIS.pdf"]))

    receipt = return_to_folders(
        batch,
        make_result_file(tmp_path / "second.xlsx", [], failed=[f"{B}_IIS.pdf"]),
        now=RETURNED_AT + dt.timedelta(hours=1),
    )

    o = outcome(receipt, B)
    assert (o.ts, o.iis) == (RETURNED, PENDING_ALREADY_THERE)
    assert f"資料夾仍有舊的錯訊檔 {B}_錯誤訊息_20261020-090000.xlsx，請確認後刪除" in o.reason


def test_empty_error_message_still_gets_a_row(root: Path, batch: Path, tmp_path: Path) -> None:
    result = make_result_file(tmp_path / "r.xlsx", [], failed=[B])
    wb = openpyxl.load_workbook(result)
    wb["錯誤清單"]["D2"] = None
    wb.save(result)

    run(batch, result)

    rows = read_error_book(root / B / f"{B}_錯誤訊息_20261020-090000.xlsx")
    assert rows[1] == (B, "TS", f"{B}_TS_待確認.pdf", None, "（錯誤清單沒有寫錯訊）")


def test_rechecking_the_same_version_with_new_errors_writes_a_new_error_book(
    root: Path, batch: Path, tmp_path: Path
) -> None:
    """同一版重複核對（例如改過參考條件表）：錯訊不同就再寫一份，相同就不寫（#23）。"""
    run(batch, make_result_file(tmp_path / "first.xlsx", [], failed=[B]))
    second = make_result_file(tmp_path / "second.xlsx", [], failed=[B])
    wb = openpyxl.load_workbook(second)
    wb["錯誤清單"]["D2"] = "年利率對不起來"
    wb.save(second)

    receipt = return_to_folders(batch, second, now=RETURNED_AT + dt.timedelta(hours=1))

    o = outcome(receipt, B)
    assert o.ts == PENDING_ALREADY_THERE
    assert sorted(p.name for p in (root / B).glob("*_錯誤訊息_*.xlsx")) == [
        f"{B}_錯誤訊息_20261020-090000.xlsx",
        f"{B}_錯誤訊息_20261020-100000.xlsx",
    ]
    assert read_error_book(root / B / f"{B}_錯誤訊息_20261020-100000.xlsx")[1][4] == "年利率對不起來"
    assert f"資料夾仍有舊的錯訊檔 {B}_錯誤訊息_20261020-090000.xlsx" in o.reason


def test_error_message_starting_with_equals_is_kept_as_text(root: Path, batch: Path, tmp_path: Path) -> None:
    result = make_result_file(tmp_path / "r.xlsx", [], failed=[B])
    wb = openpyxl.load_workbook(result)
    wb["錯誤清單"]["D2"] = "'=期初價格對不起來"
    wb.save(result)

    run(batch, result)

    rows = read_error_book(root / B / f"{B}_錯誤訊息_20261020-090000.xlsx")
    assert rows[1][4] == "'=期初價格對不起來"


def test_copies_of_error_books_are_not_taken_as_the_latest(root: Path, batch: Path, tmp_path: Path) -> None:
    run(batch, make_result_file(tmp_path / "first.xlsx", [], failed=[B]))
    first = root / B / f"{B}_錯誤訊息_20261020-090000.xlsx"
    (root / B / f"{B}_錯誤訊息_20261020-090000 - 複本.xlsx").write_bytes(b"not an excel file")

    receipt = return_to_folders(
        batch, make_result_file(tmp_path / "second.xlsx", [], failed=[B]), now=RETURNED_AT + dt.timedelta(hours=1)
    )

    assert "錯訊寫在" not in outcome(receipt, B).reason  # 和最新一份（不是複本）相同，不再寫
    assert first.exists()


# ---- 錯誤清單一條錯訊一列、PDF 頁碼（#25）----


def write_error_rows(result: Path, rows: list[list[object]], header: list[str] | None = None) -> Path:
    """把錯誤清單換成指定的列（預設現行表頭）。"""
    wb = openpyxl.load_workbook(result)
    del wb["錯誤清單"]
    ws = wb.create_sheet("錯誤清單")
    ws.append(header or ["TDCC Code", "PDF 檔名", "PDF 頁碼", "錯訊"])
    for row in rows:
        ws.append(row)
    wb.save(result)
    return result


def test_all_error_rows_of_one_pdf_are_kept_with_pages(root: Path, batch: Path, tmp_path: Path) -> None:
    result = write_error_rows(
        make_result_file(tmp_path / "r.xlsx", []),
        [
            [B, f"{B}_TS.pdf", 9, "年利率對不起來"],
            [B, f"{B}_TS.pdf", "9、10", "UL_1 進場價對不起來"],
            [B, f"{B}_TS.pdf", None, "英文商品名稱對不起來"],
        ],
    )

    o = outcome(run(batch, result), B)

    assert o.reason.startswith(
        "核對沒通過（TS）：年利率對不起來（第 9 頁）／UL_1 進場價對不起來（第 9、10 頁）／英文商品名稱對不起來；"
    )
    assert read_error_book(root / B / f"{B}_錯誤訊息_20261020-090000.xlsx")[1:] == [
        (B, "TS", f"{B}_TS_待確認.pdf", "9", "年利率對不起來"),
        (B, "TS", f"{B}_TS_待確認.pdf", "9、10", "UL_1 進場價對不起來"),
        (B, "TS", f"{B}_TS_待確認.pdf", None, "英文商品名稱對不起來"),
    ]


def test_old_error_list_format_without_pages_still_works(root: Path, batch: Path, tmp_path: Path) -> None:
    result = write_error_rows(
        make_result_file(tmp_path / "r.xlsx", []),
        [[B, f"{B}_TS.pdf", "年利率對不起來\nUL_1 進場價對不起來"]],
        header=["TDCC Code", "PDF 檔名", "錯訊"],
    )

    o = outcome(run(batch, result), B)

    assert o.ts == PENDING_RETURNED
    assert o.reason.startswith("核對沒通過（TS）：年利率對不起來／UL_1 進場價對不起來；")
    rows = read_error_book(root / B / f"{B}_錯誤訊息_20261020-090000.xlsx")
    assert [r[3:] for r in rows[1:]] == [(None, "年利率對不起來"), (None, "UL_1 進場價對不起來")]


def test_same_text_with_different_bytes_counts_as_already_there(root: Path, batch: Path, tmp_path: Path) -> None:
    """MS 沿用 Word 舊版重複核對：重新轉出的 PDF 位元組不同但文字相同，不能當成內容不同的檔擋下（#25）。"""
    run(batch, make_result_file(tmp_path / "first.xlsx", [], failed=[B]))
    pending = root / B / f"{B}_TS_待確認.pdf"
    make_pdf(pending, ts_lines(B), pages=2)  # 文字相同、位元組不同（多一頁空白）
    assert pending.read_bytes() != (batch / f"{B}_TS.pdf").read_bytes()

    receipt = return_to_folders(
        batch, make_result_file(tmp_path / "second.xlsx", [], failed=[B]), now=RETURNED_AT + dt.timedelta(hours=1)
    )

    assert outcome(receipt, B).ts == PENDING_ALREADY_THERE
