"""發行機構評等（`sp_workflow.credit_ratings`，#237）：第一批評等、依日期查詢、新增與修改最新一筆、格式檢查、留紀錄。

資料庫的部分對真的 PostgreSQL 跑；新增的評等全部虛構。
"""

from __future__ import annotations

import datetime as dt
import re

import pytest

from sp_workflow.credit_ratings import (
    GUARANTOR,
    INITIAL,
    ISSUER,
    CreditRatingError,
    Entity,
    Rating,
    RatingRows,
    add_entity,
    add_rating,
    delete_latest,
    edit_latest,
    effective_label,
    entity_from,
    load_rows,
    normalize_rating,
    rating_from,
    rating_on,
)

D1, D2, D3 = dt.date(2030, 1, 10), dt.date(2030, 6, 1), dt.date(2031, 1, 1)


# ---------- 輸入與查詢（不需資料庫） ----------


@pytest.mark.parametrize(
    ("agency", "text", "expected"),
    [
        ("標準普爾", " a+ ", "A+"),
        ("標準普爾", "BBB-", "BBB-"),
        ("穆迪", "aa2", "Aa2"),
        ("穆迪", "BAA1", "Baa1"),
        ("惠譽", "aa -", "AA-"),
        ("惠譽", "", None),
    ],
)
def test_ratings_are_normalized(agency, text, expected) -> None:
    assert normalize_rating(agency, text) == expected


@pytest.mark.parametrize(("agency", "text"), [("標準普爾", "A1"), ("穆迪", "A+"), ("惠譽", "AAA+"), ("穆迪", "Aa4")])
def test_wrong_rating_formats_are_refused(agency, text) -> None:
    with pytest.raises(CreditRatingError, match=re.escape(f"{agency}評等「{text}」格式不對")):
        normalize_rating(agency, text)


def test_a_rating_needs_at_least_one_agency() -> None:
    with pytest.raises(CreditRatingError, match="至少要填一家"):
        rating_from("BARC", ISSUER, D1, "", " ", "")
    assert rating_from("BNP", ISSUER, D1, "a+", "", "").by_agency() == {"標準普爾": "A+"}


def test_new_entities_are_checked() -> None:
    assert entity_from(" barc ", ISSUER, " Barclays ") == Entity("BARC", ISSUER, "Barclays")
    with pytest.raises(CreditRatingError, match="上手代碼"):
        entity_from("B1", ISSUER, "x")
    with pytest.raises(CreditRatingError, match="名稱"):
        entity_from("ABC", GUARANTOR, " ")


def test_the_rating_on_a_day_is_the_latest_effective_on_or_before_it() -> None:
    first = Rating("ABC", ISSUER, INITIAL, "A", None, None)
    later = Rating("ABC", ISSUER, D2, "A+", None, None)
    rows = RatingRows((Entity("ABC", ISSUER, "Abc"),), (first, later))

    assert rows.on("ABC", ISSUER, dt.date(1990, 1, 1)) == first, "第一批不設起始日"
    assert rows.on("ABC", ISSUER, D2 - dt.timedelta(days=1)) == first
    assert rows.on("ABC", ISSUER, D2) == later, "生效日當天就用新評等"
    assert rows.on("ABC", GUARANTOR, D2) is None
    assert effective_label(INITIAL) == "最初" and effective_label(D2) == "2030-06-01"


# ---------- 資料庫 ----------


def log(pg) -> list[tuple]:
    return pg.execute(
        "SELECT changed_by, action, before, after FROM workflow.credit_rating_log ORDER BY change_id"
    ).fetchall()


def test_first_ratings_cover_every_issuer_from_the_documents(pg) -> None:
    rows = load_rows(pg)

    assert len(rows.entities) == 18 and len(rows.ratings) == 18
    assert {e.issuer for e in rows.entities} == {
        "BARC", "BBVA", "BNP", "CA", "CITI", "DBS", "GS", "HSBC", "JPM", "MS", "NATIXIS", "NOMURA", "SG", "UBS"
    }  # fmt: skip
    assert rows.latest("CITI", GUARANTOR) == Rating("CITI", GUARANTOR, INITIAL, "BBB+", "A3", "A")
    assert rows.latest("UBS", ISSUER).fitch == "AA", "文件寫 AA（Generator 上方評等表誤寫 AA-）"
    assert rows.latest("BNP", ISSUER).by_agency() == {"標準普爾": "A+"}
    assert rows.entities[:3] == (
        Entity("BARC", ISSUER, "Barclays Bank PLC"),
        Entity("BBVA", ISSUER, "BBVA Global Markets B.V."),
        Entity("BBVA", GUARANTOR, "Banco Bilbao Vizcaya Argentaria, S.A."),
    ), "依上手排序、發行機構在前"
    assert rating_on(pg, "JPM", GUARANTOR, dt.date(2001, 1, 1)).moodys == "Aa2", "不設起始日"
    assert rating_on(pg, "JPM", ISSUER, D1) is None


def test_adding_a_rating_takes_effect_from_its_date_and_is_logged(pg) -> None:
    latest = load_rows(pg).latest("SG", GUARANTOR)
    new = Rating("SG", GUARANTOR, D2, "A-", "A2", "A")

    add_rating(pg, latest, new, user="tester")

    assert rating_on(pg, "SG", GUARANTOR, D2 - dt.timedelta(days=1)) == latest
    assert rating_on(pg, "SG", GUARANTOR, D2) == new
    assert log(pg) == [
        (
            "tester",
            "新增",
            None,
            {
                "issuer": "SG",
                "role": "保證機構",
                "effective_date": "2030-06-01",
                "sp": "A-",
                "moodys": "A2",
                "fitch": "A",
            },
        )
    ]


def test_a_new_rating_must_be_later_than_the_latest(pg) -> None:
    first = load_rows(pg).latest("SG", GUARANTOR)
    later = Rating("SG", GUARANTOR, D2, "A-", None, None)
    add_rating(pg, first, later)

    with pytest.raises(CreditRatingError, match="要比最新一筆（2030-06-01）晚"):
        add_rating(pg, later, Rating("SG", GUARANTOR, D2, "A", None, None))
    with pytest.raises(CreditRatingError, match="打開之後被改過"):
        add_rating(pg, first, Rating("SG", GUARANTOR, D3, "A", None, None))
    assert len(load_rows(pg).history("SG", GUARANTOR)) == 2


def test_only_the_latest_rating_can_be_edited_or_deleted(pg) -> None:
    first = load_rows(pg).latest("MS", ISSUER)
    second = Rating("MS", ISSUER, D2, "A", "A1", "AA-")
    add_rating(pg, first, second)

    with pytest.raises(CreditRatingError, match="打開之後被改過"):
        edit_latest(pg, first, Rating("MS", ISSUER, INITIAL, "A", None, None))
    with pytest.raises(CreditRatingError, match="要比前一筆（最初）晚"):
        edit_latest(pg, second, Rating("MS", ISSUER, INITIAL, "A", "A1", "AA-"))

    moved = Rating("MS", ISSUER, D3, "A", "A1", "AA")
    assert edit_latest(pg, second, moved, user="tester") is True
    assert edit_latest(pg, moved, moved) is False, "沒有改不寫"
    assert load_rows(pg).history("MS", ISSUER) == (first, moved)

    delete_latest(pg, moved, user="tester")
    assert load_rows(pg).history("MS", ISSUER) == (first,)
    with pytest.raises(CreditRatingError, match="至少要留一筆"):
        delete_latest(pg, first)
    assert [action for _, action, _, _ in log(pg)] == ["新增", "修改", "刪除"]


def test_initial_ratings_keep_their_date_but_can_be_corrected(pg) -> None:
    first = load_rows(pg).latest("NATIXIS", ISSUER)

    with pytest.raises(CreditRatingError, match="最初）的生效日不能改"):
        edit_latest(pg, first, Rating("NATIXIS", ISSUER, D1, "A+", "A2", "A+"))
    edit_latest(pg, first, Rating("NATIXIS", ISSUER, INITIAL, "A", "A2", "A+"))

    assert rating_on(pg, "NATIXIS", ISSUER, D1).sp == "A"


def test_a_new_issuer_gets_an_entity_and_its_first_rating(pg) -> None:
    entity = Entity("XYZ", GUARANTOR, "Xyz Holdings")
    first = Rating("XYZ", GUARANTOR, INITIAL, None, "Baa2", None)

    add_entity(pg, entity, first, user="tester")

    rows = load_rows(pg)
    assert entity in rows.entities and rows.latest("XYZ", GUARANTOR) == first
    with pytest.raises(CreditRatingError, match="XYZ 的保證機構已經有了"):
        add_entity(pg, entity, first)
    with pytest.raises(CreditRatingError, match="評等表沒有 XYZ 的發行機構"):
        add_rating(pg, None, Rating("XYZ", ISSUER, D1, "A", None, None))


def test_the_database_refuses_bad_formats_too(pg) -> None:
    latest = load_rows(pg).latest("DBS", ISSUER)

    with pytest.raises(CreditRatingError, match="穆迪評等格式不對"):
        add_rating(pg, latest, Rating("DBS", ISSUER, D1, None, "AA", None))
    with pytest.raises(CreditRatingError, match="至少要填一家"):
        add_rating(pg, latest, Rating("DBS", ISSUER, D1, None, None, None))
    assert log(pg) == []


def test_the_log_is_insert_only(pg) -> None:
    import psycopg

    add_rating(pg, load_rows(pg).latest("UBS", ISSUER), Rating("UBS", ISSUER, D1, "A", None, None))
    with pytest.raises(psycopg.errors.RaiseException, match="只新增"):
        pg.execute("DELETE FROM workflow.credit_rating_log")
