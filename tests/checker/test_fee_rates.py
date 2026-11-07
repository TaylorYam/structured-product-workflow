"""測試切點：費用表讀法 `common.fee_rates`／`fee_rates_by_pattern`（Issue #486 前各家一份，收成一份後各家只宣告
`FeeTable` 的座標差異）。手刻幾行文字與座標，不讀 PDF；各家實際版面由 `test_check_*` 的合成說明書覆蓋。
"""

from __future__ import annotations

from fcn_checker.parsers.common import (
    FEE_CHECKED,
    FEE_ROWS,
    EachRate,
    FeeTable,
    FixedColumns,
    PrefixedRate,
    SamePage,
    fee_rates,
    fee_rates_by_pattern,
    nearest_column_grid,
)
from fcn_checker.schema import FieldStatus, Line


def cell(x: float, y: float, text: str, page: int = 1) -> Line:
    return Line(page, x, y, x + 12 * len(text), y + 10, text)


def table(rate_header: str = "費率（百分比）", rates: dict[str, str] | None = None) -> list[Line]:
    """表頭一列＋每個費用項目一列：左欄 x 40、費率欄 x 150、收取時點欄 x 300。"""
    rates = rates or {
        "申購費用": "申購價金的0%~3%",
        "提前贖回費用": "投資人提前贖回價金的0%~5%",
        "分銷費用": "申購價金的0%~2%",
    }
    out = [cell(40, 100, "費用項目"), cell(160, 100, rate_header), cell(300, 100, "收取時點")]
    for k, label in enumerate(("申購費用", "提前贖回費用", "管理費用", "分銷費用")):
        y = 130 + 30 * k
        out += [cell(40, y, label), cell(150, y, rates.get(label, "無")), cell(300, y, "申購時")]
    return out


def test_shared_table_reads_one_rate_per_fee_with_the_row_as_evidence():
    fields = fee_rates(table())
    assert {k: f.value for k, f in fields.items()} == {
        "fee_申購費用": "0%~3%",
        "fee_提前贖回費用": "0%~5%",
        "fee_分銷費用": "0%~2%",
    }
    assert [e.text for e in fields["fee_申購費用"].evidence] == ["申購費用", "申購價金的0%~3%"]


def test_shared_table_wants_exactly_one_rate_in_the_row():
    fields = fee_rates(table(rates={"申購費用": "0%~3%或0%~4%"}))
    assert fields["fee_申購費用"].status == FieldStatus.INVALID
    assert fields["fee_申購費用"].note == "費用表「申購費用」的費率欄沒有剛好一個「X%~Y%」"


def test_without_the_rate_header_every_fee_is_missing():
    lines = [ln for ln in table() if not ln.text.startswith("費率")]
    fields = fee_rates(lines)
    assert {f.status for f in fields.values()} == {FieldStatus.MISSING}
    assert {f.note for f in fields.values()} == {"找不到費用表表頭「費率（百分比）」"}


NOMURA_LIKE = FeeTable(
    grid=nearest_column_grid,
    rate=PrefixedRate({"申購費用": "申購價金的", "提前贖回費用": "投資人提前贖回價金的", "分銷費用": "申購價金的"}),
    rows={label: label for label in FEE_ROWS},
    fees=FEE_CHECKED,
)


def test_prefixed_rate_requires_the_whole_cell_to_be_prefix_plus_range():
    fields = fee_rates(
        table("費率(百分比)", {"申購費用": "申購價金的0%~3%", "分銷費用": "贖回價金的0%~2%"}), NOMURA_LIKE
    )
    assert fields["fee_申購費用"].value == "0%~3%"
    assert fields["fee_分銷費用"].status == FieldStatus.INVALID
    assert fields["fee_分銷費用"].note == "費用表「分銷費用」的費率不是「申購價金的X%~Y%」"


FIXED = FeeTable(
    grid=FixedColumns(edge=100, lo=140, hi=230),
    band=SamePage(above=1, below=1, tail=100),
    rate=EachRate(),
    rows={label: label for label in FEE_ROWS},
    fees=FEE_CHECKED,
    duplicate="費用列錨點缺漏／重複",
)


def test_each_rate_accepts_repeats_of_the_same_range_and_flags_different_ones():
    fields = fee_rates(table(rates={"申購費用": "0%~3%，最高0%~3%", "分銷費用": "0%~2%或0%~1%"}), FIXED)
    assert fields["fee_申購費用"].value == "0%~3%"
    assert fields["fee_分銷費用"].status == FieldStatus.AMBIGUOUS
    assert fields["fee_提前贖回費用"].status == FieldStatus.MISSING  # 預設「無」：沒有區間


def test_duplicate_rows_are_missing_unless_the_table_takes_the_last():
    lines = [*table(), cell(40, 260, "申購費用"), cell(150, 260, "申購價金的1%~2%")]
    assert fee_rates(lines, FIXED)["fee_申購費用"].note == "費用列錨點缺漏／重複"
    last = FeeTable(
        grid=FIXED.grid,
        band=FIXED.band,
        rate=FIXED.rate,
        rows=FIXED.rows,
        fees=FEE_ROWS,
        unlisted="費用表找不到「{fee}」",
        last=True,
    )
    fields = fee_rates(lines, last)
    assert fields["fee_申購費用"].value == "1%~2%"
    assert fields["fee_其他費用"].note == "費用表找不到「其他費用」"


PATTERNS = {"申購費用": r"申購費用申購價金的(\d+%~\d+%)", "分銷費用": r"分銷費用申購價金的(\d+%~\d+%)"}


def test_pattern_table_cites_the_whole_sentence_or_only_the_rate():
    lines = [cell(40, 100, "申購費用"), cell(40, 112, "申購價金的0%~3%"), cell(40, 124, "分銷費用申購價金的0%~2%")]
    whole = fee_rates_by_pattern(lines, PATTERNS)
    assert whole["fee_申購費用"].value == "0%~3%"
    assert [e.text for e in whole["fee_申購費用"].evidence] == ["申購費用", "申購價金的0%~3%"]
    value_only = fee_rates_by_pattern(lines, PATTERNS, whole_match=False)
    assert [e.text for e in value_only["fee_申購費用"].evidence] == ["申購價金的0%~3%"]


def test_pattern_table_without_its_section_is_missing_with_the_given_reason():
    fields = fee_rates_by_pattern(None, PATTERNS, no_table="找不到「四、費用」段落")
    assert {f.note for f in fields.values()} == {"找不到「四、費用」段落"}
    assert list(fields) == ["fee_申購費用", "fee_分銷費用"]
