"""狀態字彙（`sp_workflow.status`，#202）：每個 enum 的值和各 module 的常數一樣，字串值不變。"""

from __future__ import annotations

from sp_workflow import check_terms, lifecycle, observation, order_import, order_terms, product_write, progress
from sp_workflow.status import CheckTerms, Lifecycle, OrderReview, Pricing, Progress


def values(enum) -> set[str]:
    return {member.value for member in enum.__members__.values()}  # 含別名（OrderReview.BLOCKED）


def test_check_terms_status_matches_the_module_constants() -> None:
    constants = {check_terms.DONE, check_terms.WAIT_PRICES, check_terms.WAIT_TRADES, check_terms.TO_CONFIRM}
    assert values(CheckTerms) == constants == {"完成", "等股價", "等交易明細", "待確認"}
    assert check_terms.STATUSES == ("完成", "等股價", "等交易明細", "待確認"), "畫面列出檔數的順序不變"


def test_pricing_status_matches_the_module_constants() -> None:
    constants = {observation.ON_BOOK, observation.WAIT_PRICES, observation.TO_CONFIRM, observation.EXITED}
    assert values(Pricing) == constants == {"在庫", "等股價", "待確認", "已出場"}
    assert observation.STATUSES == ("在庫", "等股價", "待確認", "已出場")


def test_progress_matches_the_module_constants() -> None:
    constants = {progress.PREPARING, progress.TO_CHECK, progress.TO_FIX, progress.TO_MAIL, progress.FINISHED}
    assert values(Progress) == constants == {"資料準備中", "待核對", "待修正", "待寄信", "完成"}
    assert progress.PROGRESS == ("資料準備中", "待核對", "待修正", "待寄信", "完成"), "五個階段的順序不變"


def test_lifecycle_matches_the_module_constants() -> None:
    constants = {lifecycle.IN_STOCK, lifecycle.KO, lifecycle.DELIVERY, lifecycle.MATURITY, lifecycle.REDEMPTION}
    assert values(Lifecycle) == constants == {"在庫", "KO", "接股", "到期還本", "顧客贖回"}


def test_order_review_matches_the_module_constants() -> None:
    constants = {
        order_terms.READ,
        order_terms.PENDING,
        order_terms.NOT_FOUND,
        order_import.BLOCKED,
        order_import.TO_CHECK,
        order_import.READY,
        product_write.LOCKED,
        product_write.MAINTAIN_OPEN,
        product_write.MAINTAIN_NEW,
        observation.UNLOCKED,
    }
    assert values(OrderReview) == constants == {
        "已讀取", "待確認", "找不到", "請確認", "可匯入", "已鎖住", "未鎖住", "只有交易明細",
    }  # fmt: skip
    assert OrderReview.BLOCKED is OrderReview.PENDING, "同一個字是同一個成員"


def test_values_are_plain_strings_for_the_database_and_the_screen() -> None:
    assert CheckTerms.DONE == "完成" and str(CheckTerms.DONE) == "完成" and f"{Pricing.EXITED}" == "已出場"
    assert {"完成": 1}[CheckTerms.DONE] == 1, "當 dict 的鍵和字串一樣"
