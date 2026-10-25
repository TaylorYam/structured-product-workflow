"""「維護」選單的「發行機構評等…」（`sp_workflow.tabs.ratings`，#237）：列出機構與歷次評等、新增、修改、刪除最新一筆。

背景執行改成馬上做完（`immediate`）；資料庫動作換成記下呼叫、回傳虛構的機構與評等。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tkinter import messagebox

import pytest

import sp_workflow.tabs.ratings as screen
from sp_workflow.credit_ratings import GUARANTOR, INITIAL, ISSUER, Entity, Rating, RatingRows
from sp_workflow.tabs.ratings import EntityForm, RatingForm
from window_synth import new_window

ABC = Entity("ABC", ISSUER, "Abc Bank")
XYZ = Entity("XYZ", GUARANTOR, "Xyz Holdings")
ABC_FIRST = Rating("ABC", ISSUER, INITIAL, "A+", "A1", None)
ABC_NOW = Rating("ABC", ISSUER, dt.date(2030, 6, 1), "A", "A2", None)
XYZ_FIRST = Rating("XYZ", GUARANTOR, INITIAL, None, "Baa1", "A-")
ROWS = RatingRows((ABC, XYZ), (ABC_FIRST, ABC_NOW, XYZ_FIRST))


@pytest.fixture
def opened(master, tmp_path: Path, monkeypatch, immediate):
    calls: list[tuple] = []
    for name in ("add_rated_entity", "add_credit_rating", "edit_credit_rating", "delete_credit_rating"):
        monkeypatch.setattr(screen, name, lambda settings, *args, name=name: calls.append((name, *args)) or ROWS)
    monkeypatch.setattr(screen, "load_rating_rows", lambda settings: ROWS)
    window = new_window(master, tmp_path, background=immediate, logged_in=True)
    window.open_ratings()
    return window, window.ratings, calls


def values(table) -> list[tuple]:
    return [tuple(table.item(item, "values")) for item in table.get_children()]


def test_menu_opens_one_window_listing_current_ratings_and_history(opened) -> None:
    window, ratings, _ = opened

    assert values(ratings.entities) == [
        ("ABC", "發行機構", "Abc Bank", "A", "A2", "—", "2030-06-01"),
        ("XYZ", "保證機構", "Xyz Holdings", "—", "Baa1", "A-", "最初"),
    ]
    assert values(ratings.history) == [], "還沒點機構"
    ratings.entities.selection_set("e0")
    ratings.update()
    assert values(ratings.history) == [("2030-06-01", "A", "A2", "—"), ("最初", "A+", "A1", "—")], "新的在上面"
    assert ratings.status_var.get() == "上手 2 家、機構 2 個、評等 3 筆。"
    window.open_ratings()
    assert window.ratings is ratings, "已經開著就拉到前面，不重開"


def test_add_edit_and_delete_ratings_of_the_picked_entity(opened, monkeypatch) -> None:
    _, ratings, calls = opened
    infos = []
    monkeypatch.setattr(messagebox, "showinfo", lambda title, message, **kw: infos.append(message))
    ratings.add_rating()
    assert calls == [] and infos == ["請先在機構表點一個機構。"]

    ratings.entities.selection_set("e0")
    added = Rating("ABC", ISSUER, dt.date(2031, 1, 1), "A-", None, None)
    monkeypatch.setattr(screen, "ask_rating", lambda parent, entity, before, latest=None: added)
    ratings.add_rating()
    assert calls[-1] == ("add_credit_rating", ABC_NOW, added), "帶著打開時看到的最新一筆"
    assert ratings.status_var.get().startswith("已存：新增 ABC 的發行機構評等（生效日 2031-01-01）。")
    assert ratings.entities.selection() == ("e0",), "存檔後停在同一個機構"

    edited = Rating("ABC", ISSUER, dt.date(2030, 6, 1), "A", "A3", None)
    monkeypatch.setattr(screen, "ask_rating", lambda parent, entity, before, latest=None: edited)
    ratings.edit_latest()
    assert calls[-1] == ("edit_credit_rating", ABC_NOW, edited)

    asked = []
    monkeypatch.setattr(messagebox, "askyesno", lambda title, message, **kw: asked.append(message) or True)
    ratings.delete_latest()
    assert calls[-1] == ("delete_credit_rating", ABC_NOW)
    assert "刪除 ABC 的發行機構在 2030-06-01 生效的評等（A／A2／—）？" in asked[0]


def test_the_only_rating_cannot_be_deleted_and_unchanged_edits_are_not_saved(opened, monkeypatch) -> None:
    _, ratings, calls = opened
    infos = []
    monkeypatch.setattr(messagebox, "showinfo", lambda title, message, **kw: infos.append(message))
    ratings.entities.selection_set("e1")

    ratings.delete_latest()
    monkeypatch.setattr(screen, "ask_rating", lambda parent, entity, before, latest=None: before)
    ratings.edit_latest()

    assert calls == [] and "至少要留一筆" in infos[0]
    assert ratings.status_var.get() == "沒有改任何欄位，這次沒有存。"


def test_add_an_entity(opened, monkeypatch) -> None:
    _, ratings, calls = opened
    entity = Entity("NEW", GUARANTOR, "New Holdings")
    first = Rating("NEW", GUARANTOR, INITIAL, "BBB", None, None)
    monkeypatch.setattr(screen, "ask_entity", lambda parent: (entity, first))

    ratings.add_entity()

    assert calls[-1] == ("add_rated_entity", entity, first)
    assert ratings.status_var.get().startswith("已存：新增 NEW 的保證機構「New Holdings」。")


def test_rating_form_reads_and_checks_the_input(master) -> None:
    form = RatingForm(master, ABC, None)
    form.day_var.set("2031-02-03")
    for var, text in zip(form.agency_vars, ("a", "", "aa-"), strict=True):
        var.set(text)
    form.confirm()
    assert form.result == Rating("ABC", ISSUER, dt.date(2031, 2, 3), "A", None, "AA-")

    form = RatingForm(master, ABC, None)
    form.day_var.set("2031/02/03")
    form.agency_vars[0].set("A")
    form.confirm()
    assert form.result is None and "YYYY-MM-DD" in form.error_var.get()
    form.day_var.set("2031-02-03")
    form.agency_vars[1].set("A+")
    form.confirm()
    assert form.result is None and form.error_var.get() == "穆迪評等「A+」格式不對（要像 Aa2、A1、Baa1）。"
    form.destroy()


def test_initial_rating_keeps_its_date_when_edited(master) -> None:
    form = RatingForm(master, XYZ, XYZ_FIRST)
    assert [v.get() for v in form.agency_vars] == ["", "Baa1", "A-"]
    form.agency_vars[0].set("bbb+")
    form.confirm()
    assert form.result == Rating("XYZ", GUARANTOR, INITIAL, "BBB+", "Baa1", "A-")


def test_entity_form_blank_date_means_initial(master) -> None:
    form = EntityForm(master)
    form.issuer_var.set("new")
    form.role_var.set(GUARANTOR)
    form.name_var.set("New Holdings")
    form.agency_vars[1].set("baa2")
    form.confirm()
    assert form.result == (
        Entity("NEW", GUARANTOR, "New Holdings"),
        Rating("NEW", GUARANTOR, INITIAL, None, "Baa2", None),
    )


def test_a_new_rating_starts_from_the_latest_one(master) -> None:
    form = RatingForm(master, ABC, None, ABC_NOW)
    assert form.day_var.get() == "" and [v.get() for v in form.agency_vars] == ["A", "A2", ""]
    form.day_var.set("2031-01-01")
    form.agency_vars[0].set("A-")
    form.confirm()
    assert form.result == Rating("ABC", ISSUER, dt.date(2031, 1, 1), "A-", "A2", None), "只改變了的那家"
