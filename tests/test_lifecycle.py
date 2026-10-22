"""存續事件（#152、ADR 0019）：存續狀態 = 最新一筆還有效的事件；自動事件整組取代並留紀錄，人工只能登記顧客贖回與提出異議。

對真的 PostgreSQL 跑，商品與事件全部虛構。舊清單狀態的轉換在 test_database.py。
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from sp_workflow.lifecycle import (
    DELIVERY,
    IN_STOCK,
    KO,
    MATURITY,
    REDEMPTION,
    AutoEvent,
    LifecycleError,
    current_events,
    lifecycle_status,
    raise_objection,
    record_redemption,
    replace_auto_events,
    resolve_objection,
)

pytestmark = pytest.mark.db

A, B = "147000000001", "147000000002"
D1, D2, D3 = dt.date(2026, 11, 2), dt.date(2026, 11, 3), dt.date(2027, 4, 22)


@pytest.fixture
def products(pg):
    for tdcc in (A, B):
        pg.execute(
            "INSERT INTO workflow.product (tdcc, product, issuer, source) VALUES (%s, 'FCN', 'SG', '人工維護')", (tdcc,)
        )
    return pg


def changes(pg, tdcc: str = A) -> list[tuple]:
    return pg.execute(
        "SELECT changed_by, previous, current FROM workflow.product_event_change WHERE tdcc = %s ORDER BY change_id",
        (tdcc,),
    ).fetchall()


def old_list_event(pg, tdcc: str, event: str, day: dt.date | None) -> int:
    (event_id,) = pg.execute(
        "INSERT INTO workflow.product_event (tdcc, event, event_date, source, recorded_by)"
        " VALUES (%s, %s, %s, '舊清單', '資料表版本 0016') RETURNING event_id",
        (tdcc, event, day),
    ).fetchone()
    return event_id


# ---------- 存續狀態 ----------


def test_no_event_is_in_stock(products) -> None:
    status = lifecycle_status(products, [A])[A]

    assert (status.status, status.latest) == (IN_STOCK, None)


def test_status_is_the_latest_current_event(products) -> None:
    replace_auto_events(products, A, [AutoEvent(KO, D1)], user="比價")
    old_list_event(products, B, MATURITY, D3)

    statuses = lifecycle_status(products, [B, A, "147000000009"])

    assert list(statuses) == [B, A, "147000000009"], "依輸入順序"
    assert (statuses[A].status, statuses[A].latest.event_date) == (KO, D1)
    assert (statuses[B].status, statuses[B].latest.source) == (MATURITY, "舊清單")
    assert statuses["147000000009"].status == IN_STOCK


# ---------- 自動事件：整組取代 ----------


def test_first_auto_events_are_written_with_a_change_record(products) -> None:
    assert replace_auto_events(products, A, [AutoEvent(DELIVERY, D3, "AAA UW")], user="tester") is True

    (event,) = current_events(products, A)
    assert (event.event, event.event_date, event.underlying, event.source, event.recorded_by) == (
        DELIVERY,
        D3,
        "AAA UW",
        "比價",
        "tester",
    )
    assert changes(products) == [("tester", [], [[DELIVERY, "2027-04-22", "AAA UW"]])]


def test_same_auto_events_write_nothing(products) -> None:
    replace_auto_events(products, A, [AutoEvent(KO, D1)], user="tester")

    assert replace_auto_events(products, A, [AutoEvent(KO, D1)], user="tester") is False
    assert len(changes(products)) == 1 and len(current_events(products, A)) == 1


def test_different_auto_events_replace_the_whole_set_and_keep_history(products) -> None:
    replace_auto_events(products, A, [AutoEvent(KO, D2)], user="tester")

    assert replace_auto_events(products, A, [AutoEvent(KO, D1)], user="tester") is True

    (event,) = current_events(products, A)
    assert event.event_date == D1 and lifecycle_status(products, [A])[A].latest.event_date == D1
    all_rows = products.execute(
        "SELECT event_date, superseded_at IS NOT NULL FROM workflow.product_event WHERE tdcc = %s ORDER BY event_id",
        (A,),
    ).fetchall()
    assert all_rows == [(D2, True), (D1, False)], "舊的那組留著，記下取代時間"
    assert changes(products)[-1][1:] == ([[KO, "2026-11-03", None]], [[KO, "2026-11-02", None]])


def test_auto_events_can_be_cleared(products) -> None:
    replace_auto_events(products, A, [AutoEvent(KO, D1)], user="tester")

    assert replace_auto_events(products, A, [], user="tester") is True
    assert current_events(products, A) == [] and lifecycle_status(products, [A])[A].status == IN_STOCK


def test_auto_replacement_leaves_old_list_and_manual_events(products) -> None:
    old_list_event(products, A, KO, D2)

    replace_auto_events(products, A, [AutoEvent(KO, D1)], user="tester")

    assert [(e.source, e.event_date) for e in current_events(products, A)] == [("比價", D1), ("舊清單", D2)]


def test_only_automatic_kinds_come_from_observation(products) -> None:
    with pytest.raises(LifecycleError, match="不是自動事件"):
        replace_auto_events(products, A, [AutoEvent(REDEMPTION, D1)], user="tester")


def test_unknown_product_is_rejected(products) -> None:
    with pytest.raises(LifecycleError, match="沒有 147000000009"):
        replace_auto_events(products, "147000000009", [AutoEvent(KO, D1)], user="tester")


# ---------- 人工：顧客贖回 ----------


def test_redemption_sets_the_status(products) -> None:
    event = record_redemption(products, A, D2, "  顧客提前贖回  ", user="op")

    assert (event.event, event.source, event.note, event.recorded_by) == (REDEMPTION, "人工", "顧客提前贖回", "op")
    assert lifecycle_status(products, [A])[A].status == REDEMPTION


@pytest.mark.parametrize("existing", ["auto", "old_list", "redemption"])
def test_redemption_is_refused_when_an_event_exists(products, existing: str) -> None:
    if existing == "auto":
        replace_auto_events(products, A, [AutoEvent(KO, D1)], user="tester")
    elif existing == "old_list":
        old_list_event(products, A, MATURITY, None)
    else:
        record_redemption(products, A, D1, user="op")

    with pytest.raises(LifecycleError, match="已有存續事件"):
        record_redemption(products, A, D2, user="op")


# ---------- 異議 ----------


def test_objection_does_not_change_the_event(products) -> None:
    replace_auto_events(products, A, [AutoEvent(KO, D1)], user="tester")
    (event,) = current_events(products, A)

    objection_id = raise_objection(products, event.event_id, "上手通知 11/3 才 KO", user="op")

    assert current_events(products, A) == [event]
    assert products.execute(
        "SELECT event_id, note, raised_by, resolved_at FROM workflow.event_objection WHERE objection_id = %s",
        (objection_id,),
    ).fetchone() == (event.event_id, "上手通知 11/3 才 KO", "op", None)


def test_resolving_an_objection_records_who_and_when(products) -> None:
    replace_auto_events(products, A, [AutoEvent(KO, D1)], user="tester")
    objection_id = raise_objection(products, current_events(products, A)[0].event_id, "待查", user="op")

    resolve_objection(products, objection_id, user="lead")

    resolved_by, resolved = products.execute(
        "SELECT resolved_by, resolved_at IS NOT NULL FROM workflow.event_objection WHERE objection_id = %s",
        (objection_id,),
    ).fetchone()
    assert (resolved_by, resolved) == ("lead", True)
    with pytest.raises(LifecycleError, match="已經解除"):
        resolve_objection(products, objection_id, user="lead")


def test_objections_only_on_current_automatic_events(products) -> None:
    old_id = old_list_event(products, A, KO, D2)
    with pytest.raises(LifecycleError, match="只有比價產生的自動事件"):
        raise_objection(products, old_id, "x", user="op")
    replace_auto_events(products, B, [AutoEvent(KO, D1)], user="tester")
    first = current_events(products, B)[0].event_id
    replace_auto_events(products, B, [AutoEvent(KO, D2)], user="tester")
    with pytest.raises(LifecycleError, match="已被重新比價取代"):
        raise_objection(products, first, "x", user="op")
    with pytest.raises(LifecycleError, match="要寫備註"):
        raise_objection(products, current_events(products, B)[0].event_id, "  ", user="op")


# ---------- 資料庫擋下人工改事件 ----------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE workflow.product_event SET event_date = '2026-12-01'",
        "UPDATE workflow.product_event SET superseded_at = now(), note = 'x'",
        "DELETE FROM workflow.product_event",
    ],
)
def test_events_cannot_be_edited_or_deleted(products, statement: str) -> None:
    import psycopg

    replace_auto_events(products, A, [AutoEvent(KO, D1)], user="tester")

    with pytest.raises(psycopg.errors.RaiseException, match="存續事件不能"):
        products.execute(statement)


def test_manual_source_cannot_write_automatic_events(products) -> None:
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        products.execute(
            "INSERT INTO workflow.product_event (tdcc, event, event_date, source, recorded_by)"
            " VALUES (%s, 'KO', '2026-11-02', '人工', 'op')",
            (A,),
        )


def test_change_record_is_json(products) -> None:
    replace_auto_events(products, A, [AutoEvent(KO, D1), AutoEvent(MATURITY, D3)], user="tester")

    (raw,) = products.execute("SELECT current::text FROM workflow.product_event_change").fetchone()

    assert json.loads(raw) == [[KO, "2026-11-02", None], [MATURITY, "2027-04-22", None]]


def test_redemption_stops_observation(products) -> None:
    record_redemption(products, A, D1, user="op")

    with pytest.raises(LifecycleError, match="已登記顧客贖回，停止比價"):
        replace_auto_events(products, A, [AutoEvent(KO, D2)], user="tester")
    assert lifecycle_status(products, [A])[A].status == REDEMPTION


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE workflow.product_event_change SET changed_by = 'x'",
        "DELETE FROM workflow.product_event_change",
        "UPDATE workflow.event_objection SET note = 'x'",
        "DELETE FROM workflow.event_objection",
    ],
)
def test_change_records_and_objections_are_kept(products, statement: str) -> None:
    import psycopg

    replace_auto_events(products, A, [AutoEvent(KO, D1)], user="tester")
    raise_objection(products, current_events(products, A)[0].event_id, "待查", user="op")

    with pytest.raises(psycopg.errors.RaiseException, match="不能"):
        products.execute(statement)
