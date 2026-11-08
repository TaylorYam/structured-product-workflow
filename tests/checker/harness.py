"""單份核對 harness（不分上手）：一份說明書＋參考條件表跑公開批量入口（預覽＋核對），再依 rule_id 取結果。

說明書旁有同商品投資人須知（`<商品代號>_IIS.pdf`，合成器預設會一起寫出）時一起核對：說明書與投資人須知要
一起選取（ADR 0007），只給說明書會因「這批缺投資人須知」轉人工覆核。

`CONFIG` 是以 repo config 載入一次的核對設定 fixture（交易所假日表的涵蓋範圍延到合成說明書的年份，見 `cover_synthetic_years`）；要換某個設定檔或上手註冊表時用 `load_config`／
`CheckConfig.with_registry`。審查標準的原始內容 `STANDARD` 也只在這裡讀一次，三家合成器都從這裡取固定文字
（固定警語的風險等級佔位符已代入 `SYNTH_RISK_LEVEL`）。

說明書與參考條件表列由各上手的合成器產生（商品規格與參考條件表列在 tests/checker/reference_synth.py）；
這裡不引用任何上手的版面或預設值，也不 import 合成器。各合成器共用的單份核對殼 `SynthChecks`（`check`／`check_item`／
`check_iis`／`check_pair`）在這裡，由合成器給規格、`build_pdf` 與參考條件表列。
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fcn_checker.batch import BatchItem, BatchOutcome, check_batch, preview_batch
from fcn_checker.check_config import CheckConfig, ConfigPaths
from fcn_checker.config import RISK_LEVEL
from fcn_checker.issuers import REGISTRY, Issuer, Template, by_code
from fcn_checker.schema import CheckReport, CheckStatus
from fcn_checker.trading_calendar import Market

if TYPE_CHECKING:
    from pdf_writer import Edit
    from reference_synth import ProductSpec

ROOT = Path(__file__).resolve().parents[2]
REVIEW_STANDARD = ROOT / "config" / "review_standard.toml"
REFERENCE_FORMAT = ROOT / "config" / "reference_sheet.toml"
ISSUER_PREFIXES = ROOT / "config" / "issuer_prefixes.toml"
EXCHANGE_HOLIDAYS = ROOT / "config" / "exchange_holidays.toml"
SYNTHETIC_THROUGH = dt.date(2031, 12, 31)  # 合成說明書的日期在 2030 年前後
SYNTH_RISK_LEVEL = "RR4"  # 合成文件的風險等級（= 審查標準 risk.level，參考條件表沒有「風險等級」欄時的期望值）
_WARNING_KEYS = ("fixed_warning", "fixed_warning_openings", "fixed_warning_variants")


def with_risk_level(table: dict[str, Any], level: str = SYNTH_RISK_LEVEL) -> dict[str, Any]:
    """審查標準原文的固定警語（共用與各上手版本）把風險等級佔位符 {level} 代入 `level`（#239），合成器照寫進文件。"""

    def fill(v: Any) -> Any:
        return v.replace(RISK_LEVEL, level) if isinstance(v, str) else [fill(x) for x in v]

    out = {k: with_risk_level(v, level) if isinstance(v, dict) else v for k, v in table.items()}
    return out | {k: fill(table[k]) for k in _WARNING_KEYS if k in table}


# 審查標準原文，只讀這一次；固定警語的 {level} 已代入合成文件的風險等級
STANDARD: dict[str, Any] = with_risk_level(tomllib.loads(REVIEW_STANDARD.read_text(encoding="utf-8")))
CURRENCY_ISO: dict[str, str] = dict(STANDARD["currency"])  # 幣別中文 → ISO 代碼


def load_config(
    *,
    review_standard: Path = REVIEW_STANDARD,
    reference_format: Path = REFERENCE_FORMAT,
    issuer_prefixes: Path = ISSUER_PREFIXES,
    exchange_holidays: Path = EXCHANGE_HOLIDAYS,
    registry: Sequence[Issuer] = REGISTRY,
) -> CheckConfig:
    """載入核對設定；預設全部用 repo config，交易所假日表另延長涵蓋範圍（`cover_synthetic_years`）。"""
    paths = ConfigPaths(review_standard, reference_format, issuer_prefixes, exchange_holidays)
    return cover_synthetic_years(paths.load(registry))


def registry_of(*templates: Template) -> tuple[Issuer, ...]:
    """只登記 `templates` 的上手清單（照給的順序，同代號的放在同一家，所以同一家要照由專到泛、FCN 最後給）；上手的設定照
    `REGISTRY`（註冊表沒有的代號用預設值）。"""
    grouped: dict[str, list[Template]] = {}
    for t in templates:
        grouped.setdefault(t.code, []).append(t)
    out = []
    for code, ts in grouped.items():
        base = by_code(code)
        out.append(Issuer(code, tuple(ts)) if base is None else replace(base, templates=tuple(ts)))
    return tuple(out)


def cover_synthetic_years(config: CheckConfig) -> CheckConfig:
    """合成說明書的日期在 2030 年前後，超出 repo 假日表的涵蓋範圍：延長到 `SYNTHETIC_THROUGH`（延長的年份沒有休市日），
    期始日規則才判得出不一致，而不是一律轉人工覆核。repo 假日表原有的休市日不變（真實樣本仍照它）。"""
    return with_markets(config, lambda m: replace(m, covered_through=max(m.covered_through, SYNTHETIC_THROUGH)))


def with_markets(config: CheckConfig, change: Callable[[Market], Market]) -> CheckConfig:
    """核對設定：交易所假日表的每個市場套用 `change`（測試延長涵蓋範圍、加休市日用）。"""
    cal = config.exchange_calendar
    return replace(config, exchange_calendar=replace(cal, markets=tuple(map(change, cal.markets))))


CONFIG = load_config()  # 核對設定 fixture：repo config 只載入一次
# 每家上手都支援所有樣態的核對設定：測範本本身的讀法（例：MS 每期觀察 KI，上手支援樣態表標為不支援，Issue #207）
ALL_VARIANTS = replace(CONFIG, unsupported_variants={code: frozenset() for code in CONFIG.unsupported_variants})

PASS, MISMATCH, REVIEW, NA, ERROR = (
    CheckStatus.PASS,
    CheckStatus.MISMATCH,
    CheckStatus.REVIEW_REQUIRED,
    CheckStatus.NOT_APPLICABLE,
    CheckStatus.ERROR,
)


def cli_root(tmp_path: Path, monkeypatch) -> Path:
    """CLI 的根目錄是執行目錄：把設定檔複製到 tmp_path/config 再切換過去，核對紀錄就寫在 tmp_path/runtime/。"""
    shutil.copytree(ROOT / "config", tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def load_record(root: Path) -> dict[str, Any]:
    """根目錄 runtime/核對紀錄/ 下唯一的一份核對紀錄。"""
    [path] = (root / "runtime" / "核對紀錄").glob("*.json")
    return json.loads(path.read_text(encoding="utf-8"))


def iis_path(ts: Path) -> Path:
    """說明書 `<商品代號>_TS.pdf` 旁的同商品投資人須知 `<商品代號>_IIS.pdf`（各上手合成器共用）。"""
    return ts.with_name(ts.stem[: -len("_TS")] + "_IIS.pdf")


def with_iis(pdfs: Sequence[Path]) -> list[Path]:
    """每份 `<商品代號>_TS.pdf` 旁若有同商品 `<商品代號>_IIS.pdf` 且沒選到，就加在最後（說明書的順序不變）。"""
    out = [Path(p) for p in pdfs]
    for p in list(out):
        sibling = iis_path(p)
        if p.stem.endswith("_TS") and sibling.exists() and sibling not in out:
            out.append(sibling)
    return out


def check_all(sheet: Path, pdfs: Sequence[Path], config: CheckConfig = CONFIG) -> BatchOutcome:
    """預覽＋核對（不儲存），回傳批量核對結果；說明書旁的同商品投資人須知一起核對（`with_iis`）。"""
    return check_batch(preview_batch(config, sheet, with_iis(pdfs)))


def check_sheet(pdf: Path, sheet: Path, config: CheckConfig = CONFIG) -> CheckReport:
    """以既有的參考條件表核對一份說明書，回傳該份的 CheckReport。"""
    return check_all(sheet, [pdf], config).items[0].report


@dataclass(frozen=True)
class SynthChecks:
    """一家上手（或商品族）合成器的單份核對殼：合成說明書＋同商品投資人須知＋參考條件表一列，經批量入口核對。

    各合成器建一次（`CHECKS = SynthChecks(Spec, build_pdf, row)`），模組層再取成 `check`／`check_item`／`check_iis`／
    `check_pair`。`default()` 是沒給規格時的商品規格；`build_pdf(path, spec, edits=...)` 寫說明書與投資人須知；
    `row(spec, **overrides)` 是與 spec 一致的參考條件表列（Citi、JPM 另收標的排序 `order`）；`headers` 是參考條件表
    預設欄名（None = 正式版面）。這裡只收各上手給的函式，不引用任何上手的版面或預設值。
    """

    default: Callable[[], ProductSpec]
    build_pdf: Callable[..., Path]
    row: Callable[..., dict[str, Any]]
    headers: Sequence[str] | None = None

    def family(self, default: ProductSpec, *, headers: Sequence[str] | None = None) -> SynthChecks:
        """同一家的商品族（例：DACN、BEN）：預設規格換成 `default`，參考條件表預設欄名換成 `headers`。"""
        return replace(self, default=lambda: default, headers=headers)

    def run(
        self,
        tmp_path: Path,
        spec: ProductSpec | None = None,
        *,
        pdf_spec: ProductSpec | None = None,
        edits: Sequence[Edit] = (),
        headers: Sequence[str] | None = None,
        config: CheckConfig = CONFIG,
        **overrides: Any,
    ) -> tuple[Path, BatchOutcome]:
        """合成說明書（`pdf_spec`，預設同 `spec`；`edits` 改字）＋投資人須知＋與 `spec` 一致的參考條件表一列（`overrides`
        以 Excel 欄名覆寫，`headers` 預設 `self.headers`），經批量入口核對，回傳（說明書路徑, 批量核對結果）。"""
        from reference_synth import build_reference_sheet  # reference_synth 引用本模組：呼叫時才引用，免得循環

        if nested := [name for name, value in overrides.items() if isinstance(value, dict)]:
            # 舊寫法 `overrides={…}` 會被當成一個叫 overrides 的欄、悄悄丟掉；欄名直接當關鍵字傳
            raise TypeError(f"參考條件表覆寫要用欄名當關鍵字，不是 dict：{nested}")
        spec = spec or self.default()
        tmp_path.mkdir(parents=True, exist_ok=True)
        pdf = self.build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", pdf_spec or spec, edits=edits)
        rows = [self.row(spec, **overrides)]
        sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows, self.headers if headers is None else headers)
        return pdf, check_all(sheet, [pdf], config)

    def check_item(self, tmp_path: Path, spec: ProductSpec | None = None, **kw: Any) -> BatchItem:
        """同 `run`，回傳說明書的批量核對項目（含 `partner` 投資人須知）。"""
        pdf, outcome = self.run(tmp_path, spec, **kw)
        return next(i for i in outcome.items if i.term_sheet == pdf)

    def check(self, tmp_path: Path, spec: ProductSpec | None = None, **kw: Any) -> CheckReport:
        """同 `run`，回傳說明書的 CheckReport。"""
        return self.check_item(tmp_path, spec, **kw).report

    def check_iis(self, tmp_path: Path, spec: ProductSpec | None = None, **kw: Any) -> BatchItem:
        """同 `run`，回傳投資人須知的批量核對項目。"""
        pdf, outcome = self.run(tmp_path, spec, **kw)
        return next(i for i in outcome.items if i.term_sheet == iis_path(pdf))

    def check_pair(self, tmp_path: Path, spec: ProductSpec | None = None, **kw: Any) -> tuple[BatchItem, BatchItem]:
        """同 `run`，回傳（說明書, 投資人須知）的批量核對項目。"""
        pdf, outcome = self.run(tmp_path, spec, **kw)
        ts = next(i for i in outcome.items if i.term_sheet == pdf)
        return ts, next(i for i in outcome.items if i.term_sheet != pdf)


# 上手規則判錯的那一處，共用規則也會報（例：括號內幣別代碼錯，`doc.currency_consistency` 和
# `standard.currency_code` 都不一致，Issue #227）：只改一處重複出現處的測試允許它們一起出現
ALSO_FLAGGED = {"doc.currency_consistency": frozenset({"standard.currency_code"})}


def also_flagged(rule_id: str) -> frozenset[str]:
    """`rule_id` 判錯時共用規則也會報的 rule_id。"""
    return ALSO_FLAGGED.get(rule_id, frozenset())


def problems(report: CheckReport) -> set[tuple[str, CheckStatus]]:
    return {(r.rule_id, r.status) for r in report.results if r.status not in (PASS, NA)}


PROBLEMS = (MISMATCH, REVIEW, ERROR)


def field_problems(report: CheckReport) -> list[tuple[str, str, CheckStatus]]:
    """不一致、待人工覆核與執行錯誤的結果：（rule_id, 欄位, 狀態），依結果順序。"""
    return [(r.rule_id, r.field, r.status) for r in report.results if r.status in PROBLEMS]


def flagged(report: CheckReport, rule_id: str, status: CheckStatus | None = None) -> bool:
    """`rule_id` 有任何一筆結果是 `status`（沒給時為任一種問題）。"""
    return any(r.rule_id == rule_id and r.status in ((status,) if status else PROBLEMS) for r in report.results)


def results(report: CheckReport, rule_id: str, field: str | None = None):
    out = [r for r in report.results if r.rule_id == rule_id and (field is None or r.field == field)]
    assert out, f"沒有 {rule_id} {field or ''} 的結果"
    return out
