"""Order 信件的各上手讀法（#40）：統一欄位、各家欄名對照、各家認得的其他欄與內嵌欄位。

新增上手或上手改版只改這個檔：在 `ISSUERS` 加一筆，列出它條件表的欄名。
- `aliases`：各家共用的欄名 → 統一欄位；欄名比對不分大小寫、忽略多餘空白。
- `IssuerFormat.overrides`：這家上手的欄名意思和共用對照不同時，以這裡為準。
- `IssuerFormat.others`：這家條件表上認得、但不屬於統一欄位的欄，原樣保留在「其他欄位」。
- `IssuerFormat.embedded`：一格裡寫了好幾個「名稱: 值」（例：UBS 的 Quote Id），拆開再對照。
- `IssuerFormat.shifts`：這家的數字算法和其他家差固定數時，統一後再加上（例：Citi 的 Non-Call +1）。
條件表出現這家沒列過的欄名時，整檔標「待確認」（格式可能改版），欄位仍保留在「其他欄位」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Kind(Enum):
    TEXT = "文字"
    PERCENT = "百分比"
    MONTHS = "月數"
    DATE = "日期"
    AMOUNT = "金額"
    INITIAL_PRICE = "期初定價"


# 統一欄位（輸出欄順序）→ 值的型別
FIELDS: dict[str, Kind] = {
    "商品": Kind.TEXT,
    "幣別": Kind.TEXT,
    "承作金額": Kind.AMOUNT,
    "價格(%)": Kind.PERCENT,
    "UF(%)": Kind.PERCENT,
    "標的1": Kind.TEXT,
    "標的2": Kind.TEXT,
    "標的3": Kind.TEXT,
    "標的4": Kind.TEXT,
    "標的5": Kind.TEXT,
    "標的6": Kind.TEXT,
    "執行價(%)": Kind.PERCENT,
    "KO(%)": Kind.PERCENT,
    "KO觀察方式": Kind.TEXT,
    "KI型態": Kind.TEXT,
    "KI(%)": Kind.PERCENT,
    "年利率(%)": Kind.PERCENT,
    "天期(月)": Kind.MONTHS,
    "Non-Call(月)": Kind.MONTHS,
    "觀察頻率(月)": Kind.MONTHS,
    "期初定價": Kind.INITIAL_PRICE,
    "風險等級": Kind.TEXT,
    "PG rate(%)": Kind.PERCENT,
    "交易日": Kind.DATE,
    "發行日": Kind.DATE,
    "最終比價日": Kind.DATE,
    "到期日": Kind.DATE,
    "發行日偏移": Kind.TEXT,
    "交割說明": Kind.TEXT,
    "上手報價編號": Kind.TEXT,
}

# 讀不到時整檔「待確認」的欄位（TDCC 本身另外檢查）
REQUIRED = ("幣別", "承作金額", "價格(%)", "標的1")


def header_key(text: str) -> str:
    """欄名比對用：小寫、空白合併、去頭尾。"""
    return re.sub(r"\s+", " ", text).strip().lower()


def _keys(mapping: dict[str, str]) -> dict[str, str]:
    return {header_key(k): v for k, v in mapping.items()}


# 各家共用的欄名對照（條件表欄名、信件開頭「名稱: 值」行、內嵌欄位都用這份）
ALIASES: dict[str, str] = _keys(
    {
        "Product": "商品",
        "Currency": "幣別",
        "Curr": "幣別",
        "Capital size": "承作金額",
        "Upfront / Note Price (%)": "價格(%)",
        "Upfront / NotePrice (%)": "價格(%)",
        "Cost (%)": "價格(%)",
        "Offer Price": "價格(%)",
        "Reoffer (%)": "價格(%)",
        "Note Price": "價格(%)",
        **{f"BBG Code {n}": f"標的{n}" for n in range(1, 7)},
        **{f"Underlying {n}": f"標的{n}" for n in range(1, 7)},
        **{f"Stock {n}": f"標的{n}" for n in range(1, 7)},
        "Strike (%)": "執行價(%)",
        "Put Strike": "執行價(%)",
        "KO Barrier (%)": "KO(%)",
        "AutoCall": "KO(%)",
        "KO": "KO(%)",
        "KO Type": "KO觀察方式",
        "Barrier Type": "KI型態",
        "KI Type": "KI型態",
        "Put KI?": "KI型態",
        "KI Barrier (%)": "KI(%)",
        "KI": "KI(%)",
        "Put KI (%)": "KI(%)",
        "Coupon p.a. (%)": "年利率(%)",
        "Coupon p.a.": "年利率(%)",
        "Coupon p.a": "年利率(%)",
        "Tenor (m)": "天期(月)",
        "Guaranteed Periods (m)": "Non-Call(月)",
        "Guaranteed Periods": "Non-Call(月)",
        "Non-call Periods (m)": "Non-Call(月)",
        "Non-Call (m)": "Non-Call(月)",
        "Observation Frequency (m)": "觀察頻率(月)",
        "Freq (m)": "觀察頻率(月)",
        "Initial price": "期初定價",
        "Risk level": "風險等級",
        "Product Risk Level": "風險等級",
        "PG rate": "PG rate(%)",
        "Trade Date": "交易日",
        "Strike Date": "交易日",
        "Initial Valuation Date": "交易日",
        "Issue Date": "發行日",
        "Issuance Date": "發行日",
        "Final Valuation Date": "最終比價日",
        "Redemption Valuation Date": "最終比價日",
        "Final Fixing Date": "最終比價日",
        "Maturity Date": "到期日",
        "Final Maturity Date": "到期日",
        "Redemption Date": "到期日",
        "Effective Date offset": "發行日偏移",
        "Effective Date Offset(Calendar Days)": "發行日偏移",
        "Issue Date Lag": "發行日偏移",
        "Issue Date (T + ?)": "發行日偏移",
        "Quote ID": "上手報價編號",
        "ID": "上手報價編號",
        "Ref": "上手報價編號",
        "Nomura ID": "上手報價編號",
        "MS ID": "上手報價編號",
        "GS Quote ID": "上手報價編號",
        "UBS ID": "上手報價編號",
        "Issuer Prod Ref": "上手報價編號",
    }
)

# 條件表上的 TDCC 欄（MS 條件表自帶 TDCC）
TDCC_HEADERS = frozenset(header_key(h) for h in ("TDCC", "TDCC Code"))

# 各家都有、不屬於統一欄位、原樣保留的欄
COMMON_OTHERS = ("OTC", "Funding Spread (bps)")


@dataclass(frozen=True)
class IssuerFormat:
    name: str
    headers: tuple[str, ...]  # 這家條件表會出現的欄名（統一欄位與其他欄都要列）
    overrides: dict[str, str] = field(default_factory=dict)  # 欄名 → 統一欄位（優先於共用對照）
    embedded: dict[str, tuple[str, ...]] = field(default_factory=dict)  # 欄名 → 格內「名稱: 值」的名稱
    shifts: dict[str, int] = field(default_factory=dict)  # 統一欄位 → 這家寫法要加的數（例：Citi Non-Call +1）

    def field_for(self, header: str) -> str | None:
        """欄名對到的統一欄位；不屬於統一欄位時回傳 None。"""
        key = header_key(header)
        overrides = _keys(self.overrides)
        if key in overrides:
            return overrides[key] or None  # 空字串：這家的這欄不屬於統一欄位
        return ALIASES.get(key)

    def knows(self, header: str) -> bool:
        key = header_key(header)
        return key in {header_key(h) for h in (*self.headers, *COMMON_OTHERS)} or key in TDCC_HEADERS

    def embedded_labels(self, header: str) -> tuple[str, ...]:
        return {header_key(k): v for k, v in self.embedded.items()}.get(header_key(header), ())


_PRICER_COMMON = (
    "Product",
    "Currency",
    "Guaranteed Periods (m)",
    *(f"BBG Code {n}" for n in range(1, 6)),
    "Strike (%)",
    "KO Type",
    "KO Barrier (%)",
    "Coupon p.a. (%)",
    "Tenor (m)",
    "Barrier Type",
    "KI Barrier (%)",
    "Observation Frequency (m)",
)

# 上手編號（TDCC 前三碼）→ 讀法；對照與核對工具 issuer_prefixes.toml 相同
ISSUERS: dict[str, IssuerFormat] = {
    "029": IssuerFormat(
        "BARC",
        (
            *_PRICER_COMMON,
            "Upfront / Note Price (%)",
            "Effective Date offset",
            "Notional",
            "Trade Date",
            "Issue Date",
            "Final Valuation Date",
            "Maturity Date",
            "Quote ID",
        ),
    ),
    "037": IssuerFormat(
        "SG",
        (
            "Strike Date",
            "Issue Date",
            "Final Valuation Date",
            "Maturity Date",
            *(f"Underlying {n}" for n in range(1, 6)),
            "No. of Periods",
            "Settlement Frequency",
            "Currency",
            "Quote ?",
            "Coupon p.a.",
            "Fixed Coupons",
            "Non-Call (m)",
            "Put Strike",
            "AutoCall",
            "KO Type",
            "KI Type",
            "KI",
            "Offer Price",
            "Funding Spread (Bps)",
            "Comment",
        ),
    ),
    "076": IssuerFormat(
        "JPM",
        (
            *_PRICER_COMMON,
            "Upfront / NotePrice (%)",
            "Effective Date Offset(Calendar Days)",
            "Ref",
            "System Remarks",
            "Issue Date",
            "Final Valuation Date",
            "Maturity Date",
        ),
    ),
    "078": IssuerFormat(
        "DBS",
        (
            *_PRICER_COMMON,
            "Upfront / NotePrice (%)",
            "Issue Date Lag",
            "Issue Date",
            "Final Valuation Date",
            "Maturity Date",
            "Issuer Prod Ref",
        ),
    ),
    "082": IssuerFormat(
        "BNP",
        (
            "Client Ref",
            *_PRICER_COMMON,
            "Upfront / NotePrice (%)",
            "Effective Date Offset(Calendar Days)",
            "Trade date",
            "Issue date",
            "Redemption Valuation Date",
            "Redemption date",
            "Remarks",
        ),
    ),
    "086": IssuerFormat(
        "CA",
        (
            "Product",
            "Currency",
            "Wrapper",
            "Tenor (m)",
            *(f"BBG Code {n}" for n in range(1, 6)),
            "Strike (%)",
            "Barrier Type",
            "KI Barrier (%)",
            "KO Type",
            "Guaranteed Periods",
            "KO Barrier (%)",
            "Coupon p.a. (%)",
            "Observation Frequency (m)",
            "Reoffer (%)",
            "Date Schedule Config",
            "Initial Valuation Date",
            "Issue Date",
            "Final Fixing Date",
            "Redemption Date",
            "Note",
            "ID",
        ),
    ),
    "092": IssuerFormat(
        "UBS",
        (
            "Product",
            "Currency",
            "Non-call Periods (m)",
            *(f"BBG Code {n}" for n in range(1, 6)),
            "Strike (%)",
            "KO Type",
            "KO Barrier (%)",
            "Coupon p.a. (%)",
            "Cost (%)",
            "Tenor (m)",
            "Barrier Type",
            "KI Barrier (%)",
            "Observation Frequency (m)",
            "Quote Id",
        ),
        # UBS 把發行日、最終比價日、到期日寫在 Quote Id 那格裡
        embedded={
            "Quote Id": (
                "Issue Date",
                "Final Fixing Date",
                "Final Maturity Date",
                "1st KO Observation",
                "UBS ID",
                "Cap Size",
            )
        },
    ),
    "147": IssuerFormat(
        "MS",
        (
            "MS ID",
            "Product",
            "Issue Date (T + calendar days)",
            *(f"Stock {n}" for n in range(1, 7)),
            "Curr",
            "Tenor (m)",
            "Freq (m)",
            "Coupon p.a",
            "Put Strike",
            "Put KI?",
            "Put KI (%)",
            "KO",
            "KO Type",
            "Non-Call (m)",
            "KO Memory",
            "Final Valuation Date",
            "Maturity Date",
            "Note Price",
        ),
        # MS 這欄的欄名寫「T + 天數」，實際填的是發行日
        overrides={"Issue Date (T + calendar days)": "發行日"},
    ),
    "148": IssuerFormat(
        "GS",
        (
            "Action",
            "GS Quote ID",
            "Product Risk Level",
            "Client Product Code",
            "Product",
            "Currency",
            "Non-call Periods (m)",
            *(f"BBG Code {n}" for n in range(1, 6)),
            "Strike (%)",
            "KO Type",
            "KO Barrier (%)",
            "Coupon p.a. (%)",
            "Cost (%)",
            "Tenor (m)",
            "Barrier Type",
            "KI Barrier (%)",
            "Observation Frequency (m)",
            "Strike Date",
            "Issue Date (T + ?)",
            "Max Notional Size(US$)",
            "System Remark",
        ),
        # GS 的發行日只寫在 System Remark：[ Issue Date: 20Oct26 ]
        embedded={"System Remark": ("Request ID", "RIC Quoted", "Issue Date", "Rounded Coupon", "Issuer")},
    ),
    "156": IssuerFormat(
        "NOMURA",
        (
            "Nomura ID",
            *_PRICER_COMMON,
            "Upfront / NotePrice (%)",
            "Effective Date Offset(Calendar Days)",
            "Issuance Date",
            "Final Valuation Date",
            "Maturity Date",
            "Max Size",
        ),
    ),
    "159": IssuerFormat(
        "CITI",
        (
            "Product",
            "Strike Date",
            "Currency",
            "Tenor (m)",
            "Issue T+",
            *(f"BBG Code {n}" for n in range(1, 6)),
            "Strike (%)",
            "Barrier Type",
            "KI Barrier (%)",
            "Observation Frequency (m)",
            "Non Callable Periods",
            "KO Barrier (%)",
            "Memory Autocall",
            "Daily KO",
            "Coupon p.a. (%)",
            "Upfront (%)",
            "Notional Amount",
            "Format",
            "Swap Index",
            "Funding Spread (bps)",
            "Issue Date",
            "Final Valuation Date",
            "Maturity Date",
            *(f"RefSpot_{n}" for n in range(1, 6)),
            "Remarks",
            "PriceID",
            "Quote Validity (UTC)",
        ),
        overrides={
            "Product": "",  # Citi 寫 FCA（自家商品名），商品別以主旨為準
            "Upfront (%)": "UF(%)",  # Citi 給 UF，不是票面價格；We buy 行也沒有 @價格
            "Issue T+": "發行日偏移",
            "Non Callable Periods": "Non-Call(月)",
            "PriceID": "上手報價編號",
        },
        # 使用者 2026-10-20 確認：Citi 的 Non Callable Periods 算法不同，統一要 +1（0 → 1）
        shifts={"Non-Call(月)": 1},
    ),
    "325": IssuerFormat(
        "HSBC",
        (
            *_PRICER_COMMON,
            "Upfront / NotePrice (%)",
            "Effective Date Offset(Calendar Days)",
            "Comments",
            "ID",
        ),
    ),
    "326": IssuerFormat(
        "BBVA",
        (
            *_PRICER_COMMON,
            "Upfront / NotePrice (%)",
            "Effective Date Offset(Calendar Days)",
            "Trade Date",
            "Issue Date",
            "Final Valuation Date",
            "Maturity Date",
            "ID",
            "Remarks",
        ),
    ),
    "328": IssuerFormat(
        "NATIXIS",
        (
            "Capital size",
            "Ref",
            *_PRICER_COMMON,
            "Upfront / NotePrice (%)",
            "Effective Date Offset(Calendar Days)",
            "Issue Date",
            "Final Valuation Date",
            "Maturity Date",
            "Message",
        ),
    ),
}

# 已知上手、但還沒有 Order 樣本與讀法（目前 14 家都有）
KNOWN_WITHOUT_FORMAT: dict[str, str] = {}
