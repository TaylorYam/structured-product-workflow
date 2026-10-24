"""本機記住上次選的資料夾（不進 Git）；讀寫失敗一律當作沒有記錄，不影響使用。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

FILE_NAME = "prepare_folders.json"
# 本機還沒記住時選擇對話框先打開的資料夾（#209）；畫面先看記住的路徑，沒有才用這裡
DEFAULT_FOLDERS = {"order_sheet": r"G:\Bond Data\PM事項\0.常用工具\ELN每日起單建檔"}  # 起單建檔（#97）


def _default_path() -> Path:
    """沒有指定安裝資料夾時的位置：在 repo 裡開發（editable 安裝）用 repo 的 .local/，否則用使用者家目錄。

    雙擊安裝由啟動入口傳入安裝資料夾（`FolderMemory.under`），不走這裡。
    """
    repo = Path(__file__).resolve().parents[2]
    base = repo / ".local" if (repo / "pyproject.toml").is_file() else Path.home() / ".sp_workflow"
    return base / FILE_NAME


DEFAULT_PATH = _default_path()


def load_folders(path: Path = DEFAULT_PATH) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def save_folders(folders: dict[str, str], path: Path = DEFAULT_PATH) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(folders, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


class FolderMemory:
    """記住上次選的資料夾；路徑由啟動入口決定（安裝資料夾底下的 `.local/`）。"""

    def __init__(self, path: Path = DEFAULT_PATH, defaults: Mapping[str, str] = DEFAULT_FOLDERS) -> None:
        self.path = path
        self.defaults = dict(defaults)

    @classmethod
    def under(cls, install_root: Path) -> FolderMemory:
        return cls(install_root / ".local" / FILE_NAME)

    def load(self) -> dict[str, str]:
        return load_folders(self.path)

    def default(self, name: str) -> Path | None:
        """還沒記住 `name` 時選擇對話框先打開的資料夾（不看記住的值，由呼叫端先看）；沒有預設值時 None。"""
        folder = self.defaults.get(name)
        return Path(folder) if folder else None

    def remember(self, **folders: str) -> None:
        save_folders({**self.load(), **folders}, self.path)
