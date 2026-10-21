"""匯出問題信件（#42）：把這次 Order 讀取有問題的商品，其商品資料夾裡的 `.msg` 複製到一個新資料夾，帶回除錯。

對外只有 `export_problem_mails` 與 `can_export`。有問題指狀態不是「已讀取」。
只複製、不改不刪商品資料夾；同一個商品資料夾只複製一次；個別檔案失敗不中斷整批。
另附這次的 Order 條件清單（含待確認原因；還沒匯出過清單時不附）。信件含真實交易與客戶資料，帶出公司要依公司的資料規定。
"""

from __future__ import annotations

import datetime as dt
import shutil
from dataclasses import dataclass
from pathlib import Path

from sp_workflow.order_terms import READ, OrderReceipt, OrderRow

EXPORT_PREFIX = "Order問題信件_"


class ExportError(Exception):
    """整批無法匯出；訊息是給作業人員看的中文。"""


@dataclass(frozen=True)
class ExportReceipt:
    folder: Path
    products: tuple[str, ...]  # 有問題的 TDCC（依清單順序）
    copied: int  # 複製了幾封信
    failures: tuple[str, ...] = ()  # 複製失敗的檔案與原因


def problem_rows(receipt: OrderReceipt) -> list[OrderRow]:
    return [row for row in receipt.rows if row.status != READ]


def can_export(receipt: OrderReceipt) -> bool:
    """有問題、而且找得到商品資料夾的商品至少一檔。"""
    return any(row.folders for row in problem_rows(receipt))


def export_problem_mails(receipt: OrderReceipt, out_parent: Path, *, now: dt.datetime | None = None) -> ExportReceipt:
    """在 out_parent 開 `Order問題信件_<日期時間>/`，每個問題商品資料夾的 `.msg` 複製到同名子資料夾。"""
    rows = problem_rows(receipt)
    if not any(row.folders for row in rows):
        raise ExportError("沒有需要匯出的問題信件（有問題的商品都找不到商品資料夾，或全部已讀取）")
    if not out_parent.is_dir():
        raise ExportError(f"輸出位置不存在：{out_parent}")
    target = out_parent / f"{EXPORT_PREFIX}{(now or dt.datetime.now()):%Y%m%d-%H%M%S}"
    try:
        target.mkdir()
    except FileExistsError:
        raise ExportError(f"資料夾已存在：{target}") from None

    copied = 0
    failures: list[str] = []
    done: set[Path] = set()
    used_names: set[str] = set()
    for row in rows:
        for folder in row.folders:
            if folder in done:
                continue  # 例：一封信下兩檔 TDCC，資料夾只複製一次
            done.add(folder)
            name = _unique(folder.name, used_names)
            try:
                mails = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".msg")
            except OSError as exc:
                failures.append(f"讀不到商品資料夾 {folder}：{exc}")
                continue
            dest = target / name
            dest.mkdir()
            for mail in mails:
                try:
                    shutil.copy2(mail, dest / mail.name)
                    copied += 1
                except OSError as exc:
                    failures.append(f"{mail}：{exc}")
    if receipt.list_path is not None:
        try:
            shutil.copy2(receipt.list_path, target / receipt.list_path.name)
        except OSError as exc:
            failures.append(f"{receipt.list_path}：{exc}")
    return ExportReceipt(target, tuple(row.tdcc for row in rows), copied, tuple(failures))


def _unique(name: str, used: set[str]) -> str:
    """同名商品資料夾（在不同位置）各放一個子資料夾：第二個起加 `_2`、`_3`。"""
    candidate, n = name, 1
    while candidate in used:
        n += 1
        candidate = f"{name}_{n}"
    used.add(candidate)
    return candidate
