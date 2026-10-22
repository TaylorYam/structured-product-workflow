"""審查標準設定檔一次性轉換：舊版面（每種基準一個表、底下分上手）→ 每家上手一張表 [issuer.<上手>]（Issue #128）。

逐行轉換以保留註解：舊表裡每個上手的值（連同前面的註解）搬到該上手的表，改用 `config.LEGACY_TABLES` 的欄位名稱；
舊表開頭的註解（說明這種基準的寫法）收在上手表前的欄位說明。共用的表、值與註解原樣不動。
轉換後先確認新內容與舊內容依 `LEGACY_TABLES` 搬移後的值完全相同、而且載入得了，才取代原檔；原檔另存 `<檔名>.bak`。
"""

from __future__ import annotations

import argparse
import copy
import os
import re
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .check_config import DEFAULTS
from .config import ISSUER_FIELDS, LEGACY_TABLES, MIGRATE_COMMAND, legacy_tables, load_review_standard
from .ingestion import IngestionError

_HEADER = re.compile(r"^\[([^\[\]]+)\][ \t]*(#.*)?$")
_KEY = re.compile(r"""^(\s*)(?:"[^"]*"|'[^']*'|[A-Za-z0-9_-]+)(\s*=)""")


def _fail(message: str) -> IngestionError:
    return IngestionError("config_invalid", message)


@dataclass
class _Entry:
    """一個鍵值（可能跨好幾行）與它前面的註解。"""

    key: str
    lines: list[str]
    comments: list[str]


@dataclass
class _Section:
    """一個表：表頭前的註解、表頭（根部為 None）與表內的鍵值。"""

    path: tuple[str, ...] | None
    header: str
    comments: list[str] = field(default_factory=list)
    entries: list[_Entry] = field(default_factory=list)


def _sections(lines: list[str]) -> tuple[list[_Section], list[str]]:
    """把設定檔切成表；鍵值讀到能單獨解析為止（多行字串、跨行陣列）。回傳表與檔尾的註解。"""
    sections = [_Section(None, "")]
    pending: list[str] = []  # 還沒歸屬的註解（下一個鍵值或表頭的）
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            pending.append(line)
            i += 1
            continue
        header = _HEADER.match(stripped)
        if header:
            path = tuple(k.strip() for k in header.group(1).split("."))
            sections.append(_Section(path, line, pending))
            pending, i = [], i + 1
            continue
        for j in range(i + 1, len(lines) + 1):
            try:
                (key,) = tomllib.loads("\n".join(lines[i:j]))
            except tomllib.TOMLDecodeError:
                continue
            break
        else:
            raise _fail(f"審查標準設定檔第 {i + 1} 行讀不出完整的鍵值，無法轉換")
        sections[-1].entries.append(_Entry(key, lines[i:j], pending))
        pending, i = [], j
    return sections, pending


def _legacy_field(path: tuple[str, ...] | None) -> tuple[str, bool] | None:
    """舊版面的表 → (新欄位, 是否為每家上手一個子表 [<表>.<上手>])；不是舊版面的表為 None。"""
    if path is None:
        return None
    if path in LEGACY_TABLES:
        return LEGACY_TABLES[path], False
    if len(path) == 2 and path[:1] in LEGACY_TABLES:
        return LEGACY_TABLES[path[:1]], True
    return None


def _trimmed(lines: list[str]) -> list[str]:
    """去掉前後的空行。"""
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return lines


def _renamed(entry: _Entry, key: str) -> list[str]:
    """鍵值換成新的鍵名，值與行尾註解不動。"""
    first = _KEY.sub(lambda m: m.group(1) + key + m.group(2), entry.lines[0], count=1)
    return [first, *entry.lines[1:]]


def restructure(d: dict[str, Any]) -> dict[str, Any]:
    """舊版面的設定內容依 LEGACY_TABLES 搬到 [issuer.<上手>]（轉換結果比對用）。"""
    out = copy.deepcopy(d)
    issuers = out.setdefault("issuer", {})
    for path in legacy_tables(d):
        parent = out
        for key in path[:-1]:
            parent = parent[key]
        for code, value in parent.pop(path[-1]).items():
            issuers.setdefault(code, {})[LEGACY_TABLES[path]] = value
    return out


def convert(text: str) -> str:
    """舊版面的設定檔文字 → 新版面；保留註解，共用的部分不動。"""
    newline = "\r\n" if "\r\n" in text else "\n"
    data = tomllib.loads(text)
    if not legacy_tables(data):
        raise _fail("審查標準設定檔已經是每家上手一張表的新版面，不需要轉換")
    if "issuer" in data:
        raise _fail("審查標準設定檔同時有舊版面的表與 [issuer.<上手>]，無法自動轉換；請人工整理成一種版面")
    sections, tail = _sections(text.splitlines())

    kept: list[_Section] = []
    notes: dict[str, list[str]] = {}  # 新欄位 → 舊表開頭的註解（欄位說明）
    values: dict[str, dict[str, tuple[list[str], list[str]]]] = {}  # 上手 → 欄位 → (註解, 內容行)
    nested_fields: set[str] = set()  # 每家上手一個子表的欄位
    for sec in sections:
        legacy = _legacy_field(sec.path)
        if legacy is None or sec.path is None:
            kept.append(sec)
            continue
        name, nested = legacy
        if not nested:
            notes.setdefault(name, []).extend(_trimmed(sec.comments))
            for n, e in enumerate(sec.entries):
                own = _trimmed(e.comments)
                if n == 0:  # 緊接在表頭後的註解說明這張表
                    notes[name].extend(own)
                    own = []
                values.setdefault(e.key, {})[name] = (own, _renamed(e, name))
            continue
        code, own = sec.path[1], _trimmed(sec.comments)
        if name not in nested_fields:  # 第一個子表前的註解說明這種基準，之後的是該上手的
            notes.setdefault(name, []).extend(own)
            own = []
            nested_fields.add(name)
        body = [line for e in sec.entries for line in (*e.comments, *e.lines)]
        values.setdefault(code, {})[name] = (own, [f"[issuer.{code}.{name}]", *body])

    order = list(data.get("issuer_name", {})) + list(values)
    out: list[str] = []
    for sec in kept:
        if sec.path is not None:
            out += ["", *_trimmed(sec.comments), sec.header]
        for e in sec.entries:
            out += [*e.comments, *e.lines]
    out = _trimmed(out)
    out += ["", "# ---------------------------------------------------------------- 每家上手一張表", "#"]
    out += [
        "# 上手不同的基準寫在 [issuer.<上手代號小寫>]（Issue #128）；沒寫的欄位用上面共用的值，或視為該上手沒有這項基準。",
        f"# 欄位見 src/fcn_checker/config.py 的 ISSUER_FIELDS；以下欄位說明由舊版面各表開頭的註解轉來（{MIGRATE_COMMAND}）。",
    ]
    for name in ISSUER_FIELDS:
        if notes.get(name):
            out += ["#", f"# {name}：", *notes[name]]
    for code in dict.fromkeys(order):
        fields = values[code]
        out += ["", f"[issuer.{code}]"]
        present = [n for n in ISSUER_FIELDS if n in fields]
        for name in present:
            if name not in nested_fields:
                out += [*fields[name][0], *fields[name][1]]
        for name in present:
            if name in nested_fields:  # 子表要在上手表的鍵值之後
                out += ["", *fields[name][0], *fields[name][1]]
    if _trimmed(tail):
        out += ["", *_trimmed(tail)]
    return newline.join(out) + newline


def _check(old: str, new: str) -> None:
    """新內容與舊內容搬移後的值完全相同。"""
    if tomllib.loads(new) != restructure(tomllib.loads(old)):
        raise _fail("轉換後的內容和原檔的值不同，已取消轉換；請找維護者確認設定檔。")


def migrate(path: Path) -> Path:
    """轉換設定檔：原檔另存 `<檔名>.bak`，新版面寫回原處；回傳備份的位置。"""
    path = Path(path)
    with path.open(encoding="utf-8", newline="") as f:
        old = f.read()
    try:
        new = convert(old)
    except tomllib.TOMLDecodeError as e:
        raise _fail(f"審查標準設定檔格式錯誤：{e}") from e
    _check(old, new)
    backup = path.with_name(path.name + ".bak")
    if backup.exists():
        raise _fail(f"備份檔 {backup} 已經存在，為避免蓋掉請先移走再轉換")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        load_review_standard(Path(tmp))  # 新版面載入得了才取代原檔
        with backup.open("x", encoding="utf-8", newline="") as f:
            f.write(old)
        try:
            os.replace(tmp, path)
        except OSError:
            backup.unlink()  # 沒有轉換成功就不留備份，否則下次會被「備份檔已經存在」擋住
            raise
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return backup


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog=MIGRATE_COMMAND,
        description="把舊版面（每種基準一個表）的審查標準設定檔轉成每家上手一張表 [issuer.<上手>]；原檔另存 .bak。",
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=DEFAULTS.review_standard,
        help="審查標準設定檔（預設 config/review_standard.toml）",
    )
    path = parser.parse_args(argv).path
    try:
        backup = migrate(path)
    except (IngestionError, OSError) as e:
        print(f"{path}：{e}", file=sys.stderr)
        return 2
    print(f"已轉換 {path}（原檔另存 {backup}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
