"""Word 轉 PDF（ADR 0002）。

轉檔器是可替換的一塊：收到一批（Word 路徑, PDF 路徑），全部轉好就回傳，任一份失敗就丟 `ConversionError`（訊息是給作業人員看的中文）。
正式版 `WordConverter` 另開子程式（`sp_workflow.word_worker`）用 pywin32 控制 Microsoft Word，
主程式只等子程式；逾時就結束子程式與它開的 Word，避免 Word 跳出對話框卡住整批。測試以假的轉檔器替換。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

Job = tuple[Path, Path]
Converter = Callable[[Sequence[Job]], None]


class ConversionError(Exception):
    """這批 Word 沒有全部轉成 PDF。"""


class WordConverter:
    """用 Microsoft Word 轉檔；一批共用一個 Word，逾時 = base_timeout + per_file_timeout × 份數（秒）。"""

    def __init__(self, base_timeout: float = 60, per_file_timeout: float = 60) -> None:
        self.base_timeout = base_timeout
        self.per_file_timeout = per_file_timeout

    def __call__(self, jobs: Sequence[Job]) -> None:
        if not jobs:
            return
        if sys.platform != "win32":
            raise ConversionError("Word 轉檔只能在安裝 Microsoft Word 的 Windows 上執行")
        timeout = self.base_timeout + self.per_file_timeout * len(jobs)
        with tempfile.TemporaryDirectory(prefix="spw-word-") as tmp:
            jobs_file, result_file, pid_file = (Path(tmp) / n for n in ("jobs.json", "result.json", "word_pid.txt"))
            jobs_file.write_text(json.dumps([[str(s), str(d)] for s, d in jobs], ensure_ascii=False), encoding="utf-8")
            command = [sys.executable, "-m", "sp_workflow.word_worker", str(jobs_file), str(result_file), str(pid_file)]
            proc = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                _kill_word(pid_file)
                raise ConversionError(f"Word 轉檔逾時（{timeout:.0f} 秒），Word 可能跳出了對話框") from None
            result = _read_result(result_file)
        if "fatal" in result:
            raise ConversionError(f"無法啟動 Microsoft Word：{result['fatal']}")
        failures = [
            (Path(s).name, r["error"]) for (s, _), r in zip(jobs, result["results"], strict=True) if not r["ok"]
        ]
        if failures:
            raise ConversionError("Word 轉檔失敗：" + "；".join(f"{name}：{error}" for name, error in failures))


def default_converter() -> Converter:
    return WordConverter()


def _read_result(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"fatal": "轉檔程式沒有留下結果"}


def _kill_word(pid_file: Path) -> None:
    """結束轉檔程式開的 Word（只結束它自己開的那個）。"""
    try:
        pids = [p for p in pid_file.read_text(encoding="utf-8").split(",") if p.strip().isdigit()]
    except OSError:
        return
    for pid in pids:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", pid], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
        )
