from __future__ import annotations

from pathlib import Path

from sp_workflow.local_settings import DEFAULT_FOLDERS, FolderMemory, load_folders, save_folders


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


def test_folder_memory_lives_under_the_install_root_and_merges_updates(tmp_path: Path) -> None:
    memory = FolderMemory.under(tmp_path)
    memory.remember(root="R:/FCN", out="D:/out")
    memory.remember(batch="D:/out/待核對_1")

    assert memory.path == tmp_path / ".local" / "prepare_folders.json"
    assert memory.load() == {"root": "R:/FCN", "out": "D:/out", "batch": "D:/out/待核對_1"}


def test_default_folders_are_only_used_before_anything_is_remembered(tmp_path: Path) -> None:
    """起單建檔的預設資料夾（#209）由 memory 提供，不寫進記錄檔；沒有預設值的名字是 None。"""
    memory = FolderMemory.under(tmp_path)

    assert memory.default("order_sheet") == Path(DEFAULT_FOLDERS["order_sheet"])
    assert "ELN每日起單建檔" in DEFAULT_FOLDERS["order_sheet"]
    assert memory.default("root") is None

    memory.remember(order_sheet="D:/ELN/起單建檔_20261015.xlsm")
    assert memory.load() == {"order_sheet": "D:/ELN/起單建檔_20261015.xlsm"}, "預設值不會被存進去"
