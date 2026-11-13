"""上手註冊表：上手（`Issuer`：代號、信用評等核對、說明書內部規則可讀的參考條件表欄位（ADR 0009）、讀出後的
交易所假日表推算）底下掛自己的範本（`Template`：一個商品家族的說明書辨識、讀出、說明書內部規則與投資人須知範本，
ADR 0007；沒有投資人須知範本的上手，投資人須知是未支援上手）。

範本 adapter 只提供這幾樣；參考條件表欄位、審查標準、Non-Call／ISIN／發行日／比價日與回填都是各上手共用的規則，
由單份核對（single_check.py）依序執行。「這份文件是哪家上手的哪個範本」：跨上手要求剛好一家命中（零家或多家轉人工覆核），
同一家之內由專到泛（DACN／Super Bull／BEN 先、FCN 最後）取第一個命中，所以 FCN 範本不必寫「不是 DACN 才算」。
新增上手在 `REGISTRY` 登記一筆、在 `config/issuer_prefixes.toml` 登記上手編號；新增（上手, 商品家族）只多一個範本物件，
放在該上手範本清單的前面，並在 `[auto_check_products]` 與審查標準 `[issuer.<x>.product_names.<P>]` 各加一段
（docs/checker/issuer-onboarding.md §6）；批量入口、CLI 與 PANEL 都由這裡分派。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .investor_sheet import IisSheet
from .parsers import barc as barc_parser
from .parsers import barc_dacn as barc_dacn_parser
from .parsers import (
    barc_dacn_iis,
    barc_iis,
    barc_stepdown_iis,
    bbva_iis,
    bnp_iis,
    bnp_super_bull_iis,
    ca_ben_iis,
    ca_iis,
    ca_super_bull_iis,
    citi_iis,
    dbs_dacn_iis,
    dbs_iis,
    gs_iis,
    hsbc_ben_iis,
    hsbc_iis,
    hsbc_super_bull_iis,
    jpm_iis,
    ms_ben_iis,
    ms_iis,
    natixis_dacn_iis,
    natixis_iis,
    natixis_stepdown_iis,
    nomura_dacn_iis,
    nomura_iis,
    sg_dacn_iis,
    sg_iis,
    ubs_iis,
)
from .parsers import barc_stepdown as barc_stepdown_parser
from .parsers import bbva as bbva_parser
from .parsers import bnp as bnp_parser
from .parsers import bnp_super_bull as bnp_sb_parser
from .parsers import ca as ca_parser
from .parsers import ca_ben as ca_ben_parser
from .parsers import ca_super_bull as ca_sb_parser
from .parsers import citi as citi_parser
from .parsers import dbs as dbs_parser
from .parsers import dbs_dacn as dbs_dacn_parser
from .parsers import gs as gs_parser
from .parsers import hsbc as hsbc_parser
from .parsers import hsbc_ben as hsbc_ben_parser
from .parsers import hsbc_super_bull as hsbc_sb_parser
from .parsers import jpm as jpm_parser
from .parsers import ms as ms_parser
from .parsers import ms_ben as ms_ben_parser
from .parsers import natixis as natixis_parser
from .parsers import natixis_dacn as natixis_dacn_parser
from .parsers import natixis_stepdown as natixis_stepdown_parser
from .parsers import nomura as nomura_parser
from .parsers import nomura_dacn as nomura_dacn_parser
from .parsers import sg as sg_parser
from .parsers import sg_dacn as sg_dacn_parser
from .parsers import ubs as ubs_parser
from .products import BEN, DACN, FCN, FCN_EXPRESS, FCN_STEPDOWN, SUPER_BULL
from .rules import barc as barc_rules
from .rules import barc_dacn as barc_dacn_rules
from .rules import barc_dacn_iis as barc_dacn_iis_rules
from .rules import barc_stepdown as barc_stepdown_rules
from .rules import bbva as bbva_rules
from .rules import bbva_iis as bbva_iis_rules
from .rules import bnp as bnp_rules
from .rules import bnp_super_bull as bnp_sb_rules
from .rules import bnp_super_bull_iis as bnp_sb_iis_rules
from .rules import ca as ca_rules
from .rules import ca_ben as ca_ben_rules
from .rules import ca_ben_iis as ca_ben_iis_rules
from .rules import ca_iis as ca_iis_rules
from .rules import ca_super_bull as ca_sb_rules
from .rules import ca_super_bull_iis as ca_sb_iis_rules
from .rules import citi as citi_rules
from .rules import citi_iis as citi_iis_rules
from .rules import dbs as dbs_rules
from .rules import dbs_dacn as dbs_dacn_rules
from .rules import dbs_dacn_iis as dbs_dacn_iis_rules
from .rules import dbs_iis as dbs_iis_rules
from .rules import gs as gs_rules
from .rules import gs_iis as gs_iis_rules
from .rules import hsbc as hsbc_rules
from .rules import hsbc_ben as hsbc_ben_rules
from .rules import hsbc_ben_iis as hsbc_ben_iis_rules
from .rules import hsbc_super_bull as hsbc_sb_rules
from .rules import hsbc_super_bull_iis as hsbc_sb_iis_rules
from .rules import jpm as jpm_rules
from .rules import jpm_iis as jpm_iis_rules
from .rules import ms as ms_rules
from .rules import ms_ben as ms_ben_rules
from .rules import ms_ben_iis as ms_ben_iis_rules
from .rules import ms_iis as ms_iis_rules
from .rules import natixis as natixis_rules
from .rules import natixis_dacn as natixis_dacn_rules
from .rules import natixis_dacn_iis as natixis_dacn_iis_rules
from .rules import natixis_iis as natixis_iis_rules
from .rules import natixis_stepdown as natixis_stepdown_rules
from .rules import natixis_stepdown_iis as natixis_stepdown_iis_rules
from .rules import nomura as nomura_rules
from .rules import nomura_dacn as nomura_dacn_rules
from .rules import nomura_dacn_iis as nomura_dacn_iis_rules
from .rules import nomura_iis as nomura_iis_rules
from .rules import sg as sg_rules
from .rules import sg_dacn as sg_dacn_rules
from .rules import sg_dacn_iis as sg_dacn_iis_rules
from .rules import sg_iis as sg_iis_rules
from .rules import ubs as ubs_rules
from .rules import ubs_iis as ubs_iis_rules
from .rules.iis import IisIssuerContext
from .rules.kit import IssuerContext
from .schema import CheckResult, CheckStatus, DetectionResult, Evidence, Item, Line
from .standard_fields import TermSheet
from .trading_calendar import TradingCalendar


@dataclass(frozen=True)
class IisTemplate:
    """某上手的投資人須知範本：檢查點依範本實際有的欄位（`IisSheet.provides`），規則各上手共用（rules/iis.py）。

    範本另有共用規則表達不了的檢查（例：MS 商品種類依標的數）時，由 `rules` 提供；只拿到投資人須知與同商品說明書的
    讀出結果，不含參考條件表（同上手說明書內部規則）。
    """

    template_id: str
    label: str
    parser_version: str
    detect: Callable[[Sequence[Line]], DetectionResult]
    read: Callable[[Sequence[Line]], IisSheet]
    not_covered: tuple[dict[str, str], ...] = ()  # 範本未涵蓋的型態（PANEL「待處理」與核對紀錄）
    rules: Callable[[IisIssuerContext], list[CheckResult]] | None = None  # 範本專屬的投資人須知規則


@dataclass(frozen=True)
class Template:
    """上手的一個說明書範本（一個商品家族）：辨識、讀出、說明書內部規則與同商品的投資人須知範本。"""

    code: str  # 屬於哪家上手（上手代號）；和所在 `Issuer.code` 相同
    template_id: str
    label: str
    parser_version: str
    not_covered: tuple[dict[str, str], ...]  # 「未涵蓋」的固定清單（PANEL「待處理」分頁與核對紀錄）
    # 辨識：文字行 → 是否為這個範本（含證據）；一次核對中每份說明書只呼叫一次
    detect: Callable[[Sequence[Line]], DetectionResult]
    # 讀出：文字行 → 標準欄位（standard_fields.STANDARD_FIELDS）＋該範本規則需要的專屬資料；每份只呼叫一次
    read: Callable[[Sequence[Line]], TermSheet]
    # 說明書內部規則：只用讀出結果與審查標準，拿不到參考條件表的列與格式設定（可讀的欄位由上手宣告）
    rules: Callable[[IssuerContext], list[CheckResult]]
    # 投資人須知範本；None → 這個範本的投資人須知是未支援上手
    iis: IisTemplate | None = None
    # 範本涵蓋的商品種類（參考條件表 Product 的寫法）：辨識到的範本和該列 Product 不同時轉人工覆核（#311）；Product 空白視為 FCN
    products: frozenset[str] = frozenset({FCN, FCN_EXPRESS})


@dataclass(frozen=True)
class Issuer:
    """一家上手：上手的事只寫一次，底下的範本由專到泛排（商品家族在前、FCN 最後）。"""

    code: str  # 上手代號（例：BARC），與上手編號對照、參考條件表「發行機構」設定相同
    templates: tuple[Template, ...]  # 由專到泛：辨識時同一家取第一個命中的範本
    # 說明書內部規則可讀的參考條件表欄位（ADR 0009，須逐一宣告：BARC 月配息率推算的年利率與天期（Issue #54）、
    # MS 月配息率與年化報酬率的年利率（說明書沒有年利率，Issue #135））；這家上手的每個範本共用
    reference_fields: tuple[str, ...] = ()
    # 說明書與投資人須知的信用評等核對（rules/ratings.py，#240～#243）：parser 交出 `issuer_ratings`／`guarantor_ratings` 的上手才標。
    # 現有 14 家都標了；留著給新上手：上線時評等還沒做的不跑這條規則、不產生結果（docs/checker/issuer-onboarding.md §6.2a）
    ratings: bool = False
    # 讀出後用交易所假日表補上推算的欄位（CA：沒有 Non-Call 時第一個觀察日 = 發行日後下一個一籃子預定交易日，workflow #305）；
    # 套在這家上手每個範本的說明書讀出結果上；None → 不用補
    with_calendar: Callable[[TermSheet, TradingCalendar], TermSheet] | None = None

    def __post_init__(self) -> None:
        if not self.templates or any(t.code != self.code for t in self.templates):
            raise ValueError(f"上手 {self.code} 的範本清單是空的或含別家上手的範本")

    @property
    def has_iis(self) -> bool:
        """至少一個範本有投資人須知範本；沒有時這家上手的投資人須知是未支援上手。"""
        return any(t.iis is not None for t in self.templates)


_IIS_SAMPLES = {
    "rule_id": "iis.template_variants",
    "description": "投資人須知依各 5～8 份樣本建立（docs/checker/templates/*-zh-iis.md 樣本總表；MS 只支援其中新版 6 份）："
    "其他型態的寫法未驗證，範本以外的欄位或寫法會轉人工覆核",
}

BARC = Template(
    code=barc_rules.ISSUER,
    template_id=barc_parser.TEMPLATE_ID,
    label="BARC 中文產品說明書",
    parser_version=barc_parser.PARSER_VERSION,
    not_covered=tuple(barc_rules.NOT_COVERED),
    detect=lambda lines: barc_parser.detect(barc_parser.document(lines)),
    read=barc_parser.read,
    rules=barc_rules.run_all,
    iis=IisTemplate(
        template_id=barc_iis.TEMPLATE_ID,
        label="BARC 中文投資人須知",
        parser_version=barc_iis.PARSER_VERSION,
        detect=barc_iis.detect,
        read=barc_iis.read,
        not_covered=(_IIS_SAMPLES,),
    ),
)

BARC_STEPDOWN = Template(
    code=barc_rules.ISSUER,
    template_id=barc_stepdown_parser.TEMPLATE_ID,
    label="BARC FCN_Stepdown 中文產品說明書",
    parser_version=barc_stepdown_parser.PARSER_VERSION,
    not_covered=tuple(barc_stepdown_rules.NOT_COVERED),
    detect=lambda lines: barc_stepdown_parser.detect(barc_parser.document(lines)),
    read=barc_stepdown_parser.read,
    rules=barc_stepdown_rules.run_all,
    iis=IisTemplate(
        template_id=barc_stepdown_iis.TEMPLATE_ID,
        label="BARC FCN_Stepdown 中文投資人須知",
        parser_version=barc_stepdown_iis.PARSER_VERSION,
        detect=barc_stepdown_iis.detect,
        read=barc_stepdown_iis.read,
        not_covered=(_IIS_SAMPLES,),
    ),
    products=frozenset({FCN_STEPDOWN}),
)

# BARC DACN（workflow #547）：和 BARC FCN 同一家上手、不同範本（名稱相同，依第 14 條配息率 t 公式與配息門檻辨識，
# 排在 BARC FCN 前面）
BARC_DACN = Template(
    code=barc_dacn_rules.ISSUER,
    template_id=barc_dacn_parser.TEMPLATE_ID,
    label="BARC DACN 中文產品說明書",
    parser_version=barc_dacn_parser.PARSER_VERSION,
    not_covered=tuple(barc_dacn_rules.NOT_COVERED),
    detect=barc_dacn_parser.detect,
    read=barc_dacn_parser.read,
    rules=barc_dacn_rules.run_all,
    iis=IisTemplate(
        template_id=barc_dacn_iis.TEMPLATE_ID,
        label="BARC DACN 中文投資人須知",
        parser_version=barc_dacn_iis.PARSER_VERSION,
        detect=barc_dacn_iis.detect,
        read=barc_dacn_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=barc_dacn_iis_rules.run_all,
    ),
    products=frozenset({DACN}),
)

HSBC = Template(
    code=hsbc_rules.ISSUER,
    template_id=hsbc_parser.TEMPLATE_ID,
    label="HSBC 中文產品說明書",
    parser_version=hsbc_parser.PARSER_VERSION,
    not_covered=tuple(hsbc_rules.NOT_COVERED),
    detect=hsbc_parser.detect,
    read=hsbc_parser.read,
    rules=hsbc_rules.run_all,
    iis=IisTemplate(
        template_id=hsbc_iis.TEMPLATE_ID,
        label="HSBC 中文投資人須知",
        parser_version=hsbc_iis.PARSER_VERSION,
        detect=hsbc_iis.detect,
        read=hsbc_iis.read,
        not_covered=(_IIS_SAMPLES,),
    ),
)

# HSBC Super Bull（#314）：和 HSBC FCN 同一家上手、不同範本（商品種類須和參考條件表 Product 相同，檢查在辨識）
HSBC_SUPER_BULL = Template(
    code=hsbc_rules.ISSUER,
    template_id=hsbc_sb_parser.TEMPLATE_ID,
    label="HSBC Super Bull 中文產品說明書",
    parser_version=hsbc_sb_parser.PARSER_VERSION,
    not_covered=tuple(hsbc_sb_rules.NOT_COVERED),
    detect=hsbc_sb_parser.detect,
    read=hsbc_sb_parser.read,
    rules=hsbc_sb_rules.run_all,
    iis=IisTemplate(
        template_id=hsbc_super_bull_iis.TEMPLATE_ID,
        label="HSBC Super Bull 中文投資人須知",
        parser_version=hsbc_super_bull_iis.PARSER_VERSION,
        detect=hsbc_super_bull_iis.detect,
        read=hsbc_super_bull_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=hsbc_sb_iis_rules.run_all,
    ),
    products=frozenset({SUPER_BULL}),
)

# HSBC BEN（#375）：Maxi 與 Digital 同一個範本（同 Super Bull）
HSBC_BEN = Template(
    code=hsbc_rules.ISSUER,
    template_id=hsbc_ben_parser.TEMPLATE_ID,
    label="HSBC BEN 中文產品說明書",
    parser_version=hsbc_ben_parser.PARSER_VERSION,
    not_covered=tuple(hsbc_ben_rules.NOT_COVERED),
    detect=hsbc_ben_parser.detect,
    read=hsbc_ben_parser.read,
    rules=hsbc_ben_rules.run_all,
    iis=IisTemplate(
        template_id=hsbc_ben_iis.TEMPLATE_ID,
        label="HSBC BEN 中文投資人須知",
        parser_version=hsbc_ben_iis.PARSER_VERSION,
        detect=hsbc_ben_iis.detect,
        read=hsbc_ben_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=hsbc_ben_iis_rules.run_all,
    ),
    products=frozenset({BEN}),
)

MS = Template(
    code=ms_rules.ISSUER,
    template_id=ms_parser.TEMPLATE_ID,
    label="MS 中文產品說明書",
    parser_version=ms_parser.PARSER_VERSION,
    not_covered=tuple(ms_rules.NOT_COVERED),
    detect=ms_parser.detect,
    read=ms_parser.read,
    rules=ms_rules.run_all,
    iis=IisTemplate(
        template_id=ms_iis.TEMPLATE_ID,
        label="MS 中文投資人須知",
        parser_version=ms_iis.PARSER_VERSION,
        detect=ms_iis.detect,
        read=ms_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=ms_iis_rules.run_all,
    ),
)

# MS BEN（#376）：一般 BEN 與 Digital（各有 lookback）同一個範本（同 HSBC BEN）
MS_BEN = Template(
    code=ms_rules.ISSUER,
    template_id=ms_ben_parser.TEMPLATE_ID,
    label="MS BEN 中文產品說明書",
    parser_version=ms_ben_parser.PARSER_VERSION,
    not_covered=tuple(ms_ben_rules.NOT_COVERED),
    detect=ms_ben_parser.detect,
    read=ms_ben_parser.read,
    rules=ms_ben_rules.run_all,
    iis=IisTemplate(
        template_id=ms_ben_iis.TEMPLATE_ID,
        label="MS BEN 中文投資人須知",
        parser_version=ms_ben_iis.PARSER_VERSION,
        detect=ms_ben_iis.detect,
        read=ms_ben_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=ms_ben_iis_rules.run_all,
    ),
    products=frozenset({BEN}),
)

BNP = Template(
    code=bnp_rules.ISSUER,
    template_id=bnp_parser.TEMPLATE_ID,
    label="BNP 中文產品說明書",
    parser_version=bnp_parser.PARSER_VERSION,
    not_covered=tuple(bnp_rules.NOT_COVERED),
    detect=bnp_parser.detect,
    read=bnp_parser.read,
    rules=bnp_rules.run_all,
    iis=IisTemplate(
        template_id=bnp_iis.TEMPLATE_ID,
        label="BNP 中文投資人須知",
        parser_version=bnp_iis.PARSER_VERSION,
        detect=bnp_iis.detect,
        read=bnp_iis.read,
        not_covered=(_IIS_SAMPLES,),
    ),
)

# BNP Super Bull（#311）：和 BNP FCN 同一家上手、不同範本（商品種類須和參考條件表 Product 相同，檢查在辨識）
BNP_SUPER_BULL = Template(
    code=bnp_rules.ISSUER,
    template_id=bnp_sb_parser.TEMPLATE_ID,
    label="BNP Super Bull 中文產品說明書",
    parser_version=bnp_sb_parser.PARSER_VERSION,
    not_covered=tuple(bnp_sb_rules.NOT_COVERED),
    detect=bnp_sb_parser.detect,
    read=bnp_sb_parser.read,
    rules=bnp_sb_rules.run_all,
    iis=IisTemplate(
        template_id=bnp_super_bull_iis.TEMPLATE_ID,
        label="BNP Super Bull 中文投資人須知",
        parser_version=bnp_super_bull_iis.PARSER_VERSION,
        detect=bnp_super_bull_iis.detect,
        read=bnp_super_bull_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=bnp_sb_iis_rules.run_all,
    ),
    products=frozenset({SUPER_BULL}),
)

SG = Template(
    code=sg_rules.ISSUER,
    template_id=sg_parser.TEMPLATE_ID,
    label="SG 中文產品說明書",
    parser_version=sg_parser.PARSER_VERSION,
    not_covered=tuple(sg_rules.NOT_COVERED),
    detect=sg_parser.detect,
    read=sg_parser.read,
    rules=sg_rules.run_all,
    iis=IisTemplate(
        template_id=sg_iis.TEMPLATE_ID,
        label="SG 中文投資人須知",
        parser_version=sg_iis.PARSER_VERSION,
        detect=sg_iis.detect,
        read=sg_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=sg_iis_rules.run_all,
    ),
)

# SG DACN（workflow #369）：和 SG FCN 同一家上手、不同範本（依第 8 項 n(i)／N(i) 區間累積配息公式辨識，排在 SG FCN 前面）
SG_DACN = Template(
    code=sg_rules.ISSUER,
    template_id=sg_dacn_parser.TEMPLATE_ID,
    label="SG DACN 中文產品說明書",
    parser_version=sg_dacn_parser.PARSER_VERSION,
    not_covered=tuple(sg_dacn_rules.NOT_COVERED),
    detect=sg_dacn_parser.detect,
    read=sg_dacn_parser.read,
    rules=sg_dacn_rules.run_all,
    iis=IisTemplate(
        template_id=sg_dacn_iis.TEMPLATE_ID,
        label="SG DACN 中文投資人須知",
        parser_version=sg_dacn_iis.PARSER_VERSION,
        detect=sg_dacn_iis.detect,
        read=sg_dacn_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=sg_dacn_iis_rules.run_all,
    ),
    products=frozenset({DACN}),
)

CITI = Template(
    code=citi_rules.ISSUER,
    template_id=citi_parser.TEMPLATE_ID,
    label="Citi 中文產品說明書",
    parser_version=citi_parser.PARSER_VERSION,
    not_covered=tuple(citi_rules.NOT_COVERED),
    detect=citi_parser.detect,
    read=citi_parser.read,
    rules=citi_rules.run_all,
    iis=IisTemplate(
        template_id=citi_iis.TEMPLATE_ID,
        label="Citi 中文投資人須知",
        parser_version=citi_iis.PARSER_VERSION,
        detect=citi_iis.detect,
        read=citi_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=citi_iis_rules.run_all,
    ),
)

JPM = Template(
    code=jpm_rules.ISSUER,
    template_id=jpm_parser.TEMPLATE_ID,
    label="JPM 中文產品說明書",
    parser_version=jpm_parser.PARSER_VERSION,
    not_covered=tuple(jpm_rules.NOT_COVERED),
    detect=jpm_parser.detect,
    read=jpm_parser.read,
    rules=jpm_rules.run_all,
    iis=IisTemplate(
        template_id=jpm_iis.TEMPLATE_ID,
        label="JPM 中文投資人須知",
        parser_version=jpm_iis.PARSER_VERSION,
        detect=jpm_iis.detect,
        read=jpm_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=jpm_iis_rules.run_all,
    ),
)

NOMURA = Template(
    code=nomura_rules.ISSUER,
    template_id=nomura_parser.TEMPLATE_ID,
    label="Nomura 中文產品說明書",
    parser_version=nomura_parser.PARSER_VERSION,
    not_covered=tuple(nomura_rules.NOT_COVERED),
    detect=nomura_parser.detect,
    read=nomura_parser.read,
    rules=nomura_rules.run_all,
    iis=IisTemplate(
        template_id=nomura_iis.TEMPLATE_ID,
        label="Nomura 中文投資人須知",
        parser_version=nomura_iis.PARSER_VERSION,
        detect=nomura_iis.detect,
        read=nomura_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=nomura_iis_rules.run_all,
    ),
)

# Nomura DACN（workflow #368）：和 Nomura FCN 同一家上手、不同範本（依第 7 項每日計息公式辨識，排在 Nomura FCN 前面）
NOMURA_DACN = Template(
    code=nomura_rules.ISSUER,
    template_id=nomura_dacn_parser.TEMPLATE_ID,
    label="Nomura DACN 中文產品說明書",
    parser_version=nomura_dacn_parser.PARSER_VERSION,
    not_covered=tuple(nomura_dacn_rules.NOT_COVERED),
    detect=nomura_dacn_parser.detect,
    read=nomura_dacn_parser.read,
    rules=nomura_dacn_rules.run_all,
    iis=IisTemplate(
        template_id=nomura_dacn_iis.TEMPLATE_ID,
        label="Nomura DACN 中文投資人須知",
        parser_version=nomura_dacn_iis.PARSER_VERSION,
        detect=nomura_dacn_iis.detect,
        read=nomura_dacn_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=nomura_dacn_iis_rules.run_all,
    ),
    products=frozenset({DACN}),
)

GS = Template(
    code=gs_rules.ISSUER,
    template_id=gs_parser.TEMPLATE_ID,
    label="GS 中文產品說明書",
    parser_version=gs_parser.PARSER_VERSION,
    not_covered=tuple(gs_rules.NOT_COVERED),
    detect=gs_parser.detect,
    read=gs_parser.read,
    rules=gs_rules.run_all,
    iis=IisTemplate(
        template_id=gs_iis.TEMPLATE_ID,
        label="GS 中文投資人須知",
        parser_version=gs_iis.PARSER_VERSION,
        detect=gs_iis.detect,
        read=gs_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=gs_iis_rules.run_all,
    ),
)

NATIXIS = Template(
    code=natixis_rules.ISSUER,
    template_id=natixis_parser.TEMPLATE_ID,
    label="Natixis 中文產品說明書",
    parser_version=natixis_parser.PARSER_VERSION,
    not_covered=tuple(natixis_rules.NOT_COVERED),
    detect=natixis_parser.detect,
    read=natixis_parser.read,
    rules=natixis_rules.run_all,
    iis=IisTemplate(
        template_id=natixis_iis.TEMPLATE_ID,
        label="Natixis 中文投資人須知",
        parser_version=natixis_iis.PARSER_VERSION,
        detect=natixis_iis.detect,
        read=natixis_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=natixis_iis_rules.run_all,
    ),
)

# Natixis DACN（workflow #371）：和 Natixis FCN 同一家上手、不同範本（依第 13 項「變動配息金額」公式與配息界限價格辨識，
# 排在 Natixis FCN 前面）
NATIXIS_DACN = Template(
    code=natixis_rules.ISSUER,
    template_id=natixis_dacn_parser.TEMPLATE_ID,
    label="Natixis DACN 中文產品說明書",
    parser_version=natixis_dacn_parser.PARSER_VERSION,
    not_covered=tuple(natixis_dacn_rules.NOT_COVERED),
    detect=natixis_dacn_parser.detect,
    read=natixis_dacn_parser.read,
    rules=natixis_dacn_rules.run_all,
    iis=IisTemplate(
        template_id=natixis_dacn_iis.TEMPLATE_ID,
        label="Natixis DACN 中文投資人須知",
        parser_version=natixis_dacn_iis.PARSER_VERSION,
        detect=natixis_dacn_iis.detect,
        read=natixis_dacn_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=natixis_dacn_iis_rules.run_all,
    ),
    products=frozenset({DACN}),
)

# Natixis FCN_Stepdown（workflow #545）：和 Natixis FCN 同一家上手、不同範本（依第 14 項「為下表所列之期初進場價格」與每期 KO 表
# 辨識，排在 Natixis DACN、FCN 前面）
NATIXIS_STEPDOWN = Template(
    code=natixis_rules.ISSUER,
    template_id=natixis_stepdown_parser.TEMPLATE_ID,
    label="Natixis FCN_Stepdown 中文產品說明書",
    parser_version=natixis_stepdown_parser.PARSER_VERSION,
    not_covered=tuple(natixis_stepdown_rules.NOT_COVERED),
    detect=natixis_stepdown_parser.detect,
    read=natixis_stepdown_parser.read,
    rules=natixis_stepdown_rules.run_all,
    iis=IisTemplate(
        template_id=natixis_stepdown_iis.TEMPLATE_ID,
        label="Natixis FCN_Stepdown 中文投資人須知",
        parser_version=natixis_stepdown_iis.PARSER_VERSION,
        detect=natixis_stepdown_iis.detect,
        read=natixis_stepdown_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=natixis_stepdown_iis_rules.run_all,
    ),
    products=frozenset({FCN_STEPDOWN}),
)

CA = Template(
    code=ca_rules.ISSUER,
    template_id=ca_parser.TEMPLATE_ID,
    label="CA 中文產品說明書",
    parser_version=ca_parser.PARSER_VERSION,
    not_covered=tuple(ca_rules.NOT_COVERED),
    detect=ca_parser.detect,
    read=ca_parser.read,
    rules=ca_rules.run_all,
    iis=IisTemplate(
        template_id=ca_iis.TEMPLATE_ID,
        label="CA 中文投資人須知",
        parser_version=ca_iis.PARSER_VERSION,
        detect=ca_iis.detect,
        read=ca_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=ca_iis_rules.run_all,
    ),
)

# CA Super Bull（#312）：和 CA FCN 同一家上手、不同範本（商品種類須和參考條件表 Product 相同，檢查在辨識）
CA_SUPER_BULL = Template(
    code=ca_rules.ISSUER,
    template_id=ca_sb_parser.TEMPLATE_ID,
    label="CA Super Bull 中文產品說明書",
    parser_version=ca_sb_parser.PARSER_VERSION,
    not_covered=tuple(ca_sb_rules.NOT_COVERED),
    detect=ca_sb_parser.detect,
    read=ca_sb_parser.read,
    rules=ca_sb_rules.run_all,
    iis=IisTemplate(
        template_id=ca_super_bull_iis.TEMPLATE_ID,
        label="CA Super Bull 中文投資人須知",
        parser_version=ca_super_bull_iis.PARSER_VERSION,
        detect=ca_super_bull_iis.detect,
        read=ca_super_bull_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=ca_sb_iis_rules.run_all,
    ),
    products=frozenset({SUPER_BULL}),
)

CA_BEN = Template(
    code=ca_rules.ISSUER,
    template_id=ca_ben_parser.TEMPLATE_ID,
    label="CA BEN 中文產品說明書",
    parser_version=ca_ben_parser.PARSER_VERSION,
    not_covered=tuple(ca_ben_rules.NOT_COVERED),
    detect=ca_ben_parser.detect,
    read=ca_ben_parser.read,
    rules=ca_ben_rules.run_all,
    iis=IisTemplate(
        template_id=ca_ben_iis.TEMPLATE_ID,
        label="CA BEN 中文投資人須知",
        parser_version=ca_ben_iis.PARSER_VERSION,
        detect=ca_ben_iis.detect,
        read=ca_ben_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=ca_ben_iis_rules.run_all,
    ),
    products=frozenset({BEN}),
)

BBVA = Template(
    code=bbva_rules.ISSUER,
    template_id=bbva_parser.TEMPLATE_ID,
    label="BBVA 中文產品說明書",
    parser_version=bbva_parser.PARSER_VERSION,
    not_covered=tuple(bbva_rules.NOT_COVERED),
    detect=bbva_parser.detect,
    read=bbva_parser.read,
    rules=bbva_rules.run_all,
    iis=IisTemplate(
        template_id=bbva_iis.TEMPLATE_ID,
        label="BBVA 中文投資人須知",
        parser_version=bbva_iis.PARSER_VERSION,
        detect=bbva_iis.detect,
        read=bbva_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=bbva_iis_rules.run_all,
    ),
)

DBS = Template(
    code=dbs_rules.ISSUER,
    template_id=dbs_parser.TEMPLATE_ID,
    label="DBS 中文產品說明書",
    parser_version=dbs_parser.PARSER_VERSION,
    not_covered=tuple(dbs_rules.NOT_COVERED),
    detect=dbs_parser.detect,
    read=dbs_parser.read,
    rules=dbs_rules.run_all,
    iis=IisTemplate(
        template_id=dbs_iis.TEMPLATE_ID,
        label="DBS 中文投資人須知",
        parser_version=dbs_iis.PARSER_VERSION,
        detect=dbs_iis.detect,
        read=dbs_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=dbs_iis_rules.run_all,
    ),
)

# DBS DACN（workflow #370）：和 DBS FCN 同一家上手、不同範本（依累積日數定義的「其區間下限價格」辨識，排在 DBS FCN 前面）
DBS_DACN = Template(
    code=dbs_dacn_rules.ISSUER,
    template_id=dbs_dacn_parser.TEMPLATE_ID,
    label="DBS DACN 中文產品說明書",
    parser_version=dbs_dacn_parser.PARSER_VERSION,
    not_covered=tuple(dbs_dacn_rules.NOT_COVERED),
    detect=dbs_dacn_parser.detect,
    read=dbs_dacn_parser.read,
    rules=dbs_dacn_rules.run_all,
    iis=IisTemplate(
        template_id=dbs_dacn_iis.TEMPLATE_ID,
        label="DBS DACN 中文投資人須知",
        parser_version=dbs_dacn_iis.PARSER_VERSION,
        detect=dbs_dacn_iis.detect,
        read=dbs_dacn_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=dbs_dacn_iis_rules.run_all,
    ),
    products=frozenset({DACN}),
)

UBS = Template(
    code=ubs_rules.ISSUER,
    template_id=ubs_parser.TEMPLATE_ID,
    label="UBS 中文產品說明書",
    parser_version=ubs_parser.PARSER_VERSION,
    not_covered=tuple(ubs_rules.NOT_COVERED),
    detect=ubs_parser.detect,
    read=ubs_parser.read,
    rules=ubs_rules.run_all,
    iis=IisTemplate(
        template_id=ubs_iis.TEMPLATE_ID,
        label="UBS 中文投資人須知",
        parser_version=ubs_iis.PARSER_VERSION,
        detect=ubs_iis.detect,
        read=ubs_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=ubs_iis_rules.run_all,
    ),
)


# 上手：範本由專到泛（商品家族在前、FCN 最後）
REGISTRY: tuple[Issuer, ...] = (
    Issuer(BARC.code, (BARC_DACN, BARC_STEPDOWN, BARC), reference_fields=barc_rules.REFERENCE_FIELDS, ratings=True),
    Issuer(HSBC.code, (HSBC_BEN, HSBC_SUPER_BULL, HSBC), ratings=True),
    Issuer(MS.code, (MS_BEN, MS), reference_fields=ms_rules.REFERENCE_FIELDS, ratings=True),
    Issuer(BNP.code, (BNP_SUPER_BULL, BNP), reference_fields=bnp_rules.REFERENCE_FIELDS, ratings=True),
    Issuer(SG.code, (SG_DACN, SG), reference_fields=sg_rules.REFERENCE_FIELDS, ratings=True),
    Issuer(CITI.code, (CITI,), reference_fields=citi_rules.REFERENCE_FIELDS, ratings=True),
    Issuer(JPM.code, (JPM,), reference_fields=jpm_rules.REFERENCE_FIELDS, ratings=True),
    Issuer(NOMURA.code, (NOMURA_DACN, NOMURA), ratings=True),
    Issuer(GS.code, (GS,), reference_fields=gs_rules.REFERENCE_FIELDS, ratings=True),
    Issuer(
        NATIXIS.code,
        (NATIXIS_STEPDOWN, NATIXIS_DACN, NATIXIS),
        reference_fields=natixis_rules.REFERENCE_FIELDS,
        ratings=True,
    ),
    Issuer(
        CA.code,
        (CA_BEN, CA_SUPER_BULL, CA),
        reference_fields=ca_rules.REFERENCE_FIELDS,
        ratings=True,
        with_calendar=ca_parser.with_calendar,  # 只對沒有 Non-Call 的 FCN 有作用；Super Bull、BEN 的 Non-Call ≥ 1，原樣交回
    ),
    Issuer(BBVA.code, (BBVA,), reference_fields=bbva_rules.REFERENCE_FIELDS, ratings=True),
    Issuer(DBS.code, (DBS_DACN, DBS), reference_fields=dbs_rules.REFERENCE_FIELDS, ratings=True),
    Issuer(UBS.code, (UBS,), reference_fields=ubs_rules.REFERENCE_FIELDS, ratings=True),
)


def by_code(code: str, registry: Sequence[Issuer] = REGISTRY) -> Issuer | None:
    return next((i for i in registry if i.code == code), None)


TEMPLATE = Item.note("範本")

_Candidate = tuple[Template, Callable[[Sequence[Line]], DetectionResult], str, str]
_Detection = tuple[Template, DetectionResult, str, str]


def detect(lines: Sequence[Line], registry: Sequence[Issuer] = REGISTRY) -> tuple[Template | None, CheckResult]:
    """辨識說明書範本：跨上手剛好一家命中才回傳該範本，零家或多家命中轉人工覆核；同一家之內由專到泛取第一個命中。"""
    return _detect(lines, [[(t, t.detect, t.template_id, t.label) for t in i.templates] for i in registry], "說明書")


def detect_iis(lines: Sequence[Line], registry: Sequence[Issuer] = REGISTRY) -> tuple[Template | None, CheckResult]:
    """辨識投資人須知範本（回傳同商品的說明書範本，投資人須知範本在它的 `iis`）；規則同 `detect`。"""
    issuers = [[(t, t.iis.detect, t.iis.template_id, t.iis.label) for t in i.templates if t.iis] for i in registry]
    return _detect(lines, issuers, "投資人須知")


def _detect(
    lines: Sequence[Line], issuers: Sequence[Sequence[_Candidate]], what: str
) -> tuple[Template | None, CheckResult]:
    # 每個範本都跑一次；錯訊照登記順序（每家上手 FCN 在前）列出每個範本的結果
    detections = [[(t, d(lines), tid, label) for t, d, tid, label in reversed(cands)] for cands in issuers]
    flat = [x for per_issuer in detections for x in per_issuer]
    hits = [h for per_issuer in detections if (h := _pick(per_issuer)) is not None]
    rid = "template.detect"
    if len(hits) == 1:
        template, det, tid, label = hits[0]
        return template, CheckResult(
            rule_id=rid,
            field="template",
            status=CheckStatus.PASS,
            actual=tid,
            document_evidence=det.evidence,
            message=f"符合 {label} 範本",
            item=TEMPLATE,
        )
    if hits:
        return None, CheckResult(
            rule_id=rid,
            field="template",
            status=CheckStatus.REVIEW_REQUIRED,
            actual=[tid for _, _, tid, _ in hits],
            reason_code="template_ambiguous",
            document_evidence=[e for _, d, _, _ in hits for e in d.evidence],
            message="同時符合多個範本，無法確定上手，請人工處理：" + "、".join(label for _, _, _, label in hits),
            item=TEMPLATE,
        )
    old = [d for _, d, _, _ in flat if d.unsupported]
    if len(old) == 1:  # 認得出上手、但範本版本不支援
        return None, CheckResult(
            rule_id=rid,
            field="template",
            status=CheckStatus.REVIEW_REQUIRED,
            reason_code="template_unsupported",
            document_evidence=old[0].evidence,
            message=f"{old[0].unsupported}；請人工核對，不能人工放行",
            item=TEMPLATE,
        )
    evidence: list[Evidence] = [e for _, d, _, _ in flat for e in d.evidence]
    reasons = "；".join(f"{label}：" + "；".join(d.failed) for _, d, _, label in flat)
    return None, CheckResult(
        rule_id=rid,
        field="template",
        status=CheckStatus.REVIEW_REQUIRED,
        reason_code="template_unknown",
        document_evidence=evidence,
        message=f"不是已支援的{what}範本，請人工處理：" + reasons,
        item=TEMPLATE,
    )


def _pick(detections: Sequence[_Detection]) -> _Detection | None:
    """同一家上手由專到泛取第一個命中的範本（`detections` 是登記順序、FCN 在前，所以倒著看）；較專的範本認得出商品家族、
    但版本不支援（`DetectionResult.unsupported`）或文件有該家族的標記寫法（`DetectionResult.claimed`）時停在那裡，
    不退回較泛的範本。"""
    for hit in reversed(detections):
        if hit[1].matched:
            return hit
        if hit[1].unsupported or hit[1].claimed:
            return None
    return None
