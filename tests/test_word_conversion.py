"""真的 Microsoft Word 轉檔：只在 Windows 且能啟動 Word 時執行，CI 自動略過。

測試用的 Word 檔由 Word 現場產生（內容虛構）。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pymupdf
import pytest

from sp_workflow.conversion import ConversionError, WordConverter
from sp_workflow.preparation import PREPARED, prepare_batch
from synth import iis_lines, snapshot, ts_lines

pytestmark = pytest.mark.word

WD_FORMAT_DOCUMENT_97 = 0  # 舊版 .doc
WD_FORMAT_DOCUMENT_DEFAULT = 16  # .docx
A = "147199990003"


@pytest.fixture(scope="module")
def word():
    if sys.platform != "win32":
        pytest.skip("不是 Windows")
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        pytest.skip("沒有 pywin32")
    pythoncom.CoInitialize()
    try:
        app = win32com.client.DispatchEx("Word.Application")
    except Exception:
        pytest.skip("無法啟動 Microsoft Word")
    app.Visible = False
    app.DisplayAlerts = 0
    yield app
    app.Quit(SaveChanges=0)


def save_word(word, path: Path, lines: list[str], file_format: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = word.Documents.Add()
    try:
        doc.Content.Text = "\r".join(lines)
        doc.SaveAs2(str(path), FileFormat=file_format)
    finally:
        doc.Close(SaveChanges=0)
    return path


def test_prepares_word_only_product_with_real_word(word, tmp_path: Path) -> None:
    root, out = tmp_path / "FCN", tmp_path / "out"
    out.mkdir()
    folder = root / A
    save_word(word, folder / "ATS.docx", ts_lines(A), WD_FORMAT_DOCUMENT_DEFAULT)
    save_word(word, folder / "IIS.DOC", iis_lines(A), WD_FORMAT_DOCUMENT_97)
    before = snapshot(root)

    receipt = prepare_batch(A, root, out)

    [o] = receipt.outcomes
    assert o.status == PREPARED, o.reason
    assert all(d.from_word for d in o.documents)
    with pymupdf.open(receipt.batch_dir / f"{A}_TS.pdf") as doc:
        assert "中文產品說明書" in doc[0].get_text()
    assert snapshot(root) == before  # 原始 Word 沒被動、沒有留下 Word 鎖定檔


def test_corrupt_word_file_fails_without_hanging(tmp_path: Path, word) -> None:
    source = tmp_path / "broken.docx"
    source.write_bytes(b"this is not a word document")
    started = time.monotonic()

    with pytest.raises(ConversionError):
        WordConverter(base_timeout=30, per_file_timeout=30)([(source, tmp_path / "broken.pdf")])

    assert time.monotonic() - started < 70
