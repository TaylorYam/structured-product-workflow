"""說明書與投資人須知的發行機構／保證機構信用評等（#240，docs/checker/rules/shared-check-rules.md §10）：各上手 parser 共用。

各上手只宣告評等段落在哪裡（`RatingPlace`：位置說明＋找出段落的 pattern），這裡負責讀出段落裡的（評等機構, 評等）：

- 全文去空白、半形括號換成全形後找 pattern（named group `body` 是評等段落）；每處各自是一筆 `StatedRatings`。
  宣告的位置找不到時也是一筆（`problem` 寫出原因），規則轉人工覆核，不當成沒寫。
- 段落裡依序找評等機構名稱（標準普爾、穆迪、惠譽的中英文寫法；DBRS、R&I 等不追蹤的也認得，好把段落切開），
  每家的評等是名稱之後、下一家名稱之前唯一的評等寫法（例：`穆迪(Moody’s)信評Aa3`、`經惠譽(Fitch)評定為AA`、
  `標準普爾評等A+`）。同一家連續寫兩次名稱（`穆迪（Moody’s）`）算一處。
- 讀不出來（某家名稱後面沒有評等、有兩個評等、評等格式不是那家的寫法、同一家寫了兩個不同評等、段落裡沒有評等機構）
  → `problem`，規則轉人工覆核；不猜值。不追蹤的評等機構不交出（規則忽略）。
- 文件沒寫的評等機構不交出（規則只比文件有寫的）。

新增上手（#241～#243）：在該上手說明書與投資人須知 parser 宣告 `RatingPlace`，用 `read` 交出 `issuer_ratings`
（與 `guarantor_ratings`；沒有保證機構的範本交 `absent`），並在上手註冊表標 `Issuer.ratings`。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from ..schema import Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import absent
from .layout import TextIndex

FIELDS = ("issuer_ratings", "guarantor_ratings")  # 標準欄位：發行機構評等、保證機構評等
SP, MOODYS, FITCH = "標準普爾", "穆迪", "惠譽"  # 追蹤的評等機構（同評等表）
AGENCIES = (SP, MOODYS, FITCH)

# 評等機構名稱的寫法（去空白後）；None 是不追蹤的評等機構，只用來把段落切開
_NAMES: tuple[tuple[str | None, str], ...] = (
    (SP, r"標準普爾|標普|S&P|Standard(?:&|and)Poor['’]?s"),
    (MOODYS, r"穆迪|Moody['’]?s"),
    (FITCH, r"惠譽|Fitch"),
    (None, r"DBRS|R&I|日本格付研究所|Morningstar"),
)
_NAME = re.compile("|".join(f"(?P<a{i}>{p})" for i, (_, p) in enumerate(_NAMES)))
# 評等寫法：前後不接英文字母或數字（排除 Corporation、DBRS 這類字裡的字母）
_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(Aaa|Aa[1-3]|Baa[1-3]|Ba[1-3]|Caa[1-3]|Ca|A[1-3]|B[1-3]"
    r"|AAA|AA[+-]?|BBB[+-]?|BB[+-]?|CCC[+-]?|CC|A[+-]?|B[+-]?|C|RD|D)(?![A-Za-z0-9+-])"
)
_SP_FORMAT = re.compile(r"AAA|AA[+-]?|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?|CCC[+-]?|CC|C|D")
_FITCH_FORMAT = re.compile(r"AAA|AA[+-]?|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?|CCC[+-]?|CC|C|RD|D")
_MOODYS_FORMAT = re.compile(r"Aaa|Aa[123]|A[123]|Baa[123]|Ba[123]|B[123]|Caa[123]|Ca|C")
FORMATS = {SP: _SP_FORMAT, MOODYS: _MOODYS_FORMAT, FITCH: _FITCH_FORMAT}
_DASHES = str.maketrans({"－": "-", "–": "-", "‐": "-", "−": "-", "＋": "+"})  # 一對一換字，位置不變


@dataclass(frozen=True)
class RatingPlace:
    """一處評等段落：位置說明（用在項目名稱，例：第一章第 3 項）與找出段落的 pattern。

    pattern 在去空白、半形括號換成全形的全文上找，named group `body` 是要讀的評等段落；找到幾處就讀幾處。
    """

    where: str
    pattern: str


@dataclass(frozen=True)
class StatedRatings:
    """文件一處寫的評等：（評等機構, 評等）依文件順序，只含追蹤的三家；讀不出來時 `problem` 寫出原因。"""

    where: str
    ratings: tuple[tuple[str, str], ...]
    evidence: tuple[Evidence, ...]
    problem: str = ""


def parse(body: str) -> tuple[tuple[tuple[str, str], ...], str]:
    """一段評等文字（已去空白）→（（評等機構, 評等）…, 讀不出來的原因）；原因空白表示讀得出來。"""
    body = body.translate(_DASHES)
    mentions: list[tuple[str | None, int, int]] = []  # （評等機構, 名稱起點, 名稱終點）；同一家連續寫的合併
    for m in _NAME.finditer(body):
        agency = _NAMES[int(m.lastgroup[1:])][0]
        if mentions and mentions[-1][0] == agency and not _TOKEN.search(body, mentions[-1][2], m.start()):
            mentions[-1] = (agency, mentions[-1][1], m.end())
        else:
            mentions.append((agency, m.start(), m.end()))
    if not any(agency for agency, _, _ in mentions):
        return (), "段落裡找不到標準普爾、穆迪或惠譽"
    found: dict[str, str] = {}
    for k, (agency, _, end) in enumerate(mentions):
        if agency is None:
            continue
        stop = mentions[k + 1][1] if k + 1 < len(mentions) else len(body)
        tokens = [t[1] for t in _TOKEN.finditer(body, end, stop)]
        if len(tokens) != 1:
            what = "找不到評等" if not tokens else f"有多個評等（{'、'.join(tokens)}）"
            return (), f"{agency}{what}"
        value = tokens[0]
        if not FORMATS[agency].fullmatch(value):
            return (), f"{agency}評等「{value}」不是{agency}的評等寫法"
        if found.get(agency, value) != value:
            return (), f"{agency}寫了兩個不同的評等（{found[agency]}、{value}）"
        found[agency] = value
    return tuple(found.items()), ""


def read(name: str, lines: Sequence[Line], places: Sequence[RatingPlace]) -> ParsedField:
    """各處評等段落 → 出處清單型的欄位（值為 tuple[StatedRatings, ...]，每處一筆，依宣告順序）。"""
    ti = TextIndex(lines, unify_brackets=True)
    found: list[StatedRatings] = []
    for place in places:
        hits = list(ti.finditer(place.pattern))
        if not hits:
            found.append(StatedRatings(place.where, (), (), "找不到評等段落"))
        for m in hits:
            ratings, problem = parse(m["body"])
            evidence = tuple(Evidence.of(ln) for ln in ti.lines_for(m.start("body"), m.end("body")))
            found.append(StatedRatings(place.where, ratings, evidence, problem))
    return ParsedField(name, FieldStatus.PRESENT, tuple(found))


def fields(
    lines: Sequence[Line], issuer: Sequence[RatingPlace], guarantor: Sequence[RatingPlace] = ()
) -> dict[str, ParsedField]:
    """上手 parser 交出的兩個評等欄位（`FIELDS`）；範本沒有保證機構（`guarantor` 沒給）時保證機構評等交 absent。"""
    issuer_field, guarantor_field = FIELDS
    return {
        issuer_field: read(issuer_field, lines, issuer),
        guarantor_field: read(guarantor_field, lines, guarantor) if guarantor else absent(guarantor_field, "保證機構"),
    }
