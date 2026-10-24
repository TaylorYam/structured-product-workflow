"""信箱維護（`sp_workflow.mailbox`，#158）：新增、修改、刪除人員與別名，資料庫的檢查轉成原因，每次存檔留紀錄。

對真的 PostgreSQL 跑，人員與信箱全部虛構。
"""

from __future__ import annotations

import pytest

from db_synth import seed_mailbox
from sp_workflow.mailbox import (
    Alias,
    Mailbox,
    MailboxError,
    MailboxRows,
    Person,
    alias_from,
    delete_alias,
    delete_person,
    load_mailbox,
    load_rows,
    person_from,
    save_alias,
    save_person,
)

pytestmark = pytest.mark.db

AMY = Person("Amy", "amy@example.com", "FC", False)
BEN = Person("Ben", "ben@example.com", "FC", False)


# ---------- 名字比對（#201：比對規則唯一的測試位置） ----------

MAILBOX = Mailbox((AMY, BEN), {"amie": "Amy"})


def test_match_goes_through_an_alias() -> None:
    assert MAILBOX.match("Amie") == AMY
    assert MAILBOX.match(" AMIE ") == AMY, "別名也不分大小寫、去頭尾空白"


def test_match_ignores_case_and_surrounding_spaces() -> None:
    assert MAILBOX.match("ben") == BEN and MAILBOX.match(" BEN ") == BEN


def test_match_does_not_guess() -> None:
    assert MAILBOX.match("Amyy") is None and MAILBOX.match("Bennie") is None, "不模糊比對"


def log(pg) -> list[tuple]:
    return pg.execute(
        "SELECT changed_by, kind, action, before, after FROM mailbox.change_log ORDER BY change_id"
    ).fetchall()


def test_rows_are_listed_by_name_ignoring_case(pg) -> None:
    people = [
        ("ben", "ben@example.com", None, False),
        ("Cat", "cat@example.com", "PM", True),
        ("Amy", "a@example.com", "FC", False),
    ]
    seed_mailbox(pg, people, aliases=[("Zed", "ben"), ("amie", "Amy")])

    rows = load_rows(pg)

    assert [p.name for p in rows.people] == ["Amy", "ben", "Cat"]
    assert rows.aliases == (Alias("amie", "Amy"), Alias("Zed", "ben"))
    assert rows.aliases_of("Amy") == (Alias("amie", "Amy"),)


def test_add_and_edit_a_person_keep_a_record(pg) -> None:
    assert save_person(pg, None, AMY, user="op")
    edited = Person("Amy", "amy.lin@example.com", None, True)

    assert save_person(pg, AMY, edited, user="op")

    assert load_rows(pg).people == (edited,)
    assert log(pg) == [
        ("op", "人員", "新增", None, {"name": "Amy", "email": "amy@example.com", "unit": "FC", "fixed_cc": False}),
        (
            "op",
            "人員",
            "修改",
            {"name": "Amy", "email": "amy@example.com", "unit": "FC", "fixed_cc": False},
            {"name": "Amy", "email": "amy.lin@example.com", "unit": None, "fixed_cc": True},
        ),
    ]


def test_saving_without_changes_writes_nothing(pg) -> None:
    seed_mailbox(pg)

    assert not save_person(pg, AMY, AMY, user="op")
    assert log(pg) == []


def test_renaming_a_person_moves_their_aliases(pg) -> None:
    seed_mailbox(pg, aliases=[("Amie", "Amy")])

    save_person(pg, AMY, Person("Amelia", AMY.email, AMY.unit, AMY.fixed_cc), user="op")

    assert load_rows(pg).aliases == (Alias("Amie", "Amelia"),)
    assert load_mailbox(pg).match("amie").name == "Amelia", "寄信與匯入照舊用別名比對"
    ((_, _, _, before, after),) = log(pg)
    assert (before["name"], before["aliases"]) == ("Amy", ["Amie"]), "跟著改的別名也寫進紀錄"
    assert (after["name"], after["aliases"]) == ("Amelia", ["Amie"])


def test_a_missing_write_permission_becomes_a_reason(pg) -> None:
    from psycopg import errors

    from sp_workflow.mailbox import _explained

    with pytest.raises(MailboxError, match="這個資料庫帳號沒有修改信箱的權限"), _explained(pg, "Amy"):
        raise errors.InsufficientPrivilege("permission denied for table person")


def test_deleting_a_person_deletes_their_aliases_and_records_them(pg) -> None:
    seed_mailbox(pg, aliases=[("Amie", "Amy"), ("A. Lin", "Amy"), ("Benny", "Ben")])

    assert delete_person(pg, AMY, user="op") == (Alias("A. Lin", "Amy"), Alias("Amie", "Amy"))

    rows = load_rows(pg)
    assert [p.name for p in rows.people] == ["Ben", "Pat"] and rows.aliases == (Alias("Benny", "Ben"),)
    ((_, kind, action, before, after),) = log(pg)
    assert (kind, action, after) == ("人員", "刪除", None)
    assert before == {
        "name": "Amy",
        "email": "amy@example.com",
        "unit": "FC",
        "fixed_cc": False,
        "aliases": ["A. Lin", "Amie"],
    }


def test_aliases_are_added_edited_and_deleted_with_records(pg) -> None:
    seed_mailbox(pg)

    save_alias(pg, None, Alias("Amie", "Amy"), user="op")
    save_alias(pg, Alias("Amie", "Amy"), Alias("Benny", "Ben"), user="op")
    delete_alias(pg, Alias("Benny", "Ben"), user="op")

    assert load_rows(pg).aliases == ()
    assert [(kind, action, before, after) for _, kind, action, before, after in log(pg)] == [
        ("別名", "新增", None, {"alias": "Amie", "person_name": "Amy"}),
        ("別名", "修改", {"alias": "Amie", "person_name": "Amy"}, {"alias": "Benny", "person_name": "Ben"}),
        ("別名", "刪除", {"alias": "Benny", "person_name": "Ben"}, None),
    ]


def test_changing_only_the_case_of_an_alias(pg) -> None:
    seed_mailbox(pg, aliases=[("amie", "Amy")])

    save_alias(pg, Alias("amie", "Amy"), Alias("Amie", "Amy"), user="op")

    assert load_rows(pg).aliases == (Alias("Amie", "Amy"),)


@pytest.mark.parametrize(
    ("write", "reason"),
    [
        (
            lambda pg: save_person(pg, None, Person("amy", "x@example.com", None, False)),
            "名字「amy」已經有了（不分大小寫）",
        ),
        (lambda pg: save_person(pg, None, Person("Amy", "x@example.com", None, False)), "名字「Amy」已經有了"),
        (lambda pg: save_person(pg, BEN, Person("AMY", BEN.email, BEN.unit, False)), "名字「AMY」已經有了"),
        (lambda pg: save_person(pg, None, Person("Cat", "not-an-email", None, False)), "信箱「not-an-email」格式不對"),
        (lambda pg: save_alias(pg, None, Alias("AMIE", "Ben")), "別名「AMIE」已經有了（不分大小寫）"),
        (lambda pg: save_alias(pg, None, Alias("Zed", "Nobody")), "別名要指到信箱裡的人：沒有「Nobody」"),
    ],
)
def test_database_checks_become_reasons_and_nothing_is_written(pg, write, reason: str) -> None:
    seed_mailbox(pg, aliases=[("Amie", "Amy")])
    before = load_rows(pg)

    with pytest.raises(MailboxError, match=reason):
        write(pg)

    assert load_rows(pg) == before and log(pg) == []


@pytest.mark.parametrize(
    "write",
    [
        lambda pg: save_person(pg, Person("Amy", "old@example.com", "FC", False), AMY),
        lambda pg: delete_person(pg, Person("Zoe", "zoe@example.com", None, False)),
        lambda pg: save_alias(pg, Alias("Amie", "Ben"), Alias("Amie", "Pat")),
        lambda pg: delete_alias(pg, Alias("Nobody", "Amy")),
    ],
)
def test_rows_changed_since_opening_are_not_overwritten(pg, write) -> None:
    seed_mailbox(pg, aliases=[("Amie", "Amy")])
    before = load_rows(pg)

    with pytest.raises(MailboxError, match="在打開之後被改過或刪掉了，請按「重新整理」再改"):
        write(pg)

    assert load_rows(pg) == before and log(pg) == []


def test_change_log_is_insert_only(pg) -> None:
    import psycopg

    save_person(pg, None, AMY, user="op")

    with pytest.raises(psycopg.errors.RaiseException, match="只新增"):
        pg.execute("DELETE FROM mailbox.change_log")


def test_input_is_trimmed_and_required() -> None:
    assert person_from(" Amy ", " amy@example.com ", "  ", True) == Person("Amy", "amy@example.com", None, True)
    assert alias_from(" Amie ", "Amy") == Alias("Amie", "Amy")
    for build, reason in (
        (lambda: person_from(" ", "a@example.com", "", False), "請填名字"),
        (lambda: person_from("Amy", "", "", False), "請填信箱"),
        (lambda: alias_from("", "Amy"), "請填別名"),
        (lambda: alias_from("Amie", ""), "請選這個別名是哪一個人"),
    ):
        with pytest.raises(MailboxError, match=reason):
            build()


def test_window_actions_write_then_reload_everything(pg, pg_settings) -> None:
    from sp_workflow.window_actions import (
        delete_mailbox_person,
        load_mailbox_rows,
        save_mailbox_alias,
        save_mailbox_person,
    )

    assert load_mailbox_rows(pg_settings) == load_rows(pg)
    rows = save_mailbox_person(pg_settings, None, AMY)
    assert rows.people == (AMY,)
    assert save_mailbox_alias(pg_settings, None, Alias("Amie", "Amy")).aliases == (Alias("Amie", "Amy"),)
    assert delete_mailbox_person(pg_settings, AMY) == load_rows(pg) == MailboxRows((), ())
    assert [action for _, _, action, _, _ in log(pg)] == ["新增", "新增", "刪除"]
