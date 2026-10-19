"""Word 轉 PDF 子程式（由 `conversion.WordConverter` 呼叫，不直接使用）。

用法：python -m sp_workflow.word_worker <jobs.json> <result.json> <word_pid.txt>
jobs.json 是 [[Word 路徑, PDF 路徑], ...]；結果寫進 result.json：
{"results": [{"ok": true} | {"ok": false, "error": "..."}]}，Word 啟動失敗時 {"fatal": "..."}。
Word 啟動後，用專屬標題找到它的視窗、取得 PID 寫進 word_pid.txt，主程式逾時時只結束這個 Word，不會動到作業人員自己開的 Word。
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

WD_EXPORT_FORMAT_PDF = 17
WD_DO_NOT_SAVE_CHANGES = 0
WD_ALERTS_NONE = 0
MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3  # 不執行巨集


@dataclass(frozen=True)
class WorkerFiles:
    """主程式與子程式交換的三個檔案。"""

    jobs: Path
    result: Path
    word_pid: Path

    @classmethod
    def inside(cls, folder: Path) -> WorkerFiles:
        return cls(folder / "jobs.json", folder / "result.json", folder / "word_pid.txt")

    def as_args(self) -> tuple[Path, Path, Path]:
        return (self.jobs, self.result, self.word_pid)


def _word_pid(caption: str) -> int | None:
    """標題含 caption 的 Word 主視窗（OpusApp）所屬的 PID。"""
    import win32gui
    import win32process

    found: list[int] = []

    def visit(hwnd: int, _: object) -> bool:
        if win32gui.GetClassName(hwnd) == "OpusApp" and caption in win32gui.GetWindowText(hwnd):
            found.append(win32process.GetWindowThreadProcessId(hwnd)[1])
        return True

    win32gui.EnumWindows(visit, None)
    return found[0] if len(found) == 1 else None


def convert(jobs: list[list[str]], pid_file: Path) -> dict:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        word = win32com.client.DispatchEx("Word.Application")
    except Exception as exc:
        return {"fatal": str(exc)}
    pid = None
    try:
        caption = f"spw-word-{uuid.uuid4().hex}"
        word.Caption = caption
        pid = _word_pid(caption)
        if pid is not None:
            pid_file.write_text(str(pid), encoding="utf-8")
        word.Visible = False
        word.DisplayAlerts = WD_ALERTS_NONE
        word.AutomationSecurity = MSO_AUTOMATION_SECURITY_FORCE_DISABLE
        return {"results": [_convert_one(word, source, target) for source, target in jobs]}
    finally:
        try:
            word.Quit(SaveChanges=WD_DO_NOT_SAVE_CHANGES)
        except Exception:
            if pid is not None:  # 關不掉就結束自己開的這個 Word，不留在背景
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
                )


def _convert_one(word, source: str, target: str) -> dict:
    try:
        doc = word.Documents.Open(
            FileName=source,
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
            PasswordDocument="spw-no-password",  # 有密碼的檔直接失敗，不跳出輸入視窗
            Visible=False,
            NoEncodingDialog=True,
        )
        try:
            doc.ExportAsFixedFormat(OutputFileName=target, ExportFormat=WD_EXPORT_FORMAT_PDF)
        finally:
            doc.Close(SaveChanges=WD_DO_NOT_SAVE_CHANGES)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True} if Path(target).is_file() else {"ok": False, "error": "沒有產生 PDF"}


def main(argv: list[str]) -> int:
    files = WorkerFiles(*(Path(a) for a in argv))
    jobs = json.loads(files.jobs.read_text(encoding="utf-8"))
    try:
        result = convert(jobs, files.word_pid)
    except Exception as exc:  # 任何意外都要留下結果給主程式
        result = {"fatal": repr(exc)}
    files.result.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
