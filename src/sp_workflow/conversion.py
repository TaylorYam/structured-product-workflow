"""Word 轉 PDF（ADR 0002）。

轉檔器是可替換的一塊：收到一批（Word 路徑, PDF 路徑），逐份轉檔，回傳轉失敗的那幾份與原因（{Word 路徑: 原因}）；
整批都無法進行（Word 無法啟動、逾時）時丟 `ConversionError`（訊息是給作業人員看的中文）。
正式版 `WordConverter` 另開子程式（`sp_workflow.word_worker`）用 pywin32 控制 Microsoft Word，
主程式只等子程式；逾時就結束子程式與它開的那個 Word，避免 Word 跳出對話框卡住整批。測試以假的轉檔器替換。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from sp_workflow import word_worker

Job = tuple[Path, Path]
Converter = Callable[[Sequence[Job]], dict[Path, str]]


class ConversionError(Exception):
    """這批 Word 整批無法轉檔（Word 無法啟動或逾時）。"""


class WordConverter:
    """用 Microsoft Word 轉檔；一批共用一個 Word，逾時 = base_timeout + per_file_timeout × 份數（秒）。

    Word 一啟動就卡住（例如授權視窗）時，同一個轉檔器之後的批次直接失敗，不再每批各等一次逾時。
    """

    def __init__(self, base_timeout: float = 60, per_file_timeout: float = 60) -> None:
        self.base_timeout = base_timeout
        self.per_file_timeout = per_file_timeout
        self._startup_failure = ""

    def __call__(self, jobs: Sequence[Job]) -> dict[Path, str]:
        if not jobs:
            return {}
        if sys.platform != "win32":
            raise ConversionError("Word 轉檔只能在安裝 Microsoft Word 的 Windows 上執行")
        if self._startup_failure:
            raise ConversionError(self._startup_failure)
        timeout = self.base_timeout + self.per_file_timeout * len(jobs)
        with tempfile.TemporaryDirectory(prefix="spw-word-", ignore_cleanup_errors=True) as tmp:
            files = word_worker.WorkerFiles.inside(Path(tmp))
            files.jobs.write_text(json.dumps([[str(s), str(d)] for s, d in jobs], ensure_ascii=False), encoding="utf-8")
            command = [sys.executable, "-m", "sp_workflow.word_worker", *map(str, files.as_args())]
            proc = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                if not _kill_word(files.word_pid):
                    self._startup_failure = (
                        "Microsoft Word 啟動時卡住（可能跳出授權或安全模式視窗），本批其餘要轉檔的商品一併跳過；"
                        "請在工作管理員結束背景的 Word 後重跑"
                    )
                    raise ConversionError(self._startup_failure) from None
                raise ConversionError(f"Word 轉檔逾時（{timeout:.0f} 秒），Word 可能跳出了對話框") from None
            result = _read_result(files.result)
        if "fatal" in result:
            raise ConversionError(f"無法啟動 Microsoft Word：{result['fatal']}")
        return {source: r["error"] for (source, _), r in zip(jobs, result["results"], strict=True) if not r["ok"]}


def default_converter() -> Converter:
    return WordConverter()


def _read_result(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"fatal": "轉檔程式沒有留下結果"}


def _kill_word(pid_file: Path) -> bool:
    """結束轉檔程式自己開的那個 Word；不知道是哪個（還沒啟動完成）時不動任何 Word，回傳 False。"""
    try:
        pid = pid_file.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not pid.isdigit():
        return False
    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return True
