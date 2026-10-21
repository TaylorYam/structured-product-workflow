"""`fcn-batch` 指令：批量入口（多份說明書＋參考條件表）的薄包裝。

`--database`（#68）：不給參考條件表，改依 PDF 檔名的 TDCC 從流程資料庫產生（連線設定讀 `.env`），
核對結果檔存好後寫回資料庫；寫回失敗只提示，核對結果檔照樣保留。
結束碼：0 = 整體 PASS；1 = 有 MISMATCH 或 REVIEW_REQUIRED（含未支援上手）；2 = ERROR。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .batch import BatchOutcome, failed_batch
from .check_config import DEFAULTS, ConfigPaths
from .ingestion import IngestionError
from .messages import STATUS_ZH
from .saving import SaveReceipt, run_batch
from .schema import CheckStatus

EXIT = {CheckStatus.PASS: 0, CheckStatus.MISMATCH: 1, CheckStatus.REVIEW_REQUIRED: 1, CheckStatus.ERROR: 2}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fcn-batch",
        description=(
            "以參考條件表（Excel 第一張工作表，必要欄都在即可）批量核對多份說明書 PDF；結果存成一份核對結果檔"
            "（「回填後」：說明書與投資人須知都通過、TS、IIS 打 V 並回填 ISIN、發行日、比價日的列；「錯誤清單」：沒通過的說明書與錯訊），"
            "原檔不動；另在執行目錄的 runtime/核對紀錄/ 寫一份內部核對紀錄（JSON）。"
        ),
    )
    p.add_argument(
        "paths",
        type=Path,
        nargs="+",
        metavar="參考條件表 說明書",
        help="參考條件表 Excel（.xlsx），後面接說明書 PDF（可多份；檔名前三碼為上手編號）；用 --database 時只給 PDF",
    )
    p.add_argument(
        "--database",
        action="store_true",
        help="條件從流程資料庫讀（依 PDF 檔名的 TDCC，連線設定讀 .env），存檔後把回填值寫回資料庫",
    )
    p.add_argument("--env-file", type=Path, default=None, help="資料庫連線設定檔（預設同 FCN Workflow 視窗）")
    p.add_argument(
        "--review-standard",
        type=Path,
        default=DEFAULTS.review_standard,
        help=f"審查標準設定檔（預設 {DEFAULTS.review_standard.as_posix()}）",
    )
    p.add_argument(
        "--reference-format",
        type=Path,
        default=DEFAULTS.reference_format,
        help=f"參考條件表格式設定檔（預設 {DEFAULTS.reference_format.as_posix()}）",
    )
    p.add_argument(
        "--issuer-prefixes",
        type=Path,
        default=DEFAULTS.issuer_prefixes,
        help=f"上手編號對照設定檔（預設 {DEFAULTS.issuer_prefixes.as_posix()}）",
    )
    p.add_argument(
        "--exchange-holidays",
        type=Path,
        default=DEFAULTS.exchange_holidays,
        help=f"交易所假日表設定檔（預設 {DEFAULTS.exchange_holidays.as_posix()}）",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path("runtime/reports"),
        help="核對結果檔的輸出資料夾（預設 runtime/reports）",
    )
    return p


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _run(args: argparse.Namespace) -> tuple[BatchOutcome, SaveReceipt]:
    reference = Path("資料庫") if args.database else args.reference_sheet
    try:
        config = ConfigPaths(
            args.review_standard, args.reference_format, args.issuer_prefixes, args.exchange_holidays
        ).load()
        if args.database:
            reference, absent = args.source.export(args.term_sheets, config.reference_format)
            return run_batch(config, reference, args.term_sheets, args.out, root=Path.cwd(), absent=absent)
    except IngestionError as e:  # 設定檔有問題或讀不到資料庫：整批錯誤，不核對任何說明書
        outcome = failed_batch(reference, e)
        return outcome, SaveReceipt.nothing_to_save(outcome)
    return run_batch(config, args.reference_sheet, args.term_sheets, args.out, root=Path.cwd())


def _database_source(args: argparse.Namespace):
    """流程資料庫的來源（sp_workflow）；`.env` 沒有密碼時在這裡問。產生的參考條件表放在輸出資料夾。"""
    from getpass import getpass

    from sp_workflow.check_source import WorkflowDatabase
    from sp_workflow.database import default_env_path, settings_from_env

    settings = settings_from_env(args.env_file or default_env_path())
    if settings.password is None:
        settings = settings.with_password(getpass(f"{settings.user}@{settings.host} 的資料庫密碼（不顯示）："))
    source = WorkflowDatabase(lambda: settings, args.out)
    source.ready()
    return source


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = _parser()
    args = parser.parse_args(argv)
    if args.database:
        args.reference_sheet, args.term_sheets = None, args.paths
    elif len(args.paths) < 2:
        parser.error("請給參考條件表與至少一份說明書 PDF（或用 --database 從資料庫讀條件）")
    else:
        args.reference_sheet, args.term_sheets = args.paths[0], args.paths[1:]
    write_back = ""
    try:
        if args.database:
            args.source = _database_source(args)
        outcome, receipt = _run(args)
        if args.database and receipt.output is not None:
            try:
                write_back = args.source.write_back(outcome, receipt)
            except Exception as e:  # 寫回失敗只提示：核對結果檔已存好（#68）
                write_back = f"[警告] 寫回資料庫失敗：{e}；核對結果檔已存好，排除後請重新核對並儲存"
    except Exception as e:  # 非預期錯誤：回報後以 ERROR 結束
        print(f"執行錯誤：{type(e).__name__}: {e}", file=sys.stderr)
        return EXIT[CheckStatus.ERROR]
    for item in outcome.items:
        reason = f"  {item.not_filled_reason}" if item.not_filled_reason else ""
        print(f"{item.status_label}  {item.term_sheet.name}{'  已回填' if receipt.filled(item) else ''}{reason}")
    for e in (*outcome.errors, *receipt.errors):
        print(f"  [ERROR] {e.field}：{e.message}", file=sys.stderr)
    print(f"整體狀態：{receipt.status.value}（{STATUS_ZH[receipt.status]}）")
    if receipt.output is not None:
        print(f"核對結果檔：{receipt.output}")
    if receipt.record is not None:
        print(f"核對紀錄：{receipt.record}")
    if write_back:
        print(write_back, file=sys.stderr if write_back.startswith("[警告]") else sys.stdout)
    return EXIT[receipt.status]


if __name__ == "__main__":
    raise SystemExit(main())
