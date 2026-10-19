"""Word 轉 PDF 子程式（由 `conversion.WordConverter` 呼叫，不直接使用）。

用法：python -m sp_workflow.word_worker <jobs.json> <result.json> <word_pid.txt>
jobs.json 是 [[Word 路徑, PDF 路徑], ...]；結果寫進 result.json：
{"results": [{"ok": true} | {"ok": false, "error": "..."}]}，Word 啟動失敗時 {"fatal": "..."}。
啟動 Word 後立刻把它的 PID 寫進 word_pid.txt，主程式逾時時據此結束 Word。
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from pathlib import Path

WD_EXPORT_FORMAT_PDF = 17
WD_DO_NOT_SAVE_CHANGES = 0
WD_ALERTS_NONE = 0
MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3  # 不執行巨集


def _word_pids() -> set[str]:
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq WINWORD.EXE", "/FO", "CSV", "/NH"],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ).stdout.decode(errors="replace")
    return {row[1] for row in csv.reader(io.StringIO(out)) if len(row) > 1 and row[1].isdigit()}


def convert(jobs: list[list[str]], pid_file: Path) -> dict:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    before = _word_pids()
    try:
        word = win32com.client.DispatchEx("Word.Application")
    except Exception as exc:
        return {"fatal": str(exc)}
    pid_file.write_text(",".join(sorted(_word_pids() - before)), encoding="utf-8")
    results = []
    try:
        word.Visible = False
        word.DisplayAlerts = WD_ALERTS_NONE
        word.AutomationSecurity = MSO_AUTOMATION_SECURITY_FORCE_DISABLE
        for source, target in jobs:
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
                results.append({"ok": True} if Path(target).is_file() else {"ok": False, "error": "沒有產生 PDF"})
            except Exception as exc:
                results.append({"ok": False, "error": str(exc)})
    finally:
        try:
            word.Quit(SaveChanges=WD_DO_NOT_SAVE_CHANGES)
        except Exception:
            pass
    return {"results": results}


def main(argv: list[str]) -> int:
    jobs_file, result_file, pid_file = (Path(a) for a in argv)
    jobs = json.loads(jobs_file.read_text(encoding="utf-8"))
    try:
        result = convert(jobs, pid_file)
    except Exception as exc:  # 任何意外都要留下結果給主程式
        result = {"fatal": repr(exc)}
    result_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
