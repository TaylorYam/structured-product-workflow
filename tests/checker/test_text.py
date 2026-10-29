"""文字工具：找最接近的段落（固定警語的「應改為」用，Issue #339）。"""

from __future__ import annotations

from fcn_checker.text import nearest

WARNING = "本商品之風險等級為RR4，投資人應詳閱商品說明書並審慎評估自身風險承受度，本商品不受存款保險之保障。"
FILLER = "第一章商品概要。"


def test_nearest_finds_the_altered_passage():
    altered = WARNING.replace("RR4", "RR3")
    text = FILLER * 20 + altered + FILLER * 20
    near = nearest(text, WARNING)
    assert near is not None and text[near.start : near.end] == altered


def test_nearest_skips_passages_that_already_match():
    altered = WARNING.replace("審慎", "謹慎")
    text = FILLER * 5 + WARNING + FILLER * 5 + altered + FILLER * 5
    exact = text.index(WARNING)
    near = nearest(text, WARNING, skip=[(exact, exact + len(WARNING))])
    assert near is not None and text[near.start : near.end] == altered
    assert nearest(text, WARNING, skip=[(exact, exact + len(WARNING)), (near.start, near.end)]) is None


def test_nearest_skip_covers_the_padded_ends():
    """頭尾被改的字往外補後才和已相符的段落重疊時也不算（review #342）。"""
    text = WARNING + "甲" + WARNING.replace("本商品之", "乙乙乙乙")
    first = (0, len(WARNING))
    near = nearest(text, WARNING, skip=[first])
    assert near is None or near.start >= first[1]


def test_nearest_returns_none_without_a_similar_passage():
    assert nearest(FILLER * 50, WARNING) is None
