"""進度頁的登記（#157、#186、ADR 0019）：登記已寄出、批次登記已寄出、登記顧客贖回、提出與解除異議。

資料庫測試對真的 PostgreSQL；視窗測試把資料庫動作換掉。商品、事件與備註全部虛構。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tkinter import messagebox

import pytest

import sp_workflow.tabs.progress
import sp_workflow.window_actions
from db_synth import inventory, seed_products, seed_waiting_for_mail
from order_synth import CONFIG
from sp_workflow.lifecycle import (
    KO,
    REDEMPTION,
    AutoEvent,
    LifecycleError,
    StoredEvent,
    current_events,
    open_objections,
    replace_auto_events,
)
from sp_workflow.progress import (
    DATE_COLUMNS,
    FINISHED,
    SENT_STEP,
    TO_CHECK,
    TO_MAIL,
    ProgressRow,
    SentBatch,
    load_progress,
)
from sp_workflow.tabs.progress_dialogs import FormDialog, parse_redemption, parse_sent_through, tdcc_list
from sp_workflow.window_actions import (
    EventChoices,
    Registered,
    event_choices,
    find_sent_batch,
    mark_sent,
    mark_sent_batch,
    object_to_event,
    register_redemptions,
    resolve_event_objection,
    sent_before,
)
from window_synth import HeldBackground, new_window

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
    assert result.failed[0][1].startswith("資料庫錯誤："), "資料庫拒絕的原因用中文開頭"
    assert step_count(pg, A) == 1
    (by,) = pg.execute("SELECT recorded_by FROM workflow.step_record WHERE tdcc = %s", (A,)).fetchone()
    assert by, "留誰寫的"
    (row,) = load_progress(pg, CONFIG, dt.date(2026, 10, 22))
    assert row.progress == FINISHED
    assert sent_before(pg_settings, [B, A, A]) == (A,), "登記前從資料庫查已寄出過的"


@pytest.mark.db
def test_a_batch_of_sent_skips_products_already_sent(pg, pg_settings) -> None:
    """#186、#205：寫的時候已經不是待寄信（已經有「已寄出」、已經解鎖）的跳過，不重複寫、不動其他階段；結果寫出選的交易日。"""
    c = "147199990003"
    seed_waiting_for_mail(pg, [A, B, c])
    pg.execute("UPDATE workflow.product SET locked_at = NULL WHERE tdcc = %s", (c,))  # c 確認期間被解鎖
    mark_sent(pg_settings, [B])  # 找出這批之後別人剛登記過

    result = mark_sent_batch(pg_settings, SentBatch(dt.date(2026, 10, 22), (A, B, c), 0))

    assert result.done == (A,) and result.failed == ()
    assert (step_count(pg, A), step_count(pg, B), step_count(pg, c)) == (1, 1, 0)
    (note,) = pg.execute(
        "SELECT result FROM workflow.step_record WHERE tdcc = %s AND step = %s", (A, SENT_STEP)
    ).fetchone()
    assert note == "進度頁批次登記已寄出（交易日 2026-10-22 以前）"


def test_a_batch_of_sent_reads_the_progress_again(monkeypatch) -> None:
    rows = (row(A, progress=TO_MAIL), row(B, progress=TO_MAIL, trade_date=dt.date(2026, 10, 23)), row("3"))
    monkeypatch.setattr(sp_workflow.window_actions, "load_progress_rows", lambda settings, paths: rows)

    assert find_sent_batch(object(), object(), dt.date(2026, 10, 22)) == SentBatch(dt.date(2026, 10, 22), (A,), 1)


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


def test_the_trade_day_of_a_batch_of_sent_is_required() -> None:
    assert parse_sent_through(" 2026-10-22 ") == (dt.date(2026, 10, 22), "")
    assert parse_sent_through("")[1].startswith("請填交易日")
    assert parse_sent_through("10/22") == (None, "交易日「10/22」不是 YYYY-MM-DD。")


def test_the_batch_form_asks_for_a_trade_day(master) -> None:
    dialog = FormDialog(master, "批次登記已寄出", "選交易日", with_day=True, day_label="交易日",
                        parse_day=parse_sent_through, with_note=False)  # fmt: skip

    dialog.confirm()
    assert dialog.result is None and dialog.error_var.get().startswith("請填交易日")

    dialog.day_var.set("2026-10-22")
    dialog.confirm()
    assert dialog.result == (None, dt.date(2026, 10, 22), "")


def test_the_form_keeps_open_until_the_input_is_valid(master) -> None:
    dialog = FormDialog(master, "提出異議", "選一筆", choices=["KO 2026-10-01"], note_required=True)

    dialog.confirm()
    assert dialog.result is None and "備註" in dialog.error_var.get()

    dialog.note_var.set("上手通知不同")
    assert dialog.bind("<Return>"), "Enter 等於按確定"
    dialog.confirm()
    assert dialog.result == (0, None, "上手通知不同")


def test_long_tdcc_lists_are_shortened() -> None:
    assert tdcc_list(["1", "2"]) == "1、2"
    assert tdcc_list([str(i) for i in range(12)], limit=3) == "0、1、2 等 12 檔"


# ---------------------------------------------------------------- 視窗


def row(tdcc: str, sent: bool = False, **changes) -> ProgressRow:
    dates = tuple(T if sent and i == SENT else None for i in range(len(DATE_COLUMNS)))
    values = {"progress": FINISHED if sent else TO_CHECK, "trade_date": dt.date(2026, 10, 15), **changes}
    return ProgressRow(tdcc, "SG", values.pop("trade_date"), 7, values.pop("progress"), "在庫", dates, "", **values)


ROWS = (row(A), row(B, sent=True))


def loaded(master, tmp_path: Path, monkeypatch, rows=ROWS):
    window = new_window(master, tmp_path, background=HeldBackground, logged_in=True)
    tab = window.progress
    actions = tab.background.held
    monkeypatch.setattr(sp_workflow.tabs.progress, "load_progress_rows", lambda settings, paths: rows)
    tab.refresh()
    tab.background.run_all()
    return tab, actions


def run_all(actions: list) -> None:
    """依序做完 `HeldBackground` 留著的動作（做完時又開始的也算）。"""
    while actions:
        action, done = actions.pop(0)
        done(action())


def select(tab, *tdccs: str) -> None:
    tab.table.selection_set([i for i in tab.table.get_children() if tab.table.set(i, "tdcc") in tdccs])


def test_nothing_is_written_without_a_selection(master, tmp_path: Path, monkeypatch) -> None:
    tab, actions = loaded(master, tmp_path, monkeypatch)
    shown = []
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: shown.append(a[1]))

    tab.record_sent()
    tab.raise_objection()

    assert actions == [] and len(shown) == 2 and "請先在表上選商品" in shown[0]


def recording(tab, monkeypatch, before: tuple[str, ...]) -> list:
    calls = []
    monkeypatch.setattr(sp_workflow.tabs.progress, "sent_before", lambda settings, tdccs: before)
    monkeypatch.setattr(
        sp_workflow.tabs.progress,
        "mark_sent",
        lambda settings, tdccs: calls.append(list(tdccs)) or Registered(tuple(tdccs)),
    )
    return calls


@pytest.mark.parametrize(("answer", "written"), [(True, [A, B]), (False, [A]), (None, None)])
def test_already_sent_products_are_asked_about_first(master, tmp_path: Path, monkeypatch, answer, written) -> None:
    tab, actions = loaded(master, tmp_path, monkeypatch, rows=(row(A), row(B)))
    asked = []
    monkeypatch.setattr(messagebox, "askyesnocancel", lambda *a, **k: asked.append(a[1]) or answer)
    calls = recording(tab, monkeypatch, before=(B,))  # 畫面上還沒寄出，資料庫裡別人剛登記過
    select(tab, A, B)

    tab.record_sent()
    run_all(actions)

    assert B in asked[0] and f"{A} " not in asked[0] and "還沒登記過的 1 檔" in asked[0]
    assert calls == ([] if written is None else [written])
    if written:
        assert f"登記已寄出：寫了 {len(written)} 檔。" in tab.status_var.get(), "寫完重新整理，狀態列先寫登記結果"
        tab.refilter()
        assert "登記已寄出" not in tab.status_var.get(), "登記結果只顯示一次"
    else:
        assert tab.status_var.get().startswith("共 2 檔"), "取消時狀態列回到原本的樣子"


@pytest.mark.parametrize(("answer", "written"), [(True, [[A]]), (False, [])])
def test_products_not_sent_yet_are_confirmed_too(master, tmp_path: Path, monkeypatch, answer, written) -> None:
    tab, actions = loaded(master, tmp_path, monkeypatch)
    asked = []
    monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **k: asked.append(a[1]) or answer)
    calls = recording(tab, monkeypatch, before=())
    select(tab, A)

    tab.record_sent()
    run_all(actions)

    assert asked == [f"登記 1 檔已寄出：{A}？"] and calls == written


def test_no_leaves_nothing_to_write(master, tmp_path: Path, monkeypatch) -> None:
    tab, actions = loaded(master, tmp_path, monkeypatch)
    shown = []
    monkeypatch.setattr(messagebox, "askyesnocancel", lambda *a, **k: False)
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: shown.append(a[1]))
    calls = recording(tab, monkeypatch, before=(B,))
    select(tab, B)

    tab.record_sent()
    run_all(actions)

    assert calls == [] and shown == ["選中的都已經登記過已寄出，這次沒有寫。"]


def test_a_partial_failure_of_sent_is_reported(master, tmp_path: Path, monkeypatch) -> None:
    tab, actions = loaded(master, tmp_path, monkeypatch)
    warnings = []
    monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr(messagebox, "showwarning", lambda *a, **k: warnings.append(a[1]))
    monkeypatch.setattr(sp_workflow.tabs.progress, "sent_before", lambda settings, tdccs: ())
    monkeypatch.setattr(
        sp_workflow.tabs.progress, "mark_sent", lambda settings, tdccs: Registered((A,), ((B, "資料庫錯誤：壞了"),))
    )
    select(tab, A, B)

    tab.record_sent()
    run_all(actions)

    assert warnings == [f"登記已寄出：寫了 1 檔；寫不進去 1 檔：{B} 資料庫錯誤：壞了"]


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


DAY = dt.date(2026, 10, 22)


def batch(tab, monkeypatch, found: SentBatch, day: dt.date | None = DAY) -> list:
    calls = []
    monkeypatch.setattr(sp_workflow.tabs.progress, "ask_sent_through", lambda parent: day)
    monkeypatch.setattr(sp_workflow.tabs.progress, "find_sent_batch", lambda settings, paths, through: found)
    monkeypatch.setattr(
        sp_workflow.tabs.progress,
        "mark_sent_batch",
        lambda settings, chosen: calls.append(chosen) or Registered(chosen.tdccs),
    )
    return calls


@pytest.mark.parametrize(("answer", "written"), [(True, True), (False, False)])
def test_a_batch_of_sent_is_listed_and_confirmed_first(master, tmp_path: Path, monkeypatch, answer, written) -> None:
    tab, actions = loaded(master, tmp_path, monkeypatch)
    asked = []
    monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **k: asked.append(a[1]) or answer)
    found = SentBatch(DAY, (A, B), 3)
    calls = batch(tab, monkeypatch, found)

    tab.record_sent_batch()
    run_all(actions)

    assert asked == [
        f"登記 2 檔已寄出（交易日 2026-10-22 以前、核對通過、還沒登記已寄出）：{A}、{B}？\n"
        "交易日 2026-10-22 以前還沒核對完、這次沒有登記的 3 檔。"
    ]
    assert calls == ([found] if written else [])
    if written:
        assert tab.status_var.get().startswith(
            "登記已寄出：寫了 2 檔；交易日 2026-10-22 以前還沒核對完、這次沒有登記的 3 檔。"
        ), "寫完提示沒登記的檔數"
    else:
        assert tab.status_var.get().startswith("共 2 檔")


def test_a_batch_with_nothing_waiting_for_mail_writes_nothing(master, tmp_path: Path, monkeypatch) -> None:
    tab, actions = loaded(master, tmp_path, monkeypatch)
    shown = []
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: shown.append(a[1]))
    calls = batch(tab, monkeypatch, SentBatch(DAY, (), 2))

    tab.record_sent_batch()
    run_all(actions)

    assert calls == []
    assert shown == [
        "交易日 2026-10-22 以前沒有待寄信的商品，這次沒有寫。\n交易日 2026-10-22 以前還沒核對完、這次沒有登記的 2 檔。"
    ]


def test_cancelling_the_batch_form_reads_nothing(master, tmp_path: Path, monkeypatch) -> None:
    tab, actions = loaded(master, tmp_path, monkeypatch)
    batch(tab, monkeypatch, SentBatch(DAY, (A,), 0), day=None)

    tab.record_sent_batch()

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


def test_cancelling_the_objection_form_restores_the_status(master, tmp_path: Path, monkeypatch) -> None:
    tab, actions = loaded(master, tmp_path, monkeypatch)
    monkeypatch.setattr(
        sp_workflow.tabs.progress, "event_choices", lambda settings, tdcc: EventChoices(tdcc, (EVENT,), ())
    )
    monkeypatch.setattr(sp_workflow.tabs.progress, "ask_objection", lambda parent, tdcc, events: None)
    select(tab, A)

    tab.raise_objection()
    run_all(actions)

    assert tab.status_var.get().startswith("共 2 檔") and str(tab.object_button.cget("state")) == "normal"
    assert [tab.table.set(i, "tdcc") for i in tab.table.selection()] == [A], "選取照樣留著"


def test_a_refused_objection_writes_nothing_and_unlocks_the_buttons(master, tmp_path: Path, monkeypatch) -> None:
    tab, actions = loaded(master, tmp_path, monkeypatch)
    refreshed, errors = [], []
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: errors.append(a[1]))
    monkeypatch.setattr(
        sp_workflow.tabs.progress, "event_choices", lambda settings, tdcc: EventChoices(tdcc, (EVENT,), ())
    )
    monkeypatch.setattr(sp_workflow.tabs.progress, "ask_objection", lambda parent, tdcc, events: (7, "不同"))
    select(tab, A)
    tab.raise_objection()
    action, done = actions.pop(0)
    done(action())
    monkeypatch.setattr(tab, "refresh", lambda notice="": refreshed.append(notice))

    action, done = actions.pop(0)
    done(LifecycleError("這筆事件已被重新比價取代"))  # 背景的錯誤由 `ActionRunner` 顯示，不交給 on_result

    assert errors == ["這筆事件已被重新比價取代"]
    assert refreshed == [] and str(tab.object_button.cget("state")) == "normal"


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
    assert str(tab.batch_sent_button.cget("state")) == "disabled"
