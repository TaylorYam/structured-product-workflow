"""本機記住上次選的資料夾（不進 Git）；讀寫失敗一律當作沒有記錄，不影響使用。"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / ".local" / "prepare_folders.json"


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
