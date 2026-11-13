"""BNP 表格解析：標的表（第一章第 11 項與第 18 項重印）、日期表（第 14 項）、費用表（第四章第 2 項）。

擷取文字為「一格一行」的順序（由左到右、由上到下），格內文字再依寬度拆行，標籤格與第一格有時在同一行
（docs/checker/templates/bnp-zh-product-description.md §5）。價格表以列標籤切段、在去空白的全文上讀值，不依座標。
"""

from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal

from ..schema import Line, ParsedField
from ..text import squash
from .common import dec
from .layout import DATE_RE, TextIndex, parse_date
from .money import CURRENCY_WORD

# 標的表的列標籤（去空白；冒號全半形皆有），依出現順序；下限價格列只在有 KI 時出現
PRICE_LABELS = (
    ("initial", r"期初價格\(InitialPrice\)[:：]"),
    ("strike", r"執行價格\(StrikePrice\)[:：]"),
    ("ko", r"自動提前贖回價格\(AutomaticEarlyRedemptionPrice\)[:：]"),
    ("ki", r"下限價格\(Knock-InPrice\)[:：]"),
    ("currency", r"貨幣\(Currency\)[:：]"),
)
# DACN 標的表：下限價格與貨幣之間多一列定界價格（配息門檻，docs/checker/templates/bnp-zh-dacn.md §5）；說明書與投資人須知
# 的範本辨識也用這個標籤看有沒有這一列
BARRIER_LABEL = ("barrier", r"定界價格\(BarrierPrice\)[:：]")
DACN_PRICE_LABELS = tuple(
    x for label in PRICE_LABELS for x in ((label, BARRIER_LABEL) if label[0] == "ki" else (label,))
)
PRICE = r"([\d,]+\.\d{4})"
_CURRENCY_PEEK = 8  # 貨幣列讀不到幣別字時，帶出標籤後幾個字給人工覆核看
# 說明書第 11 項「N 即期初價格之X%」、第 18 項「N，即期初價格之X%」；投資人須知「N,即期初價格之X%」（半形逗號）
PRICE_PCT = PRICE + r"[，,]?即期初價格之(\d+(?:\.\d+)?)%"
TICKER = re.compile(r"^[A-Z0-9][A-Z0-9./-]*\s+[A-Z]{2}$")
NAME_LABEL, TICKER_LABEL, EXCHANGE_LABEL = "連結標的名稱：", "彭博代碼：", "交易所："
WRAP_LABELS = (NAME_LABEL, TICKER_LABEL, EXCHANGE_LABEL)
# 標的表每一列的標籤開頭（去空白）：跨頁時用來找下一頁續行到哪裡為止
ROW_STARTS = (
    "連結標的名",
    "彭博代碼",
    "交易所",
    "期初價格",
    "執行價格",
    "自動提前贖回",
    "下限價格",
    "定界價格",
    "貨幣",
)

# 日期表：表頭（去空白、接在「j」之後）→ 表型
# 表頭（去空白）→ 表型：D 每日觀察、P 每期觀察；SB 為 Super Bull 只列評價日的表（範本規格 bnp-zh-super-bull.md §4.1，#311）
DATE_HEADERS = {
    "配息期開始日配息期結束日配息日": "D",
    "自動提前贖回評價日配息日": "P",
    "自動提前贖回評價日自動提前贖回日": "SB",
}
DATE_KEYS = {"D": ("start", "end", "payment"), "P": ("pricing", "payment"), "SB": ("pricing", "redemption")}
NOT_APPLICABLE = "不適用"  # P 型第一個可提前出場期之前的自動提前贖回評價日

FEE_PATTERNS = {  # 第四章第 2 項費用表（去空白）：費用項目名稱 → 該列費率欄的寫法
    "申購費用": r"申購費用申購價金的(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)",
    "提前贖回費用": r"提前贖回費用投資人提前贖回價金的(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)",
    "分銷費用": r"分銷費用\([^)]*\)申購價金的(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)",
}


def price_table(
    name: str, lines: list[Line], *, with_tickers: bool, labels: tuple[tuple[str, str], ...] = PRICE_LABELS
) -> ParsedField:
    """標的表：列標籤依序為期初價格、執行價格、自動提前贖回價格、（下限價格）、貨幣；每列每檔一格。DACN 由呼叫端給
    `DACN_PRICE_LABELS`（下限價格後多一列定界價格，欄名 `barrier`）。

    值：{"columns": [...], "pcts": {欄: [各檔百分比]}, "rows": [{"prices": {欄: Decimal}, "currency": 幣別字或 None}],
    "row_lines": [...], "tickers": [...]（`with_tickers` 時，第 11 項「彭博代碼」列）}。列數（標的數）由期初價格列決定，
    其他列須相同。貨幣列（標的交易幣別，Issue #227）緊接在標籤後、每檔一個幣別字；不是剛好 N 個時各檔幣別都放讀到的
    原文（連續的幣別字，沒有時為標籤後幾個字），核對時對不到幣別而轉人工覆核。
    """
    if not lines:
        return ParsedField.missing(name, "找不到標的表")
    lines = join_wrapped_labels(stitch_page_break(split_merged_cells(lines)))
    ti = TextIndex(lines, separate_digits=True)
    found: dict[str, re.Match[str]] = {}
    for col, pattern in labels:
        hits = list(re.finditer(pattern, ti.text))
        if len(hits) > 1:
            return ParsedField.invalid(name, lines, f"標的表「{col}」列標籤出現多次")
        if hits:
            found[col] = hits[0]
    missing = [col for col in ("initial", "strike", "ko", "currency") if col not in found]
    if missing:
        return ParsedField.invalid(name, lines, "標的表缺少列標籤：" + "、".join(missing))
    order = [col for col, _ in labels if col in found]
    starts = [found[col].start() for col in order]
    if starts != sorted(starts):
        return ParsedField.invalid(name, lines, "標的表列標籤順序不符範本（期初、執行、自動提前贖回、下限、貨幣）")
    columns = [c for c in order if c != "currency"]
    cells: dict[str, list[re.Match[str]]] = {}
    for k, col in enumerate(columns):
        segment_start, segment_end = found[col].end(), found[order[k + 1]].start()
        segment = ti.text[segment_start:segment_end]
        pattern = PRICE if col == "initial" else PRICE_PCT
        matches = list(re.finditer(pattern, segment))
        rest = re.sub(pattern, "", segment).replace("|", "")
        if not matches or rest:
            where = ti.lines_for(segment_start, segment_end) or lines
            return ParsedField.invalid(name, where, f"標的表「{col}」列有無法辨識的儲存格")
        cells[col] = matches
    counts = {col: len(ms) for col, ms in cells.items()}
    n = counts["initial"]
    if any(c != n for c in counts.values()):
        return ParsedField.invalid(name, lines, f"標的表各列的格數不同：{counts}")
    rows: list[dict] = []
    row_lines: list[list[Line]] = []
    pcts: dict[str, list[Decimal]] = {col: [] for col in columns if col != "initial"}
    after = ti.text[found["currency"].end() :]
    held = re.match(rf"(?:{CURRENCY_WORD})*", after)[0]
    words = re.findall(CURRENCY_WORD, held)
    currencies = words if len(words) == n else [held or after[:_CURRENCY_PEEK].strip() or "（空白）"] * n
    for i in range(n):
        prices: dict[str, Decimal] = {}
        lns: list[Line] = []
        for col in columns:
            m = cells[col][i]
            offset = found[col].end()
            prices[col] = dec(m[1])
            if col != "initial":
                pcts[col].append(Decimal(m[2]))
            for ln in ti.lines_for(offset + m.start(), offset + m.end()):
                if ln not in lns:
                    lns.append(ln)
        rows.append({"prices": prices, "currency": currencies[i]})
        row_lines.append(lns)
    value = {"columns": columns, "pcts": pcts, "rows": rows, "row_lines": row_lines, "tickers": None}
    evidence = ti.lines_for(found["initial"].start(), found["currency"].start())
    if with_tickers:
        tickers = _tickers(lines)
        if tickers is None:
            return ParsedField.invalid(name, lines, "標的表「彭博代碼」列缺漏、重複或代碼無法辨識")
        if len(tickers) != n:
            return ParsedField.invalid(name, lines, f"標的表彭博代碼有 {len(tickers)} 個，價格列有 {n} 格")
        value["tickers"] = tickers
        value["names"] = _names(lines, n)
    return ParsedField.present(name, value, evidence)


def _row_start(ln: Line, label_x: float) -> bool:
    return abs(ln.x0 - label_x) < 2 and squash(ln.text).startswith(ROW_STARTS)


# 一格「價格,即期初價格之X%」（原文，可含空白；百分比可能換到下一行或下一頁）
_CELL = re.compile(r"[\d,]+\.\d{4}\s*[，,]?\s*即期初價格之(?:\s*\d+(?:\.\d+)?%)?")


def split_merged_cells(lines: list[Line]) -> list[Line]:
    """同一行裡有兩格以上、或列標籤後面接著格時，依每格第一個字的位置拆成多行（x0 為該格起點）。

    標的表在頁尾跨頁時，同一列各格的前半可能被擷取成一行（投資人須知，#311），各格後半在下一頁；拆開後
    `stitch_page_break` 才能依欄把後半接回。沒有逐字位置的行不拆。
    """
    if not lines:
        return lines
    label_x = lines[0].x0
    out: list[Line] = []
    for ln in lines:
        spans, cells = ln.aligned_char_spans, list(_CELL.finditer(ln.text))
        labelled = abs(ln.x0 - label_x) < 2 and bool(cells) and bool(ln.text[: cells[0].start()].strip())
        if not spans or not (len(cells) >= 2 or labelled):
            out.append(ln)
            continue
        cuts = [m.start() for m in cells]
        cuts = [0, *cuts] if labelled else [0, *cuts[1:]]
        for a, b in zip(cuts, [*cuts[1:], len(ln.text)], strict=True):
            piece = ln.text[a:b]
            k, e = a + len(piece) - len(piece.lstrip()), a + len(piece.rstrip())
            if k < e:
                out.append(replace(ln, x0=spans[k][0], x1=spans[e - 1][1], text=ln.text[k:e], char_spans=spans[k:e]))
    return out


def stitch_page_break(lines: list[Line]) -> list[Line]:
    """表格跨頁時，上一頁最後一列在下一頁頂端的續行（列標籤後半、各格後半）接回上一頁同一欄（x 相同）的最後一行之後。

    擷取順序是一頁讀完才換頁，跨頁的那一列會變成「標籤前半、各格前半、標籤後半、各格後半」，標籤與格都認不出來。
    續行＝下一頁開頭到下一個列標籤之前的行；只接到上一頁最後一列裡同一欄的行，該欄在最後一列沒有行時留在原位。
    """
    if not lines:
        return lines
    label_x = lines[0].x0
    out = list(lines)
    b = 1
    while b < len(out):
        if out[b].page == out[b - 1].page:
            b += 1
            continue
        e = next((i for i in range(b, len(out)) if _row_start(out[i], label_x)), len(out))
        head, tail = out[:b], out[e:]
        block = _join_label_pieces(out[b:e], head, label_x)
        last_row = max((i for i, ln in enumerate(head) if _row_start(ln, label_x)), default=0)
        stay = []
        for ln in block:
            j = max((i for i, h in enumerate(head) if abs(h.x0 - ln.x0) < 2), default=-1)
            if j >= last_row:
                head.insert(j + 1, ln)
            else:
                stay.append(ln)
        out = head + stay + tail
        b = len(head) + len(stay) + 1
    return out


def _join_label_pieces(block: list[Line], head: list[Line], label_x: float) -> list[Line]:
    """續行裡和標籤欄那一行同一列（同頁、y 相同）、落在第一個價格欄左邊的字，接回標籤欄那一行：跨頁時英文列標籤可能
    被擷取成兩段（投資人須知 `(Automatic` ／ `Early`，docs/checker/templates/bnp-zh-dacn-iis.md §3），不接回就對不到欄。
    上一頁沒有價格欄時不接。"""
    first_col = min((ln.x0 for ln in head if ln.x0 > label_x + 2), default=None)
    if first_col is None:
        return block
    out: list[Line] = []
    for ln in block:
        prev = out[-1] if out else None
        if (
            prev is not None
            and abs(prev.x0 - label_x) < 2
            and prev.page == ln.page
            and abs(prev.y0 - ln.y0) < 1
            and label_x + 2 < ln.x0 < first_col - 2
        ):
            out[-1] = replace(prev, x1=max(prev.x1, ln.x1), text=f"{prev.text.rstrip()} {ln.text.strip()}")
            continue
        out.append(ln)
    return out


def join_wrapped_labels(lines: list[Line]) -> list[Line]:
    """「連結標的名稱：」等列標籤在標籤欄裡換行（`連結標的名` / `稱：`）時接回一行，`_names`、`_tickers` 才找得到。"""
    out: list[Line] = []
    for ln in lines:
        prev = out[-1] if out else None
        if prev is not None and abs(prev.x0 - ln.x0) < 2:
            a, joined = squash(prev.text), squash(prev.text) + squash(ln.text)
            if any(
                squash(lb).startswith(a) and a != squash(lb) and joined.startswith(squash(lb)) for lb in WRAP_LABELS
            ):
                out[-1] = replace(
                    prev,
                    x1=max(prev.x1, ln.x1),
                    y1=max(prev.y1, ln.y1),
                    text=prev.text.rstrip() + ln.text.strip(),
                )
                continue
        out.append(ln)
    return out


def _tickers(lines: list[Line]) -> list[str] | None:
    """「彭博代碼：」列到「交易所：」列之前的每一格（標籤後同一行的文字也算一格）；代碼格式不符時為 None。"""
    starts = [i for i, ln in enumerate(lines) if squash(ln.text).startswith(squash(TICKER_LABEL))]
    ends = [i for i, ln in enumerate(lines) if squash(ln.text).startswith(squash(EXCHANGE_LABEL))]
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0]:
        return None
    a, b = starts[0], ends[0]
    head = lines[a].text.split("：", 1)[1].strip() if "：" in lines[a].text else lines[a].text.split(":", 1)[-1].strip()
    cells = ([head] if head else []) + [ln.text.strip() for ln in lines[a + 1 : b]]
    if not cells or any(not TICKER.fullmatch(c) for c in cells):
        return None
    return [re.sub(r"\s+", " ", c) for c in cells]


def _names(lines: list[Line], n: int) -> list[str] | None:
    """「連結標的名稱：」列到「彭博代碼：」列之前的各格名稱（只用來指出情境假設的是哪一檔，不核對）。

    行數 = 標的數時一行一格；名稱換行時同一格的行 x 座標相同，依 x 座標分格（表格一格一欄）。分不出 n 格時為 None。
    """
    starts = [i for i, ln in enumerate(lines) if squash(ln.text).startswith(squash(NAME_LABEL))]
    ends = [i for i, ln in enumerate(lines) if squash(ln.text).startswith(squash(TICKER_LABEL))]
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0]:
        return None
    a, b = starts[0], ends[0]
    head = lines[a].text.split("：", 1)[1].strip() if "：" in lines[a].text else ""
    cells: list[tuple[float | None, str]] = ([(None, head)] if head else []) + [
        (ln.x0, ln.text.strip()) for ln in lines[a + 1 : b] if ln.text.strip()
    ]
    if len(cells) == n:
        return [text for _, text in cells]
    groups: dict[float | None, list[str]] = {}
    for x, text in cells:
        key = next((k for k in groups if k is not None and x is not None and abs(k - x) < 2), x)
        groups.setdefault(key, []).append(text)
    if None in groups and len(groups) == n + 1:  # 第一格和標籤同一行又換行：後半在最左一欄
        first = min(k for k in groups if k is not None)
        groups[first] = groups.pop(None) + groups[first]
        groups = dict(sorted(groups.items()))
    if len(groups) != n:
        return None
    return ["".join(parts) for parts in groups.values()]


def ticker_lines(lines: list[Line]) -> list[Line]:
    """「彭博代碼：」列的原文行（證據用）。"""
    starts = [i for i, ln in enumerate(lines) if squash(ln.text).startswith(squash(TICKER_LABEL))]
    ends = [i for i, ln in enumerate(lines) if squash(ln.text).startswith(squash(EXCHANGE_LABEL))]
    if len(starts) != 1 or len(ends) != 1:
        return []
    return lines[starts[0] : ends[0]]


def date_table(lines: list[Line]) -> ParsedField:
    """第 14 項日期表：表頭「j」之後的表頭格決定表型（D 配息期開始日／結束日／配息日、P 自動提前贖回評價日／配息日、
    SB 自動提前贖回評價日／自動提前贖回日）；
    資料列到下一個「(n)」子項為止。P 型自動提前贖回評價日可為「不適用」。

    值：{"type": "D"|"P", "rows": [各列 dict], "row_lines": [各列原文行]}；D 列為 period／start／end／payment，
    P 列為 period／pricing（None 為不適用）／payment，SB 列為 period／pricing／redemption（都要是日期）。
    """
    name = "date_table"
    heads = [i for i, ln in enumerate(lines) if squash(ln.text) == "j"]
    if len(heads) != 1:
        return ParsedField.missing(name, "找不到日期表表頭「j」" if not heads else "日期表表頭「j」出現多次")
    first = next((i for i in range(heads[0] + 1, len(lines)) if re.fullmatch(r"\d{1,2}", squash(lines[i].text))), None)
    if first is None:
        return ParsedField.invalid(name, lines[heads[0] :], "日期表沒有資料列")
    header_cells = [squash(ln.text) for ln in lines[heads[0] + 1 : first]]
    kind = DATE_HEADERS.get("".join(header_cells))
    if kind is None:
        return ParsedField.invalid(name, lines[heads[0] : first], "日期表表頭不是已知的寫法")
    end = next((i for i in range(first, len(lines)) if re.match(r"^[(（]\d{1,2}[)）]", lines[i].text)), len(lines))
    rows: list[dict] = []
    row_lines: list[list[Line]] = []
    for ln in lines[first:end]:
        t = squash(ln.text)
        if t == "j" or t in header_cells:  # 跨頁重複的表頭
            continue
        if re.fullmatch(r"\d{1,2}", t):
            rows.append({"period": int(t), "cells": []})
            row_lines.append([ln])
            continue
        if not rows:
            return ParsedField.invalid(name, [ln], "日期表出現無法辨識的儲存格")
        if DATE_RE.fullmatch(t):
            value = parse_date(t)
            if value is None:
                return ParsedField.invalid(name, [ln], "日期表日期不合法")
        elif t == NOT_APPLICABLE and kind == "P":
            value = None
        else:
            return ParsedField.invalid(name, [ln], "日期表出現無法辨識的儲存格")
        rows[-1]["cells"].append(value)
        row_lines[-1].append(ln)
    out = []
    keys = DATE_KEYS[kind]
    for row, lns in zip(rows, row_lines, strict=True):
        cells = row["cells"]
        if len(cells) != len(keys) or cells[-1] is None or (kind == "D" and None in cells):
            return ParsedField.invalid(name, lns, f"日期表第 {row['period']} 列欄位數或內容不符範本")
        out.append({"period": row["period"], **dict(zip(keys, cells, strict=True))})
    if not out or [r["period"] for r in out] != list(range(1, len(out) + 1)):
        return ParsedField.invalid(name, lines[first:end], "日期表期數不是從 1 起連續")
    return ParsedField.present(name, {"type": kind, "rows": out, "row_lines": row_lines}, lines[first:end])
