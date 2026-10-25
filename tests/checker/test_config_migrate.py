"""審查標準每家上手一張表（Issue #128）：舊版面讀到時停止並提示轉換指令、一次性轉換保留註解、轉換後照常寫入審查通過日期。"""

from __future__ import annotations

import datetime as dt
import tomllib
from pathlib import Path

import pytest

from fcn_checker import approval_dates
from fcn_checker.config import MIGRATE_COMMAND, load_review_standard
from fcn_checker.config_migrate import convert, main
from fcn_checker.ingestion import IngestionError
from harness import REVIEW_STANDARD

LEGACY = """\
# 審查標準（測試用舊版面）
version = 3
effective_date = 2026-01-02

[distributor]
approval_dates = [2025-12-18]
chairman = "林晋輝"
name = "玉山綜合證券股份有限公司"
phone = "02-5556-1313"
address = "台北市松山區民生東路三段158號6樓"
established = 2000-11-20

[risk]
level = "RR4"
fixed_warning = "共用警語。後半段"
fixed_warning_occurrences = 3

# 說明書固定警語次數的上手別設定
# AAA：只有 2 次
[risk.fixed_warning_occurrences_by_issuer]
aaa = 2

# 依上手不同的固定警語版本
[risk.fixed_warning_by_issuer]
aaa = "甲警語。後半段"
# BBB 的版本（多行字串）
bbb = \"\"\"\\
BBB 警語。\\
後半段\"\"\"

[risk.fixed_warning_openings]
bbb = ["BBB 另一個開頭。"]

[risk.level_formats]
bbb = ["歸類為{level}"]

[issuer_name]
# 發行機構全名
aaa = "甲銀行"
# BBB：只有英文名
bbb = "BBB Bank"

[issuer_name_ignore]
bbb = ["brackets"]

[fees]
"申購費用" = "0%~5%"

[issue_price]
pct = 100

[principal_protection]
non_protected = ["不保本"]

[wording]
forbidden = ["受託投資"]
allowed_phrases = [
    "受託投資或受託買賣",
    # 陣列裡的註解
    "受託投資或銷售時",
]

# 商品名稱樣板
[product_name.aaa]
zh = "甲{tenor}個月"
en = "A {tenor} Months"
memory_zh = ""
memory_en = ""

# BBB 的名稱樣板
[product_name.bbb]
zh = "乙{tenor}"
en = "B {tenor}"
memory_zh = ""
memory_en = ""
ccy_aliases = { CNH = "CNY" }

[guarantor_name.bbb]
cover = "保證機構"

[min_amount_multiples.bbb]
minimum_subscription = { USD = 5, JPY = 1 }

[product_type]
bbb = "股權連結結構型商品"

[currency]
"美元" = "USD"

[denomination]
USD = 10000

[iis]
pages = 4

[iis.fixed_warning_occurrences]
aaa = 1  # 警語 1.
bbb = 2
"""


def legacy_file(tmp_path: Path, text: str = LEGACY) -> Path:
    path = tmp_path / "review_standard.toml"
    path.write_bytes(text.encode("utf-8"))
    return path


def test_old_layout_is_refused_with_the_migrate_command(tmp_path):
    path = legacy_file(tmp_path)
    with pytest.raises(IngestionError) as raised:
        load_review_standard(path)
    err = raised.value
    assert err.reason_code == "config_outdated"
    assert MIGRATE_COMMAND in str(err) and str(path) in str(err) and "[issuer.<上手>]" in str(err)


def test_each_issuer_gets_one_table_with_the_same_values():
    new = tomllib.loads(convert(LEGACY))
    old = tomllib.loads(LEGACY)
    assert new["issuer"] == {
        "aaa": {
            "issuer_name": "甲銀行",
            "fixed_warning": "甲警語。後半段",
            "fixed_warning_occurrences": 2,
            "iis_fixed_warning_occurrences": 1,
            "product_name": old["product_name"]["aaa"],
        },
        "bbb": {
            "issuer_name": "BBB Bank",
            "issuer_name_ignore": ["brackets"],
            "fixed_warning": "BBB 警語。後半段",
            "fixed_warning_openings": ["BBB 另一個開頭。"],
            "risk_level_formats": ["歸類為{level}"],
            "iis_fixed_warning_occurrences": 2,
            "product_type": "股權連結結構型商品",
            "product_name": old["product_name"]["bbb"],
            "guarantor_name": {"cover": "保證機構"},
            "min_amount_multiples": {"minimum_subscription": {"USD": 5, "JPY": 1}},
        },
    }
    shared = {k: v for k, v in new.items() if k != "issuer"}
    assert shared["risk"] == {"level": "RR4", "fixed_warning": "共用警語。後半段", "fixed_warning_occurrences": 3}
    assert shared["iis"] == {"pages": 4}
    assert set(shared) == {"version", "effective_date", "distributor", "risk", "fees", "issue_price", "wording"} | {
        "principal_protection",
        "currency",
        "denomination",
        "iis",
    }


def test_comments_stay_with_their_issuer_or_become_field_notes():
    text = convert(LEGACY)
    issuers = text.index("[issuer.aaa]")
    notes = text[:issuers]
    # 舊表開頭的註解（說明這種基準）收在上手表前的欄位說明
    for note in ("# 說明書固定警語次數的上手別設定", "# 發行機構全名", "# 商品名稱樣板", "# 依上手不同的固定警語版本"):
        assert note in notes
    # 每個上手的註解跟著它的值；陣列裡與行尾的註解原樣保留
    bbb = text[text.index("[issuer.bbb]") :]
    assert bbb.index("# BBB：只有英文名") < bbb.index('issuer_name = "BBB Bank"')
    assert bbb.index("# BBB 的版本（多行字串）") < bbb.index('fixed_warning = """')
    assert bbb.index("# BBB 的名稱樣板") < bbb.index("[issuer.bbb.product_name]")
    assert "iis_fixed_warning_occurrences = 1  # 警語 1." in text
    assert "    # 陣列裡的註解\n" in text


def test_migrated_file_loads_to_the_per_issuer_standard(tmp_path):
    path = legacy_file(tmp_path)
    assert main([str(path)]) == 0
    std = load_review_standard(path)
    aaa, bbb, other = std.for_issuer("AAA"), std.for_issuer("BBB"), std.for_issuer("CCC")
    assert (aaa.fixed_warning, aaa.fixed_warning_occurrences, aaa.issuer_name) == ("甲警語。後半段", 2, "甲銀行")
    assert bbb.fixed_warnings == ("BBB 警語。後半段", "BBB 另一個開頭。後半段")
    assert bbb.risk_level_formats == ("歸類為{level}",) and bbb.issuer_name_ignore == {"brackets"}
    assert bbb.product_name is not None and bbb.product_name.ccy_text("CNH") == "CNY"
    assert (bbb.guarantor_names, bbb.product_type, bbb.iis_fixed_warning_occurrences) == (
        {"cover": "保證機構"},
        "股權連結結構型商品",
        2,
    )
    assert (other.fixed_warning, other.fixed_warning_occurrences) == ("共用警語。後半段", 3)
    assert (other.issuer_name, other.product_name, other.iis_fixed_warning_occurrences) == (None, None, None)


def test_migration_keeps_the_original_as_backup_and_runs_once(tmp_path, capsys):
    path = legacy_file(tmp_path, LEGACY.replace("\n", "\r\n"))
    assert main([str(path)]) == 0
    assert path.with_name("review_standard.toml.bak").read_bytes() == LEGACY.replace("\n", "\r\n").encode("utf-8")
    assert b"\r\n" in path.read_bytes() and b"\n" not in path.read_bytes().replace(b"\r\n", b"")
    assert main([str(path)]) == 2
    assert "已經是每家上手一張表的新版面" in capsys.readouterr().err


def test_approval_dates_are_written_on_the_new_layout(tmp_path):
    """視窗「維護 → 審查通過日期」寫回轉換後的設定檔：只動日期、version、effective_date，上手表不變。"""
    path = legacy_file(tmp_path)
    assert main([str(path)]) == 0
    before = tomllib.loads(path.read_text(encoding="utf-8"))
    assert approval_dates.write(path, (dt.date(2025, 12, 18), dt.date(2026, 6, 11)), today=dt.date(2026, 6, 12)) == 4
    after = tomllib.loads(path.read_text(encoding="utf-8"))
    assert after["distributor"]["approval_dates"] == [dt.date(2025, 12, 18), dt.date(2026, 6, 11)]
    assert (after["version"], after["effective_date"]) == (4, dt.date(2026, 6, 12))
    assert after["issuer"] == before["issuer"]


def test_repo_standard_is_on_the_new_layout():
    assert "issuer" in tomllib.loads(REVIEW_STANDARD.read_text(encoding="utf-8"))
    load_review_standard(REVIEW_STANDARD)


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ('[issuer.bbb]\nissuer_nmae = "x"\n', "issuer.bbb 有未知的欄位：issuer_nmae"),
        ("[issuer.bbb]\niis_fixed_warning_occurrences = 0\n", "issuer.bbb.iis_fixed_warning_occurrences 須是正整數"),
        ('[issuer.bbb]\nissuer_name = "x"\n[issuer.BBB]\nproduct_type = "y"\n', "issuer.BBB 重複"),
    ],
)
def test_issuer_tables_are_checked_when_loaded(tmp_path, table, message):
    path = legacy_file(tmp_path)
    assert main([str(path)]) == 0
    text = path.read_text(encoding="utf-8")
    start = text.index("[issuer.aaa]")
    path.write_text(text[:start] + table, encoding="utf-8")
    with pytest.raises(IngestionError) as raised:
        load_review_standard(path)
    assert raised.value.reason_code == "config_invalid" and message in str(raised.value)
