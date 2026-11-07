"""起單資料與重算用的設定（#493）：`ImportConfig` 與讀它的 `load_import_config`、`load_risk_underlyings`。

從 `product_terms`（寫入端）搬出來，讀取端不必為了一個設定型別 import 寫入端；這裡只 import `database`。
`product_terms` 留著 `ImportConfig`／`load_import_config` 的別名一個版本。讀不到設定時丟 `ImportConfigError`
（訊息是給作業人員看的中文）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from sp_workflow.database import OperatorError

if TYPE_CHECKING:
    from pathlib import Path

    from fcn_checker.check_config import ConfigPaths
    from fcn_checker.trading_calendar import TradingCalendar


class ImportConfigError(OperatorError):
    """設定檔讀不到或格式不對（審查標準、參考條件表格式、上手編號對照、交易所假日表、風險等級設定）。"""


@dataclass(frozen=True)
class ImportConfig:
    denominations: Mapping[str, Decimal]  # 幣別 → 單位面額預設值
    issuer_names: Mapping[str, str]  # 上手代號 → 發行機構的寫法（同清單更新）
    issuer_prefixes: Mapping[str, str]  # TDCC 前三碼 → 上手代號（上手編號對照）
    calendar: TradingCalendar  # 交易所假日表（明日開盤價的 Strike date，#101）
    index_underlyings: frozenset[str] = frozenset()  # 風險等級的指數清單（設定檔的寫法；比對規則在 `risk_level`，#238）
    etf_underlyings: frozenset[str] = frozenset()  # 補充 ETF：白名單沒有的，和白名單的 ETF 合併（同上，#252、#273）
    fallback_etf_underlyings: frozenset[str] = frozenset()  # 還沒匯入白名單時用的 ETF 清單（#273）


def load_import_config(paths: ConfigPaths) -> ImportConfig:
    """從審查標準、參考條件表格式、上手編號對照、交易所假日表與風險等級設定檔讀面額預設值、發行機構寫法、上手編號、
    假日與指數、ETF 清單。核對條件與風險等級的重算（`check_terms.recalculate`）也用這一份。"""
    from fcn_checker.config import (
        load_exchange_holidays,
        load_issuer_prefixes,
        load_reference_format,
        load_review_standard,
    )
    from fcn_checker.ingestion import IngestionError

    try:
        review = load_review_standard(paths.review_standard)
        reference = load_reference_format(paths.reference_format)
        prefixes = load_issuer_prefixes(paths.issuer_prefixes)
        calendar = load_exchange_holidays(paths.exchange_holidays)
        indices, etfs, fallback_etfs = load_risk_underlyings(paths.risk_level)
    except IngestionError as exc:
        raise ImportConfigError(str(exc)) from exc
    return ImportConfig(
        {currency: Decimal(amount) for currency, amount in review.denomination.items()},
        dict(reference.issuer_values),
        prefixes,
        calendar,
        indices,
        etfs,
        fallback_etfs,
    )


RISK_LISTS = ("index_underlyings", "etf_underlyings", "fallback_etf_underlyings")


def load_risk_underlyings(path: Path) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """風險等級的（指數清單, 補充 ETF, 還沒匯入白名單時用的 ETF）（`config/risk_level.toml`，#238、#252、#273）：
    彭博代碼，只去頭尾空白；比對規則在 `risk_level.underlying_key`。缺檔或格式錯誤時丟 `fcn_checker.ingestion.IngestionError`。
    """
    from fcn_checker.config import load_toml
    from fcn_checker.ingestion import IngestionError

    d = load_toml(path, "風險等級")
    found = []
    for key in RISK_LISTS:
        codes = d.get(key)
        if not isinstance(codes, list) or not all(isinstance(c, str) and c.strip() for c in codes):
            raise IngestionError("config_invalid", f"風險等級設定檔的 {key} 要是彭博代碼的清單（可以空白）")
        found.append(frozenset(c.strip() for c in codes))
    indices, etfs, fallback_etfs = found
    return indices, etfs, fallback_etfs
