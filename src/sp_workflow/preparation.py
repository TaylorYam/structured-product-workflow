"""準備批次：依貼上的 TDCC 找商品資料夾，挑出中文說明書與投資人須知，改成標準檔名放進新的批次資料夾（#5、#7）。

對外只有 `prepare_batch`。商品資料夾只讀不寫；批次資料夾每次新開、已存在就不做。
同一種文件剛好一份候選才採用；說明書或投資人須知任一份找不到或有多份，整個商品跳過。
這一版只處理上手 PDF：某種文件沒有 PDF 候選、資料夾裡有 Word 時，原因寫「只有 Word，尚未支援轉檔」（#8 處理）。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import openpyxl
from openpyxl.styles import Font

from sp_workflow.documents import DocKind, classify_pdf

PREPARED = "已準備"
SKIPPED = "跳過"
LIST_NAME = "準備清單.xlsx"
LIST_COLUMNS = ("TDCC", "種類", "來源檔", "商品資料夾", "由 Word 轉檔", "輸出檔名", "SHA-256", "狀態", "原因")

_SEPARATORS = re.compile(r"[\s,，、;；]+")
_TDCC = re.compile(r"\d{12}")
_PDF = ".pdf"
_WORD = (".docx", ".doc")


class PreparationError(Exception):
    """整批無法開始（輸入或資料夾有問題）；訊息是給作業人員看的中文。"""


@dataclass(frozen=True)
class PreparedDocument:
    """一個商品的一種文件；跳過的商品若找到唯一候選也會記下來源檔，但沒有輸出。"""

    kind: DocKind
    source: Path | None = None
    output_name: str = ""
    sha256: str = ""
    from_word: bool = False


@dataclass(frozen=True)
class ProductOutcome:
    tdcc: str
    status: str
    reason: str = ""
    folder: Path | None = None
    documents: tuple[PreparedDocument, ...] = ()


@dataclass(frozen=True)
class PrepareReceipt:
    batch_dir: Path
    list_path: Path
    outcomes: tuple[ProductOutcome, ...]


def prepare_batch(text: str, root: Path, out_parent: Path, *, now: dt.datetime | None = None) -> PrepareReceipt:
    """準備一個批次：回傳批次資料夾、準備清單路徑與逐商品結果（依輸入順序）。"""
    tokens = _parse_tokens(text)
    if not tokens:
        raise PreparationError("沒有輸入 TDCC")
    if not root.is_dir():
        raise PreparationError(f"根目錄不存在：{root}")
    if not out_parent.is_dir():
        raise PreparationError(f"輸出位置不存在：{out_parent}")
    batch_dir = out_parent / f"待核對_{(now or dt.datetime.now()):%Y%m%d-%H%M%S}"
    try:
        batch_dir.mkdir()
    except FileExistsError:
        raise PreparationError(f"批次資料夾已存在：{batch_dir}") from None

    wanted = [t for t in tokens if _TDCC.fullmatch(t)]
    folders = _find_product_folders(root, set(wanted))
    outcomes = tuple(
        _prepare_product(t, folders.get(t, []), batch_dir)
        if _TDCC.fullmatch(t)
        else ProductOutcome(t, SKIPPED, "不是 12 碼數字")
        for t in tokens
    )
    list_path = batch_dir / LIST_NAME
    _write_list(list_path, outcomes)
    return PrepareReceipt(batch_dir, list_path, outcomes)


def _parse_tokens(text: str) -> list[str]:
    """換行、空白、逗號、頓號、分號都可以分隔；重複的只留第一次。"""
    return list(dict.fromkeys(t for t in _SEPARATORS.split(text) if t))


def _find_product_folders(root: Path, wanted: set[str]) -> dict[str, list[Path]]:
    """根目錄底下任何深度、名稱剛好等於 TDCC 的資料夾（只走一次樹）。"""
    found: dict[str, list[Path]] = {}
    if not wanted:
        return found
    for dirpath, dirnames, _ in os.walk(root):
        dirnames.sort()
        for name in dirnames:
            if name in wanted:
                found.setdefault(name, []).append(Path(dirpath) / name)
    return found


def _prepare_product(tdcc: str, folders: list[Path], batch_dir: Path) -> ProductOutcome:
    if not folders:
        return ProductOutcome(tdcc, SKIPPED, "找不到商品資料夾")
    if len(folders) > 1:
        paths = "、".join(str(f) for f in folders)
        return ProductOutcome(tdcc, SKIPPED, f"找到 {len(folders)} 個同名商品資料夾：{paths}")
    folder = folders[0]
    files = sorted((p for p in folder.iterdir() if p.is_file()), key=lambda p: p.name)
    pdfs = [p for p in files if p.suffix.lower() == _PDF]
    has_word = any(p.suffix.lower() in _WORD for p in files)

    candidates: dict[DocKind, list[Path]] = {kind: [] for kind in DocKind}
    for pdf in pdfs:
        kind = classify_pdf(pdf, tdcc)
        if kind is not None:
            candidates[kind].append(pdf)

    problems = [_problem(kind, candidates[kind], has_word) for kind in DocKind]
    problems = [p for p in problems if p]
    if problems:
        docs = tuple(PreparedDocument(kind, c[0] if len(c := candidates[kind]) == 1 else None) for kind in DocKind)
        return ProductOutcome(tdcc, SKIPPED, "；".join(problems), folder, docs)

    docs = []
    for kind in DocKind:
        [source] = candidates[kind]
        output_name = f"{tdcc}_{kind.value}.pdf"
        target = batch_dir / output_name
        shutil.copyfile(source, target)
        docs.append(PreparedDocument(kind, source, output_name, _sha256(target)))
    return ProductOutcome(tdcc, PREPARED, "", folder, tuple(docs))


def _problem(kind: DocKind, found: list[Path], has_word: bool) -> str:
    if len(found) == 1:
        return ""
    if found:
        names = "、".join(p.name for p in found)
        return f"{kind.label}有 {len(found)} 份候選：{names}"
    if has_word:
        return f"找不到{kind.label} PDF（只有 Word，尚未支援轉檔）"
    return f"找不到{kind.label}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _list_rows(outcomes: Iterable[ProductOutcome]) -> Iterable[tuple[str, ...]]:
    for o in outcomes:
        folder = str(o.folder) if o.folder else ""
        if not o.documents:
            yield (o.tdcc, "", "", folder, "", "", "", o.status, o.reason)
            continue
        for d in o.documents:
            source = str(d.source) if d.source else ""
            word = ("是" if d.from_word else "否") if d.source else ""
            yield (o.tdcc, d.kind.value, source, folder, word, d.output_name, d.sha256, o.status, o.reason)


def _write_list(path: Path, outcomes: Iterable[ProductOutcome]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "準備清單"
    ws.append(LIST_COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in _list_rows(outcomes):
        ws.append([v or None for v in row])
        ws.cell(ws.max_row, 1).number_format = "@"  # TDCC 是文字，保留前導零
    for col, width in zip("ABCDEFGHI", (15, 6, 60, 40, 12, 22, 66, 8, 60), strict=True):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    wb.save(path)
