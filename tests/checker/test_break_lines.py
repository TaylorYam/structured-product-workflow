"""中文依寬度斷行（`fcn_checker.ui.break_lines`、`WrapLabel`，#255）：Tk 的 wraplength 只在空白處斷行。"""

from __future__ import annotations

import tkinter as tk
import unicodedata

import pytest

from fcn_checker.ui import WrapLabel, WrapTkLabel, apply_style, break_lines


def measure(text: str) -> int:
    """假的字寬：中文、全形 2，其他 1。"""
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text)


def lines(text: str, width: int) -> list[str]:
    return break_lines(text, width, measure).split("\n")


SENTENCE = "貼上 TDCC，選一份起單建檔，按「讀取」。只讀起單建檔，不會修改它。"


def test_mixed_text_fills_each_line_instead_of_breaking_at_the_first_space() -> None:
    assert lines(SENTENCE, 30) == ["貼上 TDCC，選一份起單建檔，按", "「讀取」。只讀起單建檔，不會修", "改它。"]
    assert all(measure(line) <= 30 for line in lines(SENTENCE, 30))


@pytest.mark.parametrize("width", range(12, 60))
def test_punctuation_stays_with_its_neighbour(width: int) -> None:
    result = lines(SENTENCE, width)

    assert "".join(result).replace(" ", "") == SENTENCE.replace(" ", ""), "字都在、順序不變"
    assert not any(line[0] in "，。、」）" for line in result), "句號、逗號、右括號不在行首"
    assert not any(line[-1] in "「（" for line in result), "左括號不在行尾"


def test_words_and_numbers_are_not_split() -> None:
    assert lines("共 029199990001 檔已匯入 hello world", 16) == ["共 029199990001", "檔已匯入 hello", "world"]


def test_a_word_wider_than_the_line_is_split_by_character() -> None:
    assert lines("Supercalifragilistic", 8) == ["Supercal", "ifragili", "stic"]


def test_existing_line_breaks_and_blank_lines_are_kept() -> None:
    assert lines("第一行\n\n第二段", 40) == ["第一行", "", "第二段"]


def test_no_width_means_no_wrapping() -> None:
    assert break_lines(SENTENCE, 0, measure) == SENTENCE


@pytest.fixture
def styled(master):
    apply_style(master)
    return master


def test_wrap_label_follows_its_variable_and_width(styled) -> None:
    var = tk.StringVar(styled, value=SENTENCE)
    label = WrapLabel(styled, textvariable=var, wraplength=10_000)

    assert str(label.tk.call(label, "cget", "-text")) == SENTENCE, "夠寬時不斷行"
    label.configure(wraplength=120)
    shown = str(label.tk.call(label, "cget", "-text"))
    assert "\n" in shown and shown.replace("\n", "").replace(" ", "") == SENTENCE.replace(" ", "")
    assert label.cget("text") == SENTENCE and label.cget("wraplength") == 120, "cget 回傳原文與設定的寬度"
    assert label["textvariable"] == str(var)
    assert int(label.tk.call(label, "cget", "-wraplength")) == 0, "不交給 Tk 斷行"

    var.set("完成")
    assert str(label.tk.call(label, "cget", "-text")) == "完成", "變數改了就重新斷行"

    label.destroy()
    var.set(SENTENCE)  # 標籤關掉後改變數不會出錯


def test_wrap_label_rewraps_when_its_font_changes(styled) -> None:
    label = WrapLabel(styled, text=SENTENCE * 3, wraplength=300)
    normal = str(label.tk.call(label, "cget", "-text")).count("\n")

    label.configure(style="Title.TLabel")  # 字變大

    assert str(label.tk.call(label, "cget", "-text")).count("\n") > normal


def test_tk_label_version_wraps_text_set_later(styled) -> None:
    label = WrapTkLabel(styled, font=("Microsoft JhengHei UI", 10))
    label.configure(text=SENTENCE, wraplength=120)

    assert "\n" in str(label.tk.call(label, "cget", "-text"))
    assert label.cget("text") == SENTENCE
