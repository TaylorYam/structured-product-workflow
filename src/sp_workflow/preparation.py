"""準備批次：依貼上的 TDCC 找商品資料夾，挑出中文說明書與投資人須知，改成標準檔名放進新的批次資料夾（#5、#7）。

對外只有 `prepare_batch`。商品資料夾只讀不寫；批次資料夾每次新開、已存在就不做。
同一種文件剛好一份候選才採用；說明書或投資人須知任一份找不到或有多份，整個商品跳過。
有上手 PDF 候選就只看 PDF；某種文件沒有 PDF 候選時，才把資料夾裡的 Word 複製到暫存區轉成 PDF 再判斷（#8、ADR 0002）。
Word 無法啟動或逾時，整個商品跳過；個別 Word 轉不了只影響它自己（需要它卻沒有其他候選時，原因寫出轉檔失敗）。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import openpyxl
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from sp_workflow.conversion import ConversionError, Converter, default_converter
from sp_workflow.documents import DocKind, read_pdf

PREPARED = "已準備"
SKIPPED = "跳過"
LIST_NAME = "準備清單.xlsx"
# 準備清單的欄（欄名, 欄寬）
LIST_COLUMNS = (
    ("TDCC", 15),
    ("種類", 6),
    ("來源檔", 60),
    ("商品資料夾", 40),
    ("由 Word 轉檔", 12),
    ("輸出檔名", 22),
    ("SHA-256", 66),
    ("狀態", 8),
    ("原因", 60),
)

_SEPARATORS = re.compile(r"[\s,，、;；]+")
_TDCC = re.compile(r"[0-9]{12}")  # 只認半形數字
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
class _Candidate:
    """判讀用的 PDF（上手 PDF 本身，或 Word 轉出的暫存 PDF）與它的來源檔。"""

    pdf: Path
    source: Path
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
    unreadable_dirs: tuple[Path, ...] = ()  # 搜尋商品資料夾時讀不到的資料夾（例如沒有權限）


def prepare_batch(
    text: str,
    root: Path,
    out_parent: Path,
    *,
    now: dt.datetime | None = None,
    converter: Converter | None = None,
) -> PrepareReceipt:
    """準備一個批次：回傳批次資料夾、準備清單路徑與逐商品結果（依輸入順序）。

    converter 預設用 Microsoft Word 轉檔（只有需要轉 Word 時才會啟動）。
    """
    convert = converter or default_converter()
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

    valid = {t for t in tokens if _TDCC.fullmatch(t)}
    folders, unreadable = _find_product_folders(root, valid)
    outcomes = tuple(
        _prepare_product(t, folders.get(t, []), unreadable, batch_dir, convert)
        if t in valid
        else ProductOutcome(t, SKIPPED, "不是 12 碼數字")
        for t in tokens
    )
    list_path = batch_dir / LIST_NAME
    _write_list(list_path, outcomes)
    return PrepareReceipt(batch_dir, list_path, outcomes, tuple(unreadable))


def _parse_tokens(text: str) -> list[str]:
    """換行、空白、逗號、頓號、分號都可以分隔；重複的只留第一次。"""
    return list(dict.fromkeys(t for t in _SEPARATORS.split(text) if t))


def _find_product_folders(root: Path, wanted: set[str]) -> tuple[dict[str, list[Path]], list[Path]]:
    """根目錄底下任何深度、名稱剛好等於 TDCC 的資料夾（只走一次樹），以及讀不到的資料夾。"""
    found: dict[str, list[Path]] = {}
    unreadable: list[Path] = []
    if not wanted:
        return found, unreadable
    for dirpath, dirnames, _ in os.walk(root, onerror=lambda e: unreadable.append(Path(e.filename))):
        dirnames.sort()
        for name in dirnames:
            if name in wanted:
                found.setdefault(name, []).append(Path(dirpath) / name)
    return found, unreadable


def _prepare_product(
    tdcc: str, folders: list[Path], unreadable: list[Path], batch_dir: Path, convert: Converter
) -> ProductOutcome:
    if not folders:
        reason = "找不到商品資料夾"
        if unreadable:
            reason += f"（搜尋時有 {len(unreadable)} 個資料夾讀不到，例如 {unreadable[0]}）"
        return ProductOutcome(tdcc, SKIPPED, reason)
    if len(folders) > 1:
        paths = "、".join(str(f) for f in folders)
        return ProductOutcome(tdcc, SKIPPED, f"找到 {len(folders)} 個同名商品資料夾：{paths}")
    folder = folders[0]
    try:
        return _prepare_folder(tdcc, folder, batch_dir, convert)
    except OSError as exc:
        for kind in DocKind:  # 兩份要嘛都輸出、要嘛都不輸出
            (batch_dir / _output_name(tdcc, kind)).unlink(missing_ok=True)
        return ProductOutcome(tdcc, SKIPPED, f"讀取或複製失敗：{exc}", folder)


def _prepare_folder(tdcc: str, folder: Path, batch_dir: Path, convert: Converter) -> ProductOutcome:
    files = sorted((p for p in folder.iterdir() if p.is_file()), key=lambda p: p.name)
    words = [p for p in files if p.suffix.lower() in _WORD and not p.name.startswith("~$")]  # ~$ 是 Word 鎖定檔
    candidates: dict[DocKind, list[_Candidate]] = {kind: [] for kind in DocKind}
    mismatched: dict[DocKind, list[_Candidate]] = {kind: [] for kind in DocKind}

    def read(candidate: _Candidate, kinds: tuple[DocKind, ...]) -> None:
        reading = read_pdf(candidate.pdf, tdcc)
        if reading.kind in kinds:
            (candidates if reading.code_matches else mismatched)[reading.kind].append(candidate)

    for pdf in (p for p in files if p.suffix.lower() == _PDF):
        read(_Candidate(pdf, pdf), tuple(DocKind))

    # Word 逾時被強制結束時暫存檔可能還被鎖住，清不掉也不影響結果
    with tempfile.TemporaryDirectory(prefix="spw-prepare-", ignore_cleanup_errors=True) as tmp:
        need_word = tuple(kind for kind in DocKind if not candidates[kind])
        failures: dict[Path, str] = {}
        if need_word and words:
            try:
                converted, failures = _convert_words(words, Path(tmp), convert)
            except ConversionError as exc:
                return ProductOutcome(tdcc, SKIPPED, str(exc), folder, _found_documents(candidates))
            for candidate in converted:
                read(candidate, need_word)

        problems = [_problem(kind, candidates[kind], mismatched[kind], failures) for kind in DocKind]
        problems = [p for p in problems if p]
        if problems:
            return ProductOutcome(tdcc, SKIPPED, "；".join(problems), folder, _found_documents(candidates))

        docs = []
        for kind in DocKind:
            [chosen] = candidates[kind]
            target = batch_dir / _output_name(tdcc, kind)
            shutil.copyfile(chosen.pdf, target)
            docs.append(PreparedDocument(kind, chosen.source, target.name, sha256(target), chosen.from_word))
    return ProductOutcome(tdcc, PREPARED, "", folder, tuple(docs))


def _convert_words(words: list[Path], tmp: Path, convert: Converter) -> tuple[list[_Candidate], dict[Path, str]]:
    """把 Word 複製到暫存區再轉 PDF（原始 Word 不動、Word 鎖定檔也不會留在商品資料夾）。

    回傳轉好的候選，以及轉不了的原始 Word 與原因。
    """
    jobs: dict[Path, tuple[Path, Path]] = {}
    for i, word in enumerate(words):
        work = tmp / str(i)
        work.mkdir()
        copy = work / word.name
        shutil.copyfile(word, copy)
        jobs[word] = (copy, work / "converted.pdf")
    reported = convert(list(jobs.values()))
    converted, failures = [], {}
    for word, (copy, pdf) in jobs.items():
        if copy in reported or not pdf.is_file():
            failures[word] = reported.get(copy, "沒有產生 PDF")
        else:
            converted.append(_Candidate(pdf, word, from_word=True))
    return converted, failures


def _found_documents(candidates: dict[DocKind, list[_Candidate]]) -> tuple[PreparedDocument, ...]:
    """跳過的商品：有唯一候選的那種文件記下來源檔，沒有輸出。"""
    return tuple(
        PreparedDocument(kind, c[0].source, from_word=c[0].from_word)
        if len(c := candidates[kind]) == 1
        else PreparedDocument(kind)
        for kind in DocKind
    )


def _output_name(tdcc: str, kind: DocKind) -> str:
    return f"{tdcc}_{kind.value}.pdf"


def _problem(kind: DocKind, found: list[_Candidate], mismatched: list[_Candidate], failures: dict[Path, str]) -> str:
    if len(found) == 1:
        return ""
    if found:
        return f"{kind.label}有 {len(found)} 份候選：{_names(found)}"
    if mismatched:
        return f"找不到{kind.label}（{_names(mismatched)} 封面商品代號不符）"
    if failures:
        details = "；".join(f"{word.name}：{reason}" for word, reason in failures.items())
        return f"找不到{kind.label}（有 Word 轉檔失敗：{details}）"
    return f"找不到{kind.label}"


def _names(candidates: list[_Candidate]) -> str:
    return "、".join(c.source.name for c in candidates)


def sha256(path: Path) -> str:
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
    ws.append([name for name, _ in LIST_COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in _list_rows(outcomes):
        ws.append([v or None for v in row])
        ws.cell(ws.max_row, 1).number_format = "@"  # TDCC 是文字，保留前導零
    for i, (_, width) in enumerate(LIST_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    wb.save(path)
