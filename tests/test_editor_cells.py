"""編輯區格子的規則（#127、ADR 0009、0017、0018）：顏色、說明與打的字怎麼解讀，不開 Tk。資料全部虛構。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from itertools import product

import pytest

from order_synth import drafts_of, maintenance_draft, maintenance_trades, order_row
from sp_workflow.editor_cells import (
    CellState,
    Tone,
    cell_states,
    commit_order,
    commit_trade_rows,
    order_cell_states,
    order_texts,
    trade_cell_states,
    trade_texts,
)
from sp_workflow.trade_rows import CANCEL_NOTE, UNMATCHED_NOTE

# ---------- 格子狀態：擋存檔 ＞ 和資料庫不同＋備註 ＞ 和資料庫不同 ＞ 備註 ＞ 固定 ----------


def one_cell(*, problem: bool, different: bool, note: bool, fixed: bool, stored: object = 12) -> CellState:
    key = "Coupon p.a. (%)"
    return cell_states(
        [key],
        problems={key: "必填"} if problem else {},
        notes={key: "讀到「abc」"} if note else {},
        differences={key: stored} if different else {},
        fixed={key} if fixed else set(),
        fixed_note="不能改",
    )[key]


@pytest.mark.parametrize(("different", "note", "fixed"), list(product([False, True], repeat=3)))
def test_a_problem_wins_over_everything(different: bool, note: bool, fixed: bool) -> None:
    state = one_cell(problem=True, different=different, note=note, fixed=fixed)

    assert state == CellState(Tone.PROBLEM, "必填")


@pytest.mark.parametrize("fixed", [False, True])
def test_a_difference_with_a_note_stays_red_and_says_both(fixed: bool) -> None:
    state = one_cell(problem=False, different=True, note=True, fixed=fixed)

    assert state == CellState(Tone.PROBLEM, "讀到「abc」；資料庫是 12", offer_database=True)


@pytest.mark.parametrize("fixed", [False, True])
def test_a_difference_alone_is_yellow_and_offers_the_database_value(fixed: bool) -> None:
    state = one_cell(problem=False, different=True, note=False, fixed=fixed)

    assert state == CellState(Tone.DIFFERENT, "資料庫是 12", offer_database=True)


def test_a_blank_database_value_is_called_blank() -> None:
    state = one_cell(problem=False, different=True, note=False, fixed=False, stored=None)

    assert state.note == "資料庫是 空白"


@pytest.mark.parametrize("fixed", [False, True])
def test_a_note_alone_is_red(fixed: bool) -> None:
    assert one_cell(problem=False, different=False, note=True, fixed=fixed) == CellState(Tone.PROBLEM, "讀到「abc」")


@pytest.mark.parametrize(("fixed", "note"), [(False, ""), (True, "不能改")])
def test_an_untouched_cell_is_plain_and_a_fixed_one_says_so(fixed: bool, note: str) -> None:
    assert one_cell(problem=False, different=False, note=False, fixed=fixed) == CellState(Tone.PLAIN, note)


# ---------- 起單資料／人工維護的商品條件 ----------


def test_order_cells_prefix_reading_notes_and_use_the_given_problems() -> None:
    notes = {"Coupon p.a. (%)": "讀到「abc」，不是數字"}
    [draft] = drafts_of([order_row(notes=notes)])
    draft.stored = {**draft.values, "Coupon p.a. (%)": 12}

    states = order_cell_states(draft, {"Maturity date": "必填"})

    assert states["Coupon p.a. (%)"] == CellState(
        Tone.PROBLEM, "起單建檔：讀到「abc」，不是數字；資料庫是 12", offer_database=True
    )
    assert states["Maturity date"] == CellState(Tone.PROBLEM, "必填")
    assert states["Currency"] == CellState()


@pytest.mark.parametrize(("correcting", "tone"), [(True, Tone.DIFFERENT), (False, Tone.PLAIN)])
def test_clearing_a_field_only_counts_as_different_when_correcting(correcting: bool, tone: Tone) -> None:
    draft = maintenance_draft()
    draft.correcting = correcting
    draft.values["Coupon p.a. (%)"] = None

    assert order_cell_states(draft, {})["Coupon p.a. (%)"].tone is tone


def test_fixed_order_cells_say_why_they_cannot_change() -> None:
    states = order_cell_states(maintenance_draft(), {}, fixed={"Issue date"}, fixed_note="不能更正")

    assert states["Issue date"] == CellState(Tone.PLAIN, "不能更正")


# ---------- 交易明細 ----------


def test_trade_cells_mark_edits_problems_and_unmatched_names() -> None:
    trades = maintenance_trades({"金額": Decimal(120000), "分公司": None, "FC/PB": "Zed"})

    states = trade_cell_states(trades)

    assert states[(1, "金額")] == CellState(Tone.DIFFERENT, "資料庫是 100000", offer_database=True)
    assert states[(1, "分公司")] == CellState(Tone.PROBLEM, "必填")
    assert states[(1, "FC/PB")] == CellState(Tone.PROBLEM, f"{UNMATCHED_NOTE}；資料庫是 Amy", offer_database=True)
    assert states[(1, "幣別")] == CellState()


# ---------- 商品條件：打的字解讀進 draft ----------


def test_typing_a_value_sets_it_and_counts_as_a_change() -> None:
    draft, typed = maintenance_draft(), {}

    changed = commit_order(draft, typed, {**order_texts(draft, typed), "Coupon p.a. (%)": "13"})

    assert changed and draft.values["Coupon p.a. (%)"] == Decimal(13)
    assert order_texts(draft, typed)["Coupon p.a. (%)"] == "13"


def test_committing_the_shown_texts_changes_nothing() -> None:
    draft, typed = maintenance_draft(), {}

    assert not commit_order(draft, typed, order_texts(draft, typed))


def test_a_mistyped_value_keeps_the_text_and_records_the_error_once() -> None:
    draft, typed = maintenance_draft(), {}
    texts = {**order_texts(draft, typed), "Trade date": "10/15"}

    assert commit_order(draft, typed, texts)
    assert typed == {"Trade date": "10/15"} and draft.input_errors == {"Trade date": "日期要寫成 2026-10-21"}
    assert draft.values["Trade date"] == dt.date(2026, 10, 15), "值不動"
    assert order_texts(draft, typed)["Trade date"] == "10/15"
    assert not commit_order(draft, typed, texts), "同樣的錯字再解讀一次不算改到"


def test_fixing_a_mistyped_value_counts_as_a_change_even_back_to_the_same_value() -> None:
    draft, typed = maintenance_draft(), {}
    commit_order(draft, typed, {**order_texts(draft, typed), "Trade date": "10/15"})

    assert commit_order(draft, typed, {**order_texts(draft, typed), "Trade date": "2026-10-15"})
    assert typed == {} and draft.input_errors == {}


def test_an_equal_strike_date_follows_the_trade_date() -> None:
    draft, typed = maintenance_draft(), {}
    assert draft.values["Strike date"] == draft.values["Trade date"]

    commit_order(draft, typed, {**order_texts(draft, typed), "Trade date": "2026-10-16"})

    assert draft.values["Strike date"] == dt.date(2026, 10, 16)
    assert order_texts(draft, typed)["Strike date"] == "2026-10-16"


def test_a_typed_strike_date_is_kept_when_the_trade_date_changes_too() -> None:
    draft, typed = maintenance_draft(), {}
    texts = {**order_texts(draft, typed), "Trade date": "2026-10-16", "Strike date": "2026-10-20"}

    commit_order(draft, typed, texts)

    assert draft.values["Trade date"] == dt.date(2026, 10, 16)
    assert draft.values["Strike date"] == dt.date(2026, 10, 20)


def test_a_mistyped_strike_date_does_not_follow_the_trade_date() -> None:
    draft, typed = maintenance_draft(), {}
    commit_order(draft, typed, {**order_texts(draft, typed), "Strike date": "abc"})

    commit_order(draft, typed, {**order_texts(draft, typed), "Trade date": "2026-10-16"})

    assert draft.values["Strike date"] == dt.date(2026, 10, 15) and typed == {"Strike date": "abc"}


def test_committing_one_field_leaves_a_cleared_strike_date_alone() -> None:
    draft, typed = maintenance_draft(), {}
    commit_order(draft, typed, {**order_texts(draft, typed), "Strike date": ""}, ["Strike date"])

    changed = commit_order(draft, typed, {**order_texts(draft, typed), "Coupon p.a. (%)": "13"}, ["Coupon p.a. (%)"])

    assert changed and draft.values["Strike date"] is None


def test_fields_left_out_of_the_texts_are_not_touched() -> None:
    draft, typed = maintenance_draft(), {}

    assert not commit_order(draft, typed, {"Coupon p.a. (%)": order_texts(draft, typed)["Coupon p.a. (%)"]})
    assert draft.input_errors == {}


# ---------- 交易明細：打的字解讀進 draft ----------


def test_typing_a_trade_value_uses_the_daily_import_reading() -> None:
    trades, typed = maintenance_trades({}), {}

    changed = commit_trade_rows(trades, typed, {**trade_texts(trades, typed), (1, "金額"): "120,000"})

    assert changed and trades.rows[0].values["金額"] == Decimal(120000)
    assert trade_texts(trades, typed)[(1, "金額")] == "120000"
    assert not commit_trade_rows(trades, typed, trade_texts(trades, typed))


def test_a_mistyped_trade_value_keeps_the_text_and_the_old_value() -> None:
    trades, typed = maintenance_trades({}), {}
    texts = {**trade_texts(trades, typed), (1, "UF"): "abc"}

    assert commit_trade_rows(trades, typed, texts)
    assert typed == {(1, "UF"): "abc"} and trades.rows[0].input_errors == {"UF": "「abc」不是數字"}
    assert trades.rows[0].values["UF"] == Decimal("0.021500")
    assert not commit_trade_rows(trades, typed, texts)

    assert commit_trade_rows(trades, typed, {**texts, (1, "UF"): "0.0215"})
    assert typed == {} and trades.rows[0].input_errors == {}


def test_trade_problems_come_from_the_trade_rules() -> None:
    trades, typed = maintenance_trades({}), {}

    commit_trade_rows(trades, typed, {**trade_texts(trades, typed), (1, "備註"): "Cancel"})

    assert trade_cell_states(trades)[(1, "備註")] == CellState(Tone.PROBLEM, CANCEL_NOTE)
