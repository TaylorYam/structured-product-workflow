from __future__ import annotations

from pathlib import Path

from sp_workflow.local_settings import load_folders, save_folders


def test_remembers_folders(tmp_path: Path) -> None:
    path = tmp_path / ".local" / "prepare_folders.json"
    save_folders({"root": r"\\share\FCN", "out": "D:/批次"}, path)

    assert load_folders(path) == {"root": r"\\share\FCN", "out": "D:/批次"}


def test_missing_or_broken_file_means_nothing_remembered(tmp_path: Path) -> None:
    assert load_folders(tmp_path / "none.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_folders(broken) == {}
    broken.write_text('["list"]', encoding="utf-8")
    assert load_folders(broken) == {}
