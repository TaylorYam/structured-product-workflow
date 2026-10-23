"""共用小工具（#139）：SHA-256 與簡單的 Excel 表。資料全部虛構。"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import openpyxl
import pytest

from sp_workflow.file_tools import save_book, sha256_bytes, sha256_file, table_book, tdcc_as_text


def test_sha256_of_a_file_and_of_bytes(tmp_path: Path) -> None:
    data = b"fictional" * 300_000  # 比讀檔的區塊大
    path = tmp_path / "a.bin"
    path.write_bytes(data)

    assert sha256_file(path) == sha256_bytes(data) == hashlib.sha256(data).hexdigest()


def test_table_book_writes_bold_headers_widths_and_tdcc_as_text(tmp_path: Path) -> None:
    path = tmp_path / "表.xlsx"
    book = table_book("清單", ["TDCC", "說明"], [["000123456789", "x"]], widths=(16, 40), style=tdcc_as_text)
    save_book(book, path)

    sheet = openpyxl.load_workbook(path).active
    assert sheet.title == "清單"
    assert [c.value for c in sheet[1]] == ["TDCC", "說明"]
    assert all(c.font.bold for c in sheet[1])
    assert sheet["A2"].value == "000123456789"
    assert sheet["A2"].number_format == "@"
    assert sheet["B2"].number_format == "General"
    assert (sheet.column_dimensions["A"].width, sheet.column_dimensions["B"].width) == (16, 40)
    assert sheet.freeze_panes == "A2"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.skipif(sys.platform != "win32", reason="不覆蓋靠 Windows 的 os.rename（目的地已存在時失敗）")
def test_save_without_overwrite_keeps_the_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "表.xlsx"
    path.write_bytes(b"old")

    with pytest.raises(OSError):
        save_book(table_book("清單", ["TDCC"], []), path, overwrite=False)

    assert path.read_bytes() == b"old"
    assert not list(tmp_path.glob("*.tmp"))


def test_a_failed_save_leaves_no_temp_file_and_keeps_the_old_one(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "表.xlsx"
    path.write_bytes(b"old")

    def broken_replace(self, target):
        raise PermissionError("檔案開在 Excel")

    monkeypatch.setattr(Path, "replace", broken_replace)
    with pytest.raises(PermissionError):
        save_book(table_book("清單", ["TDCC"], [["1"]]), path)

    assert path.read_bytes() == b"old"
    assert not list(tmp_path.glob("*.tmp"))
