"""進度頁的三個登記（#157、ADR 0019）：登記已寄出、登記顧客贖回、提出與解除異議。

資料庫測試對真的 PostgreSQL；視窗測試把資料庫動作換掉。商品、事件與備註全部虛構。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tkinter import messagebox

import pytest

import sp_workflow.tabs.progress
from db_synth import inventory, seed_products
from order_synth import CONFIG
from sp_workflow.lifecycle import (
    KO,
    REDEMPTION,
    AutoEvent,
    StoredEvent,
    current_events,
    open_objections,
    replace_auto_events,
)
from sp_workflow.progress import DATE_COLUMNS, FINISHED, SENT_STEP, TO_PREPARE, ProgressRow, load_progress
from sp_workflow.tabs.progress_dialogs import FormDialog, parse_redemption
from sp_workflow.window_actions import (
    EventChoices,
    Registered,
    event_choices,
    mark_sent,
    object_to_event,
    register_redemptions,
    resolve_event_objection,
)
from window_synth import new_window

A, B = "147199990001", "147199990002"
T = dt.datetime(2026, 10, 20, 9, 0, tzinfo=dt.UTC)
SENT = DATE_COLUMNS.index(SENT_STEP)


# ---------------------------------------------------------------- 資料庫


def step_count(pg, tdcc: str) -> int:
    return pg.execute(
        "SELECT count(*) FROM workflow.step_record WHERE tdcc = %s AND step = '已寄出'", (tdcc,)
    ).fetchone()[0]


@pytest.mark.db
def test_marking_sent_finishes_the_product_and_a_bad_tdcc_only_fails_itself(pg, pg_settings) -> None:
    seed_products(pg, [inventory(A, [("台北", 10000)])])

    result = mark_sent(pg_settings, [A, "12345"])

    assert result.done == (A,) and [t for t, _ in result.failed] == ["12345"]
    assert step_count(pg, A) == 1
    (by,) = pg.execute("SELECT recorded_by FROM workflow.step_record WHERE tdcc = %s", (A,)).fetchone()
    assert by, "留誰寫的"
    (row,) = load_progress(pg, CONFIG, dt.date(2026, 10, 22))
    assert row.progress == FINISHED


@pytest.mark.db
def test_redemption_is_refused_for_products_with_events(pg, pg_settings) -> None:
    seed_products(pg, [inventory(t, [("台北", 10000)]) for t in (A, B)])
    replace_auto_events(pg, B, [AutoEvent(KO, dt.date(2026, 10, 1))], user="tester")

    result = register_redemptions(pg_settings, [A, B], dt.date(2026, 10, 21), "顧客來電")

    assert result.done == (A,)
    assert result.failed[0][0] == B and "已有存續事件「KO」" in result.failed[0][1]
    (event,) = current_events(pg, A)
    assert (event.event, event.event_date, event.source, event.note) == (
        REDEMPTION,
        dt.date(2026, 10, 21),
        "人工",
        "顧客來電",
    )


@pytest.mark.db
def test_objections_are_raised_shown_and_resolved(pg, pg_settings) -> None:
    seed_products(pg, [inventory(A, [("台北", 10000)])])
    replace_auto_events(pg, A, [AutoEvent(KO, dt.date(2026, 10, 1))], user="tester")

    choices = event_choices(pg_settings, A)
    (event,) = choices.events
    objection_id = object_to_event(pg_settings, event.event_id, "上手通知 10/2 才 KO")

    (row,) = load_progress(pg, CONFIG, dt.date(2026, 10, 22))
    assert row.objection == "待確認：KO 2026-10-01：上手通知 10/2 才 KO"
    assert row.lifecycle == KO, "異議不改事件"
    assert [o.objection_id for o in event_choices(pg_settings, A).objections] == [objection_id]

    assert resolve_event_objection(pg_settings, objection_id) == objection_id
    assert open_objections(pg, [A]) == {}
    (resolved,) = pg.execute(
        "SELECT resolved_by IS NOT NULL AND resolved_at IS NOT NULL FROM workflow.event_objection"
    ).fetchone()
    assert resolved, "解除留誰、何時"


# ---------------------------------------------------------------- 小視窗


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("2026-10-21", (dt.date(2026, 10, 21), "")),
        ("", (None, "請填事件日（顧客贖回的日期）。")),
        ("10/21", (None, "事件日「10/21」不是 YYYY-MM-DD。")),
    ],
)
def test_redemption_day_is_required(text: str, found) -> None:
    assert parse_redemption(text) == found


def test_the_form_keeps_open_until_the_input_is_valid(master) -> None:
    dialog = FormDialog(master, "提出異議", "選一筆", choices=["KO 2026-10-01"], note_required=True)

    dialog.confirm()
    assert dialog.result is None and "備註" in dialog.error_var.get()

    dialog.note_var.set("上手通知不同")
    dialog.confirm()
    assert dialog.result == (0, None, "上手通知不同")


# ---------------------------------------------------------------- 視窗


def row(tdcc: str, sent: bool = False) -> ProgressRow:
    dates = tuple(T if sent and i == SENT else None for i in range(len(DATE_COLUMNS)))
    return ProgressRow(tdcc, "SG", dt.date(2026, 10, 15), 7, FINISHED if sent else TO_PREPARE, "在庫", dates, "")


ROWS = (row(A), row(B, sent=True))


def loaded(master, tmp_path: Path, monkeypatch, rows=ROWS):
    window = new_window(master, tmp_path)
    tab = window.progress
    actions = []
    monkeypatch.setattr(tab.db, "settings", lambda: object())
    monkeypatch.setattr(tab, "run_in_background", lambda action, *a, **k: actions.append((action, a[1])))
    monkeypatch.setattr(sp_workflow.tabs.progress, "load_progress_rows", lambda settings, paths: rows)
    tab.refresh()
    action, done = actions.pop()
    done(action())
    return tab, actions


def run_all(actions: list) -> None:
    while actions:
        action, done = actions.pop(0)
        done(action())


def select(tab, *tdccs: str) -> None:
    tab.table.selection_set([i for i in tab.table.get_children() if tab.table.set(i, "tdcc") in tdccs])


def test_nothing_is_written_without_a_selection(master, tmp_path: Path, monkeypatch) -> None:
    tab, actions = loaded(master, tmp_path, monkeypatch)
    shown = []
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: shown.append(a[1]))

    tab.mark_sent()
    tab.raise_objection()

    assert actions == [] and len(shown) == 2 and "請先在表上選商品" in shown[0]


@pytest.mark.parametrize(("answer", "written"), [(True, [A, B]), (False, [A]), (None, None)])
def test_already_sent_products_are_asked_about_first(master, tmp_path: Path, monkeypatch, answer, written) -> None:
    tab, actions = loaded(master, tmp_path, monkeypatch)
    calls = []
    asked = []
    monkeypatch.setattr(messagebox, "askyesnocancel", lambda *a, **k: asked.append(a[1]) or answer)
    monkeypatch.setattr(
        sp_workflow.tabs.progress,
        "mark_sent",
        lambda settings, tdccs: calls.append(list(tdccs)) or Registered(tuple(tdccs)),
    )
    select(tab, A, B)

    tab.mark_sent()
    run_all(actions)

    assert B in asked[0] and A not in asked[0]
    assert calls == ([] if written is None else [written])
    if written:
        assert f"登記已寄出：寫了 {len(written)} 檔。" in tab.status_var.get(), "寫完重新整理，狀態列先寫登記結果"


def test_a_failed_product_is_reported(master, tmp_path: Path, monkeypatch) -> None:
    tab, actions = loaded(master, tmp_path, monkeypatch)
    warnings = []
    monkeypatch.setattr(messagebox, "showwarning", lambda *a, **k: warnings.append(a[1]))
    result = Registered((A,), ((B, "已有存續事件「KO」，不能登記顧客贖回"),))
    monkeypatch.setattr(sp_workflow.tabs.progress, "ask_redemption", lambda parent, tdccs: (dt.date(2026, 10, 21), ""))
    monkeypatch.setattr(sp_workflow.tabs.progress, "register_redemptions", lambda settings, tdccs, day, note: result)
    select(tab, A, B)

    tab.register_redemption()
    run_all(actions)

    assert warnings == [f"登記顧客贖回：寫了 1 檔；寫不進去 1 檔：{B} 已有存續事件「KO」，不能登記顧客贖回"]


def test_cancelling_the_redemption_form_writes_nothing(master, tmp_path: Path, monkeypatch) -> None:
    tab, actions = loaded(master, tmp_path, monkeypatch)
    monkeypatch.setattr(sp_workflow.tabs.progress, "ask_redemption", lambda parent, tdccs: None)
    select(tab, A)

    tab.register_redemption()

    assert actions == []


EVENT = StoredEvent(7, A, KO, dt.date(2026, 10, 1), None, "比價", None, "tester", T)


def test_raising_an_objection_picks_one_automatic_event(master, tmp_path: Path, monkeypatch) -> None:
    tab, actions = loaded(master, tmp_path, monkeypatch)
    raised = []
    monkeypatch.setattr(
        sp_workflow.tabs.progress, "event_choices", lambda settings, tdcc: EventChoices(tdcc, (EVENT,), ())
    )
    monkeypatch.setattr(
        sp_workflow.tabs.progress, "ask_objection", lambda parent, tdcc, events: (events[0].event_id, "不同")
    )
    monkeypatch.setattr(
        sp_workflow.tabs.progress,
        "object_to_event",
        lambda settings, event_id, note: raised.append((event_id, note)) or 3,
    )
    select(tab, A)

    tab.raise_objection()
    run_all(actions)

    assert raised == [(7, "不同")]
    assert f"{A} 已提出異議（待確認）。" in tab.status_var.get()


def test_objections_need_one_product_with_automatic_events(master, tmp_path: Path, monkeypatch) -> None:
    tab, actions = loaded(master, tmp_path, monkeypatch)
    shown = []
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: shown.append(a[1]))
    monkeypatch.setattr(sp_workflow.tabs.progress, "event_choices", lambda settings, tdcc: EventChoices(tdcc, (), ()))
    select(tab, A, B)
    tab.raise_objection()
    assert actions == [] and shown == ["這個動作一次只能選一檔。"]

    select(tab, A)
    tab.raise_objection()
    run_all(actions)
    tab.resolve_objection()
    run_all(actions)

    assert shown[1:] == [f"{A} 沒有比價產生的自動事件，不能提出異議。", f"{A} 沒有還沒解除的異議。"]


def test_resolving_an_objection(master, tmp_path: Path, monkeypatch) -> None:
    from sp_workflow.lifecycle import Objection

    tab, actions = loaded(master, tmp_path, monkeypatch)
    objection = Objection(3, 7, A, KO, dt.date(2026, 10, 1), "不同", "tester", T)
    resolved = []
    monkeypatch.setattr(
        sp_workflow.tabs.progress, "event_choices", lambda settings, tdcc: EventChoices(tdcc, (EVENT,), (objection,))
    )
    monkeypatch.setattr(sp_workflow.tabs.progress, "ask_resolution", lambda parent, tdcc, objections: 3)
    monkeypatch.setattr(
        sp_workflow.tabs.progress, "resolve_event_objection", lambda settings, oid: resolved.append(oid) or oid
    )
    select(tab, A)

    tab.resolve_objection()
    run_all(actions)

    assert resolved == [3] and f"{A} 已解除異議。" in tab.status_var.get()


def test_the_buttons_wait_for_a_refresh(master, tmp_path: Path) -> None:
    tab = new_window(master, tmp_path).progress

    assert str(tab.sent_button.cget("state")) == "disabled" and str(tab.object_button.cget("state")) == "disabled"
