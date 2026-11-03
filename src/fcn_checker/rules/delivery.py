"""交股情境（期末股價低於執行價、以實物交付股票）的算式重算，Super Bull 情境三與 BEN 交股情境共用（workflow #439）。

`worse` 依序核對：期末股價 < 執行價、股數 = 面額與匯率、執行價推得（位數、進位、容差由寫法決定）、其後各步驟。兩種寫法
（`Convention`）：

- HSBC 寫法（`HSBC`：HSBC Super Bull、HSBC BEN；`MS`：MS BEN，報酬率先四捨五入再年化）：股數 = N × 匯率 ÷ K，4 位四捨五入、
  容差 ±0.0001；交割股數、零股數、剩餘金額 = P × F ÷ 匯率（到分）、到期贖回價值、損益、年化 = 損益 ÷ 面額 × 12 ÷ 天期。
- CA 寫法（`CA`：CA Super Bull、CA BEN）：先比假設句的收盤價；股數 = N ÷（K × 匯率），3 位無條件去位、要相等；整數股數、
  畸零股數與剩餘金額 = P × F × 匯率（文件位數）寫在同一句；報酬率、年化 = 未四捨五入的報酬率 × 12 ÷ 天期。跨幣別（第 16 項
  有匯率行，workflow #424）時三個算式寫的匯率要等於匯率行。

錯訊用字照各寫法原本的說法（期末股價／假設收盤價、交割股數／整數股數、零股數／畸零股數）。讀出的算式型別：HSBC 寫法在
parsers/delivery.py，CA 寫法在 parsers/ca_super_bull.py。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from ..schema import CheckResult
from . import kit
from .super_bull import Scenario, annualized_of_pnl, cents, pnl_check, within_tolerance

# HSBC 寫法交股情境各步驟的項目名稱（`Scenario` 名稱表；各上手再補其他情境的名稱）
HSBC_NAMES = {
    "assumed": "期末股價",
    "shares": "股數",
    "whole": "交割股數",
    "fraction": "零股數",
    "remainder": "剩餘金額",
    "value": "到期贖回價值",
    "pnl": "損益",
    "annualized": "年化報酬率",
}


class Convention(ABC):
    """一種交股情境寫法：股數的位數（`share_places`）、進位（`share_rounding`）、容差（`share_tolerance`）是資料；股數的
    算式、匯率怎麼比、股數之前的假設與之後的步驟由子類別給。"""

    share_places: Decimal
    share_rounding: str
    share_tolerance: Decimal
    shares_missing: str  # 找不到股數算式時的說明

    @abstractmethod
    def assumed(self, c: Scenario, sc) -> list[CheckResult]:
        """期末股價（假設收盤價）< 執行價。"""

    @abstractmethod
    def unrounded_shares(self, s, n: Decimal, fx: Decimal | None) -> Decimal:
        """股數進位前的值。"""

    @abstractmethod
    def shares_fx_issues(self, s, fx: Decimal | None) -> list[str]:
        """股數算式寫的匯率和情境假設（匯率行）不同。"""

    @abstractmethod
    def shares_formula(self, s, n: Decimal, fx: Decimal | None) -> str:
        """錯訊「股數 S 應為 … = E」中間的算式。"""

    @abstractmethod
    def rest(self, c: Scenario, sc, n: Decimal, tenor: int, fx: Decimal | None) -> list[CheckResult]:
        """股數之後的各步驟。"""

    def expected_shares(self, s, n: Decimal, fx: Decimal | None) -> Decimal:
        return self.unrounded_shares(s, n, fx).quantize(self.share_places, self.share_rounding)


def worse(
    c: Scenario, sc, n: Decimal, tenor: int, convention: Convention, fx: Decimal | None = None
) -> list[CheckResult]:
    """交股情境 `sc`（讀出的算式，欄位見各寫法）全部重算：`n` 面額、`tenor` 天期、`fx` 情境假設的期末匯率（HSBC 寫法）或
    第 16 項匯率行（CA 寫法，同幣別為 None）。"""
    out = convention.assumed(c, sc)
    s = sc.shares.value if sc.shares is not None else None
    if s is None:
        out.append(c.unread("shares", convention.shares_missing))
    else:
        expected = convention.expected_shares(s, n, fx)
        issues = [] if s.denomination == n else [f"算式中的面額 {s.denomination} 不等於第一章面額 {n}"]
        issues += convention.shares_fx_issues(s, fx)
        if abs(s.shares - expected) > convention.share_tolerance:
            issues.append(f"股數 {s.shares} 應為 {convention.shares_formula(s, n, fx)} = {expected}")
        out.append(c.calc("shares", issues, sc.shares, expected=expected, actual=s.shares))
    return [*out, *convention.rest(c, sc, n, tenor, fx)]


# ---------------------------------------------------------------- HSBC 寫法


@dataclass(frozen=True)
class HsbcStyle(Convention):
    """HSBC 寫法；`round_return`：年化前報酬率先四捨五入到 2 位（MS BEN，#376）。"""

    round_return: bool = False
    share_places = Decimal("0.0001")
    share_rounding = ROUND_HALF_UP
    share_tolerance = Decimal("0.0001")  # 股數（4 位）容許的進位差（核對規則 §3.7）
    shares_missing = "找不到「每單位面額 × 期末匯率 / 最差表現連結標的執行價 = N × 匯率 / K = S 股」"

    def assumed(self, c: Scenario, sc) -> list[CheckResult]:
        s = sc.shares.value if sc.shares is not None else None
        rm = sc.remainder.value if sc.remainder is not None else None
        v = sc.value.value if sc.value is not None else None
        close_price = rm.close if rm is not None else v.close if v is not None else None
        if s is None or close_price is None:
            return [c.unread("assumed", "找不到股數或剩餘金額算式，無法比對期末股價和執行價")]
        issues = [] if close_price < s.strike else [f"期末股價 {close_price} 應低於執行價 {s.strike}"]
        return [c.calc("assumed", issues, sc.shares, expected=f"< {s.strike}", actual=close_price)]

    def unrounded_shares(self, s, n: Decimal, fx: Decimal | None) -> Decimal:
        return s.denomination * s.fx / s.strike

    def shares_fx_issues(self, s, fx: Decimal | None) -> list[str]:
        return [f"算式中的期末匯率 {s.fx} 和情境假設 {fx} 不同"] if s.fx != fx else []

    def shares_formula(self, s, n: Decimal, fx: Decimal | None) -> str:
        return f"{n} × {s.fx} / {s.strike}"

    def rest(self, c: Scenario, sc, n: Decimal, tenor: int, fx: Decimal | None) -> list[CheckResult]:
        """交割股數 = 股數無條件捨去；零股數 = 股數 − 交割股數；剩餘金額 = P × 零股數 ÷ 匯率（2 位）；到期贖回價值 =
        P × 交割股數 ÷ 匯率 + 剩餘金額；損益 = 價值 − 面額；年化 = 損益 ÷ 面額 × 12 ÷ 天期。"""
        out = []
        s = sc.shares.value if sc.shares is not None else None
        rm = sc.remainder.value if sc.remainder is not None else None
        v = sc.value.value if sc.value is not None else None
        whole = s.shares.to_integral_value(ROUND_DOWN) if s is not None else None
        if sc.whole is None:
            out.append(c.unread("whole", "找不到「在此例中為W 股」"))
        else:
            issues = (
                []
                if whole is None or sc.whole.value == whole
                else [f"交割股數 {sc.whole.value} 應為股數無條件捨去 = {whole}"]
            )
            out.append(c.calc("whole", issues, sc.whole, expected=whole, actual=sc.whole.value))
        if sc.fraction is None:
            out.append(c.unread("fraction", "找不到「零股數(即F 股)」"))
        else:
            expected = s.shares - whole if s is not None else None
            issues = (
                []
                if expected is None or sc.fraction.value == expected
                else [f"零股數 {sc.fraction.value} 應為 {expected}"]
            )
            out.append(c.calc("fraction", issues, sc.fraction, expected=expected, actual=sc.fraction.value))
        if rm is None:
            out.append(c.unread("remainder", "找不到「期末股價 × 零股數 / 期末匯率 = P × F / 匯率 = R」"))
        else:
            expected = cents(rm.close * rm.fraction / rm.fx)
            issues = []
            if sc.fraction is not None and rm.fraction != sc.fraction.value:
                issues.append(f"剩餘金額算式的零股數 {rm.fraction} 和前面的 {sc.fraction.value} 不同")
            if rm.fx != fx:
                issues.append(f"剩餘金額算式的期末匯率 {rm.fx} 和情境假設 {fx} 不同")
            if not within_tolerance(rm.amount, expected):
                issues.append(f"剩餘金額 {rm.amount} 應為 {rm.close} × {rm.fraction} / {rm.fx} = {expected}")
            out.append(c.calc("remainder", issues, sc.remainder, expected=expected, actual=rm.amount))
        amount = None
        if v is None:
            out.append(c.unread("value", "找不到「= (P × W / 匯率) + R = V (四捨五入至小數點後第二位)」"))
        else:
            amount = v.amount
            expected = cents(v.close * v.whole / v.fx + v.remainder)
            issues = []
            for what, got, want in (
                ("期末股價", v.close, rm.close if rm else None),
                ("交割股數", v.whole, sc.whole.value if sc.whole else None),
                ("剩餘金額", v.remainder, rm.amount if rm else None),
            ):
                if want is not None and got != want:
                    issues.append(f"到期贖回價值算式的{what} {got} 和前面的 {want} 不同")
            if v.fx != fx:
                issues.append(f"到期贖回價值算式的期末匯率 {v.fx} 和情境假設 {fx} 不同")
            if not within_tolerance(v.amount, expected):
                issues.append(
                    f"到期贖回價值 {v.amount} 應為 {v.close} × {v.whole} / {v.fx} + {v.remainder} = {expected}"
                )
            out.append(c.calc("value", issues, sc.value, expected=expected, actual=v.amount))
        out.append(pnl_check(c, sc.worse_pnl, amount, n))
        out.append(annualized_of_pnl(c, sc.worse_annualized, sc.worse_pnl, n, tenor, round_return=self.round_return))
        return out


HSBC = HsbcStyle()
MS = HsbcStyle(round_return=True)


# ---------------------------------------------------------------- CA 寫法


def _fx_issue(what: str, got: Decimal | None, want: Decimal | None) -> list[str]:
    """CA 寫法算式的匯率要等於第 16 項匯率行（同幣別兩邊都沒有）。"""
    if got == want:
        return []
    if want is None:
        return [f"{what}乘了匯率 {got}，但第 16 項沒有匯率行"]
    if got is None:
        return [f"{what}沒有乘第 16 項匯率 {want}"]
    return [f"{what}的匯率 {got} 和第 16 項匯率 {want} 不同"]


@dataclass(frozen=True)
class CaStyle(Convention):
    """CA 寫法（跨幣別時 P 都乘匯率 R；R 是試算值，只核對三個算式寫的 R 等於匯率行）。"""

    share_places = Decimal("0.001")
    share_rounding = ROUND_DOWN
    share_tolerance = Decimal(0)
    shares_missing = "找不到「每單位面額/…執行價* = N / K = S 股」"

    def assumed(self, c: Scenario, sc) -> list[CheckResult]:
        s = sc.shares.value if sc.shares is not None else None
        if sc.worse_assumed is None:
            return [c.unread("assumed", "找不到「假設於期末訂價日…收盤價為P，低於其執行價」")]
        if s is None:
            return [c.unread("assumed", "找不到股數算式，無法比對假設收盤價和執行價")]
        assumed = sc.worse_assumed.value.close
        issues = [] if assumed < s.strike else [f"假設收盤價 {assumed} 應低於執行價 {s.strike}"]
        return [c.calc("assumed", issues, sc.worse_assumed, expected=f"< {s.strike}", actual=assumed)]

    def unrounded_shares(self, s, n: Decimal, fx: Decimal | None) -> Decimal:
        return n / (s.strike * (fx or Decimal(1)))

    def shares_fx_issues(self, s, fx: Decimal | None) -> list[str]:
        return _fx_issue("股數算式", s.fx, fx)

    def shares_formula(self, s, n: Decimal, fx: Decimal | None) -> str:
        divisor = f"({s.strike} × {fx})" if fx is not None else f"{s.strike}"
        return f"{n} / {divisor} 無條件去位到 3 位"

    def rest(self, c: Scenario, sc, n: Decimal, tenor: int, fx: Decimal | None) -> list[CheckResult]:
        """整數股數、畸零股數；剩餘金額 = 畸零股數 × P（× R）；報酬率 = (整數股數 × P（× R）+ 剩餘金額) ÷ 面額 − 1；年化 =
        未四捨五入的報酬率 × 12 ÷ 天期。"""
        rate = fx or Decimal(1)
        out = []
        assumed = sc.worse_assumed.value.close if sc.worse_assumed is not None else None
        shares = sc.shares.value.shares if sc.shares is not None else None
        times_fx = f" × {fx}" if fx is not None else ""
        if sc.remainder is None:
            out.append(c.unread("remainder", "找不到「整數股數＝W 股，畸零股數＝F 股，剩餘金額＝P x F＝R」"))
        else:
            rm = sc.remainder.value
            issues = []
            if shares is not None:
                whole = Decimal(int(shares))
                if rm.whole != whole:
                    issues.append(f"整數股數 {rm.whole} 應為 {whole}")
                if rm.fraction != shares - whole or rm.fraction_again != rm.fraction:
                    issues.append(
                        f"畸零股數 {rm.fraction}、{rm.fraction_again} 應為 {shares} - {whole} = {shares - whole}"
                    )
            if assumed is not None and rm.close != assumed:
                issues.append(f"剩餘金額算式的收盤價 {rm.close} 和假設句 {assumed} 不同")
            issues += _fx_issue("剩餘金額算式", rm.fx, fx)
            expected = kit.shown(rm.fraction * rm.close * rate, rm.amount)
            if not within_tolerance(rm.amount, expected):
                issues.append(f"剩餘金額 {rm.amount} 應為 {rm.close} × {rm.fraction}{times_fx} = {expected}")
            out.append(c.calc("remainder", issues, sc.remainder, expected=expected, actual=rm.amount))
        if sc.worse_return is None:
            out += [
                c.unread(key, "找不到「= ((W x P + R) / N) - 1 = r% (平均年化報酬率:y%)」")
                for key in ("return", "annualized")
            ]
            return out
        w = sc.worse_return.value
        issues = []
        if sc.remainder is not None:
            rm = sc.remainder.value
            for what, got, want in (
                ("整數股數", w.whole, rm.whole),
                ("收盤價", w.close, rm.close),
                ("剩餘金額", w.remainder, rm.amount),
            ):
                if got != want:
                    issues.append(f"報酬率算式的{what} {got} 和前面的 {want} 不同")
        if w.divisor != n:
            issues.append(f"報酬率算式的投資金額 {w.divisor} 不等於第一章面額 {n}")
        issues += _fx_issue("報酬率算式", w.fx, fx)
        total = (w.whole * w.close * rate + w.remainder) / w.divisor * 100 - 100
        expected = kit.shown(total, w.pct)
        if not within_tolerance(w.pct, expected):
            issues.append(
                f"報酬率 {w.pct}% 應為 (({w.whole} × {w.close}{times_fx} + {w.remainder}) / {w.divisor}) - 1"
                f" = {expected}%"
            )
        out.append(c.calc("return", issues, sc.worse_return, expected=expected, actual=w.pct))
        annualized = kit.shown(total * 12 / tenor, w.annualized)
        issues = (
            []
            if within_tolerance(w.annualized, annualized)
            else [f"年化報酬率 {w.annualized}% 應為未四捨五入的報酬率 × 12 ÷ {tenor} = {annualized}%"]
        )
        out.append(c.calc("annualized", issues, sc.worse_return, expected=annualized, actual=w.annualized))
        return out


CA = CaStyle()
