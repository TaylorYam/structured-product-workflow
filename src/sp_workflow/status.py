"""狀態字彙（#202）：同一個中文字在不同概念裡各有自己的名字，全部放在這裡。

`StrEnum` 的值就是資料庫、Excel 與畫面上的字串，和以前各 module 的常數相同；各 module 原本的常數名字照舊可用，
是這裡的別名。概念與名稱見 CONTEXT.md：核對條件狀態、比價狀態、作業進度、存續狀態（ADR 0019、ADR 0020），
另外是起單資料頁與人工維護的確認狀態。新寫的程式直接用這裡的 enum，不必再經過各 module 的別名。
這個 module 不 import 任何 `sp_workflow` module。
"""

from __future__ import annotations

from enum import StrEnum


class CheckTerms(StrEnum):
    """核對條件狀態（#88、ADR 0012）：存在 `workflow.check_terms.status`。成員的順序是畫面列出各狀態檔數的順序。

    「完成」是作業進度（ADR 0019）與比價（ADR 0020）往下走的前提。
    """

    DONE = "完成"
    WAIT_PRICES = "等股價"
    WAIT_TRADES = "等交易明細"
    TO_CONFIRM = "待確認"


class Pricing(StrEnum):
    """比價狀態（#155、#170、ADR 0020）：存在 `workflow.pricing_status.status`。成員的順序是進度頁篩選的順序。"""

    ON_BOOK = "在庫"
    WAIT_PRICES = "等股價"
    TO_CONFIRM = "待確認"
    EXITED = "已出場"


class Progress(StrEnum):
    """作業進度（#156、#186、ADR 0019）：由交易明細、商品條件、核對與步驟紀錄推導，不存。成員依階段順序。"""

    PREPARING = "資料準備中"
    TO_CHECK = "待核對"
    TO_FIX = "待修正"
    TO_MAIL = "待寄信"
    FINISHED = "完成"


class Lifecycle(StrEnum):
    """存續狀態（#152、ADR 0019）：事件存在 `workflow.product_event.event`；沒有有效事件時是「在庫」（不存）。"""

    IN_STOCK = "在庫"
    KO = "KO"
    DELIVERY = "接股"
    MATURITY = "到期還本"
    REDEMPTION = "顧客贖回"


class OrderReview(StrEnum):
    """起單資料頁與人工維護一檔的確認狀態（`OrderRow.status`，ADR 0009、0014、0017）：只在畫面上，不存。

    讀起單建檔（`order_terms`）：已讀取、待確認、找不到；確認後（`order_import`）：待確認（擋下）、請確認、可匯入；
    人工維護打開時與鎖住狀態（`product_write`）：已鎖住、未鎖住、只有交易明細。`BLOCKED` 和 `PENDING` 是同一個字，
    `Enum` 把它當成 `PENDING` 的別名（逐一列出成員時只出現一次）。比價狀態後面的「（未鎖住）」註記（ADR 0020）
    說的是同一個鎖住狀態，也用 `UNLOCKED`。
    """

    READ = "已讀取"
    PENDING = "待確認"
    NOT_FOUND = "找不到"
    BLOCKED = "待確認"
    TO_CHECK = "請確認"
    READY = "可匯入"
    LOCKED = "已鎖住"
    UNLOCKED = "未鎖住"
    TRADES_ONLY = "只有交易明細"
