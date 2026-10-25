"""信用評等的共用名稱與寫法（#237、#240）：評等機構、機構角色、各評等機構的評等格式與參考條件表評等欄。

一處定義：說明書讀法（parsers/ratings.py）、評等核對（rules/ratings.py）、評等表維護（sp_workflow.credit_ratings）
與資料庫產生參考條件表（sp_workflow.check_source）都從這裡取。sp_workflow 可以 import 這裡，反過來不行。
"""

from __future__ import annotations

import re

ISSUER, GUARANTOR = "發行機構", "保證機構"  # 機構角色（同評等表 `workflow.rated_entity.role`）
ROLES = (ISSUER, GUARANTOR)
SP, MOODYS, FITCH = "標準普爾", "穆迪", "惠譽"  # 追蹤的評等機構
AGENCIES = (SP, MOODYS, FITCH)

# 各評等機構的評等格式（同資料庫 0024 的 CHECK）
SP_FORMAT = re.compile(r"AAA|AA[+-]?|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?|CCC[+-]?|CC|C|D")
FITCH_FORMAT = re.compile(r"AAA|AA[+-]?|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?|CCC[+-]?|CC|C|RD|D")
MOODYS_FORMAT = re.compile(r"Aaa|Aa[123]|A[123]|Baa[123]|Ba[123]|B[123]|Caa[123]|Ca|C")
FORMATS = {SP: SP_FORMAT, MOODYS: MOODYS_FORMAT, FITCH: FITCH_FORMAT}
EXAMPLES = {SP: "AA-、A+、BBB+", MOODYS: "Aa2、A1、Baa1", FITCH: "AA-、A+、BBB+"}

# 全形與各種連字號 → 半形（一對一換字，位置不變；說明書全文也用這張表換）
DASHES = str.maketrans({"－": "-", "–": "-", "‐": "-", "−": "-", "＋": "+"})

# 參考條件表評等欄的標準欄位：（機構角色, 評等機構）→ issuer_rating_sp 等；評等表 `Rating` 的欄位名稱同後半
AGENCY_KEYS = {SP: "sp", MOODYS: "moodys", FITCH: "fitch"}
_ROLE_KEYS = {ISSUER: "issuer_rating", GUARANTOR: "guarantor_rating"}


def rating_key(role: str, agency: str) -> str:
    """參考條件表評等欄的標準欄位（例：發行機構、標準普爾 → issuer_rating_sp）。"""
    return f"{_ROLE_KEYS[role]}_{AGENCY_KEYS[agency]}"


# 標準欄位 → （機構角色, 評等機構）；六欄的順序：發行機構三家、保證機構三家
RATING_FIELDS: dict[str, tuple[str, str]] = {
    rating_key(role, agency): (role, agency) for role in ROLES for agency in AGENCIES
}


def canonical(agency: str, text: str) -> str:
    """一家評等機構的評等寫法統一：去所有空白、全形加減號換半形、大小寫（標普、惠譽大寫；穆迪首字大寫其餘小寫）。

    不檢查格式（`FORMATS` 另外比）；空字串照回。作業人員在 Excel 手打的 `A－`、`a+` → `A+`。
    """
    value = "".join(text.split()).translate(DASHES)
    if not value:
        return value
    if agency == MOODYS:
        return value[0].upper() + value[1:].lower()
    return value.upper()
