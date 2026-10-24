"""「維護」選單的「信箱維護…」（`sp_workflow.tabs.mailbox`，#158）：列出、新增、修改、刪除人員與別名。

背景執行改成馬上做完；資料庫動作換成記下呼叫、回傳虛構的人員與別名。
"""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from tkinter import messagebox

import pytest

import sp_workflow.tabs.mailbox as screen
from sp_workflow.mailbox import Alias, MailboxError, MailboxRows, Person
from sp_workflow.tabs.mailbox import AliasForm, PersonForm
from window_synth import new_window

AMY = Person("Amy", "amy@example.com", "FC", False)
PAT = Person("Pat", "pat@example.com", "PM", True)
ROWS = MailboxRows((AMY, PAT), (Alias("Amie", "Amy"), Alias("A. Lin", "Amy")))


class Now:
    """馬上在同一個執行緒做完的 `Background`。"""

    def run(self, action, done):
        future: Future = Future()
        try:
            future.set_result(action())
        except Exception as exc:  # noqa: BLE001 - 和 Background 一樣交給 done
            future.set_exception(exc)
        done(future)
        return future

    def close(self) -> None:
        pass


@pytest.fixture
def opened(master, tmp_path: Path, monkeypatch):
    calls: list[tuple] = []
    for name in ("save_mailbox_person", "delete_mailbox_person", "save_mailbox_alias", "delete_mailbox_alias"):
        monkeypatch.setattr(screen, name, lambda settings, *args, name=name: calls.append((name, *args)) or ROWS)
    monkeypatch.setattr(screen, "load_mailbox_rows", lambda settings: ROWS)
    monkeypatch.setattr(screen.Background, "run", lambda self, action, done: Now().run(action, done))
    window = new_window(master, tmp_path)
    monkeypatch.setattr(window.db, "settings", lambda: object())
    window.open_mailbox()
    return window, window.mailbox, calls


def values(table) -> list[tuple]:
    return [tuple(table.item(item, "values")) for item in table.get_children()]


def test_menu_opens_one_window_listing_people_and_aliases(opened) -> None:
    window, mailbox, _ = opened

    assert values(mailbox.people) == [
        ("Amy", "amy@example.com", "FC", "", "Amie、A. Lin"),
        ("Pat", "pat@example.com", "PM", "是", ""),
    ]
    assert values(mailbox.aliases) == [("Amie", "Amy"), ("A. Lin", "Amy")]
    assert mailbox.status_var.get() == "人員 2 位（固定副本 1 位）、別名 2 個。"
    window.open_mailbox()
    assert window.mailbox is mailbox, "已經開著就拉到前面，不重開"


def test_add_edit_and_delete_a_person(opened, monkeypatch) -> None:
    _, mailbox, calls = opened
    added = Person("Ben", "ben@example.com", None, False)
    monkeypatch.setattr(screen, "ask_person", lambda parent, before: added)
    mailbox.add_person()
    assert calls[-1] == ("save_mailbox_person", None, added)
    assert mailbox.status_var.get().startswith("已存：新增人員「Ben」。")

    edited = Person("Amy", "amy.lin@example.com", "FC", True)
    monkeypatch.setattr(screen, "ask_person", lambda parent, before: edited)
    mailbox.people.selection_set("p0")
    mailbox.edit_person()
    assert calls[-1] == ("save_mailbox_person", AMY, edited)

    asked = []
    monkeypatch.setattr(messagebox, "askyesno", lambda title, message, **kw: asked.append(message) or True)
    mailbox.people.selection_set("p0")
    mailbox.delete_person()
    assert calls[-1] == ("delete_mailbox_person", AMY)
    assert "他的別名也會一起刪掉：Amie、A. Lin" in asked[0]


def test_editing_without_changes_does_not_save(opened, monkeypatch) -> None:
    _, mailbox, calls = opened
    monkeypatch.setattr(screen, "ask_person", lambda parent, before: before)
    mailbox.people.selection_set("p1")

    mailbox.edit_person()

    assert calls == [] and mailbox.status_var.get() == "沒有改任何欄位，這次沒有存。"


def test_add_edit_and_delete_an_alias(opened, monkeypatch) -> None:
    _, mailbox, calls = opened
    names = []
    monkeypatch.setattr(
        screen, "ask_alias", lambda parent, before, people: names.append(people) or Alias("Patty", "Pat")
    )
    mailbox.add_alias()
    assert calls[-1] == ("save_mailbox_alias", None, Alias("Patty", "Pat")) and names == [["Amy", "Pat"]]

    mailbox.aliases.selection_set("a1")
    mailbox.edit_alias()
    assert calls[-1] == ("save_mailbox_alias", Alias("A. Lin", "Amy"), Alias("Patty", "Pat"))

    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: False)
    mailbox.aliases.selection_set("a0")
    mailbox.delete_alias()
    assert calls[-1][0] == "save_mailbox_alias", "按否不刪"


def test_nothing_selected_asks_to_pick_first(opened, monkeypatch) -> None:
    _, mailbox, calls = opened
    told = []
    monkeypatch.setattr(messagebox, "showinfo", lambda title, message, **kw: told.append(message))

    mailbox.edit_person()
    mailbox.delete_alias()

    assert told == ["請先在人員表點一個人。", "請先在別名表點一個別名。"] and calls == []


def test_a_rejected_save_shows_the_reason_and_keeps_the_table(opened, monkeypatch) -> None:
    _, mailbox, _ = opened
    shown = []
    monkeypatch.setattr(messagebox, "showerror", lambda title, message, **kw: shown.append(message))
    monkeypatch.setattr(screen, "ask_person", lambda parent, before: Person("amy", "x@example.com", None, False))

    def rejected(settings, before, after):
        raise MailboxError("名字「amy」已經有了（不分大小寫）")

    monkeypatch.setattr(screen, "save_mailbox_person", rejected)
    mailbox.add_person()

    assert shown == ["名字「amy」已經有了（不分大小寫）"]
    assert mailbox.status_var.get() == shown[0] and len(values(mailbox.people)) == 2
    assert str(mailbox.refresh_button.cget("state")) == "normal"


def test_no_fixed_cc_is_pointed_out(opened, monkeypatch) -> None:
    _, mailbox, _ = opened
    monkeypatch.setattr(screen, "load_mailbox_rows", lambda settings: MailboxRows((AMY,), ()))

    mailbox.refresh()

    assert mailbox.status_var.get() == "人員 1 位（固定副本 0 位）、別名 0 個。沒有任何固定副本，寄信草稿會停下來。"


def test_closing_the_main_window_closes_the_mailbox_window(opened) -> None:
    window, mailbox, _ = opened

    window.close()

    assert not mailbox.winfo_exists()


def test_person_form_reads_trimmed_input_and_shows_errors(master) -> None:
    form = PersonForm(master, AMY)
    assert (form.name_var.get(), form.email_var.get(), form.unit_var.get(), form.cc_var.get()) == (
        "Amy",
        "amy@example.com",
        "FC",
        False,
    )
    form.email_var.set(" ")
    form.confirm()
    assert form.error_var.get() == "請填信箱。" and form.winfo_exists()

    form.email_var.set(" amy.lin@example.com ")
    form.unit_var.set("")
    form.cc_var.set(True)
    form.confirm()
    assert form.result == Person("Amy", "amy.lin@example.com", None, True) and not form.winfo_exists()


def test_alias_form_picks_a_person_from_the_mailbox(master) -> None:
    form = AliasForm(master, None, ["Amy", "Pat"])
    assert tuple(form.person_box.cget("values")) == ("Amy", "Pat")
    form.alias_var.set("Patty")
    form.confirm()
    assert form.error_var.get() == "請選這個別名是哪一個人。"

    form.person_var.set("Pat")
    form.confirm()
    assert form.result == Alias("Patty", "Pat")
