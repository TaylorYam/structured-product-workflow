"""欄位字彙（#433）：標的價格欄的唯一宣告 `fcn_checker.fields` 與參考條件表格式的額外欄 `[extra_columns]`。

測試切點：`fields.PRICE_FIELDS`、`config.load_reference_format`。所有值皆為設定檔本身，沒有真實資料。
"""

from __future__ import annotations

import pytest

from fcn_checker.config import load_reference_format
from fcn_checker.fields import PRICE_COLUMNS, PRICE_FIELDS, PRICE_SQL
from fcn_checker.ingestion import IngestionError
from fcn_checker.orders.reference import UNDERLYING_SLOTS, price_key
from harness import REFERENCE_FORMAT


def test_the_reference_sheet_headers_map_to_the_price_vocabulary():
    fmt = load_reference_format(REFERENCE_FORMAT)
    for i in range(1, UNDERLYING_SLOTS + 1):
        for pf in PRICE_FIELDS:
            assert fmt.columns[f"UL_{i}_{pf.header}"] == price_key(i, pf.key) == f"underlying_{i}_{pf.column}"


def test_price_columns_follow_the_vocabulary_order():
    assert list(PRICE_COLUMNS) == ["initial", "strike", "ki", "ko"]
    assert PRICE_SQL == "initial_price, strike_price, ki_price, ko_price"
    assert [pf.label for pf in PRICE_FIELDS] == ["進場價", "執行價", "下限價", "KO 價"]


def test_extra_columns_come_from_the_settings_file():
    fmt = load_reference_format(REFERENCE_FORMAT)
    extra = fmt.extra
    assert (extra.upfront, extra.trade_date, extra.inventory_status) == ("UF", "交易日期", "庫存狀態")
    assert extra.branch_columns() == tuple((f"分公司_{n}", f"分公司_{n}_承作金額") for n in range(1, 8))
    written = {extra.upfront, extra.trade_date, *(name for pair in extra.branch_columns() for name in pair)}
    assert written <= set(fmt.import_format.columns)  # 資料庫來源寫的額外欄都在匯入格式裡
    assert not ({extra.upfront, extra.trade_date, extra.inventory_status} & set(fmt.columns))  # 不是標準欄位


@pytest.mark.parametrize(
    "old,new,named",
    [
        ('branch = "分公司_{n}"', 'branch = "分公司"', "branch"),  # 沒有 {n}
        ('branch_amount = "分公司_{n}_承作金額"', 'branch_amount = "分公司_{n}_{x}"', "branch_amount"),  # 其他 {}
        ('upfront = "UF"', 'upfront = ""', "upfront"),
        ("branches = 7", "branches = 0", "branches"),
    ],
)
def test_bad_extra_columns_are_a_config_problem(tmp_path, old, new, named):
    text = REFERENCE_FORMAT.read_text(encoding="utf-8")
    assert old in text
    bad = tmp_path / "reference_sheet.toml"
    bad.write_text(text.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(IngestionError) as raised:
        load_reference_format(bad)
    assert raised.value.reason_code == "config_invalid" and named in str(raised.value)


def test_missing_extra_columns_table_is_a_config_problem(tmp_path):
    text = REFERENCE_FORMAT.read_text(encoding="utf-8")
    start, end = text.index("[extra_columns]"), text.index("[issuer_values]")
    bad = tmp_path / "reference_sheet.toml"
    bad.write_text(text[:start] + text[end:], encoding="utf-8")
    with pytest.raises(IngestionError) as raised:
        load_reference_format(bad)
    assert raised.value.reason_code == "config_invalid" and "extra_columns" in str(raised.value)
