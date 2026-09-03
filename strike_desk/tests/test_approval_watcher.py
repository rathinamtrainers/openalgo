"""Every way an approval ends, and the two follow-up passes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from strike_desk.approval_gate import (
    WITHDRAWAL_NOT_ATTEMPTED,
    WITHDRAWAL_NOT_QUEUED,
    WITHDRAWAL_PERMITTED,
    WITHDRAWAL_REFUSED,
)
from strike_desk.approval_watcher import BROKER_ID_IN_FLIGHT_POLLS, ApprovalWatcher
from strike_desk.journal import (
    APPROVAL_APPROVED,
    APPROVAL_EXPIRED,
    APPROVAL_LATE,
    APPROVAL_REJECTED,
    APPROVAL_WITHDRAWN,
)

from .approval_fixtures import (
    approve_pending,
    cleared_context,
    delete_pending,
    reject_pending,
)


@pytest.fixture
def queued(gate, journal, openalgo, openalgo_db):
    """One real pending order in the mirror, and one real pending approval in the journal."""
    from .approval_fixtures import queue_pending_order

    pending_order_id = queue_pending_order(openalgo_db)
    openalgo.post("/api/v1/placeorder").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "mode": "semi_auto", "pending_order_id": pending_order_id},
        )
    )
    result = gate.submit(cleared_context())
    return result.approval_id, pending_order_id


@pytest.fixture
def watcher(execution_settings, journal, mirror, execution_client):
    """A watcher whose resume always fails, so the fallback path is what is under test."""
    resumed: list[tuple[str, dict]] = []

    def _resume(tick_id: str, resolution: dict) -> bool:
        resumed.append((tick_id, resolution))
        return False

    watcher = ApprovalWatcher(execution_settings, journal, mirror, execution_client, _resume)
    watcher.resumed = resumed  # type: ignore[attr-defined]
    return watcher


def _status_route(openalgo, status="open", average_price=0.0):
    return openalgo.post("/api/v1/orderstatus").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "orderid": "24090100000041",
                    "order_status": status,
                    "average_price": average_price,
                    "quantity": 150,
                },
            },
        )
    )


def test_a_waiting_approval_is_left_alone(watcher, queued, journal, today):
    assert watcher.poll_once() == 0
    assert [row.status for row in journal.list_approvals(today)] == ["pending"]


def test_an_approval_settles_with_its_order(watcher, queued, journal, openalgo, openalgo_db, today):
    """AC-6: identity, timestamp and the order read back, in one poll."""
    approval_id, pending_order_id = queued
    approve_pending(openalgo_db, pending_order_id, by="amit")
    _status_route(openalgo, status="open")

    assert watcher.poll_once() == 1

    rows = {row.status: row for row in journal.list_approvals(today)}
    settled = rows[APPROVAL_APPROVED]
    assert settled.approved_by == "amit"
    assert settled.resolved_at_ist and settled.resolved_at_ist.endswith("IST")
    assert settled.wait_seconds >= 0
    orders = journal.orders_for_approval(approval_id)
    assert [order.order_status for order in orders] == ["open"]
    assert orders[0].broker_order_id == "24090100000041"
    assert orders[0].quantity == settled.quantity
    assert watcher.resumed[0][0] == "tick-0001"  # the graph was asked first


def test_an_approved_row_without_a_broker_id_is_still_in_flight(
    watcher, queued, journal, openalgo, openalgo_db, today
):
    """OpenAlgo commits approved first; the watcher must wait for the broker id."""
    approval_id, pending_order_id = queued
    approve_pending(openalgo_db, pending_order_id, broker_order_id=None, broker_status=None)

    assert watcher.poll_once() == 0
    assert [row.status for row in journal.list_approvals(today)] == ["pending"]
    assert journal.orders_for_approval(approval_id) == []

    approve_pending(openalgo_db, pending_order_id, by="amit")
    _status_route(openalgo, status="open")
    assert watcher.poll_once() == 1

    settled = {row.status: row for row in journal.list_approvals(today)}[APPROVAL_APPROVED]
    assert settled.approved_by == "amit"
    assert journal.orders_for_approval(approval_id)[0].broker_order_id == "24090100000041"


def test_an_approved_row_without_a_broker_id_is_shouted_about_after_a_bound(
    watcher, queued, journal, openalgo_db, today, caplog
):
    import logging

    _, pending_order_id = queued
    approve_pending(openalgo_db, pending_order_id, broker_order_id=None, broker_status=None)

    with caplog.at_level(logging.CRITICAL):
        for _ in range(BROKER_ID_IN_FLIGHT_POLLS - 1):
            assert watcher.poll_once() == 0
        assert "APPROVED ORDER WITHOUT BROKER ID" not in caplog.text
        assert watcher.poll_once() == 0

    assert f"pending order {pending_order_id}" in caplog.text
    assert "APPROVED ORDER WITHOUT BROKER ID" in caplog.text
    assert [row.status for row in journal.list_approvals(today)] == ["pending"]


def test_a_rejection_keeps_the_reason(watcher, queued, journal, openalgo_db, today):
    approval_id, pending_order_id = queued
    reject_pending(openalgo_db, pending_order_id, reason="strike too far OTM")

    watcher.poll_once()

    rows = {row.status: row for row in journal.list_approvals(today)}
    assert "strike too far OTM" in rows[APPROVAL_REJECTED].detail
    assert journal.orders_for_approval(approval_id) == []


def test_a_deleted_pending_row_settles_as_withdrawn(watcher, queued, journal, openalgo_db, today):
    _, pending_order_id = queued
    delete_pending(openalgo_db, pending_order_id)
    watcher.poll_once()
    assert {row.status for row in journal.list_approvals(today)} == {"pending", APPROVAL_WITHDRAWN}


def test_the_deadline_expires_the_intent(
    watcher, queued, journal, execution_settings, today, caplog
):
    """AC-8: nothing to cancel, so the escalation is the CRITICAL line."""
    import logging

    from freezegun import freeze_time

    _, pending_order_id = queued
    later = datetime.now(tz=UTC) + timedelta(
        seconds=execution_settings.approval_deadline_seconds + 1
    )
    with freeze_time(later), caplog.at_level(logging.CRITICAL):
        watcher.poll_once()

    row = {r.status: r for r in journal.list_approvals(today)}[APPROVAL_EXPIRED]
    assert row.withdrawal == WITHDRAWAL_NOT_QUEUED
    assert row.defect is False
    assert f"Reject pending order {pending_order_id}" in caplog.text


def test_the_kill_switch_expires_an_outstanding_intent(
    watcher, queued, journal, execution_settings, today
):
    """AC-14: an intent this desk queued is this desk's to disown."""
    execution_settings.kill_switch_path.write_text("engaged by the trader", encoding="utf-8")
    watcher.poll_once()
    row = {r.status: r for r in journal.list_approvals(today)}[APPROVAL_EXPIRED]
    assert "kill switch" in row.detail


def test_a_late_click_is_caught_and_withdrawn(
    watcher, queued, journal, openalgo, openalgo_db, execution_settings, today, caplog
):
    """AC-9: expired first, then approved anyway — two terminal rows, both true."""
    import logging

    from freezegun import freeze_time

    approval_id, pending_order_id = queued
    later = datetime.now(tz=UTC) + timedelta(
        seconds=execution_settings.approval_deadline_seconds + 1
    )
    with freeze_time(later):
        watcher.poll_once()

    approve_pending(openalgo_db, pending_order_id, by="amit")
    _status_route(openalgo, status="open")
    openalgo.post("/api/v1/cancelorder").mock(
        return_value=httpx.Response(200, json={"status": "success", "orderid": "24090100000041"})
    )

    with caplog.at_level(logging.CRITICAL):
        watcher.poll_once()

    rows = {row.status: row for row in journal.list_approvals(today)}
    assert set(rows) == {"pending", APPROVAL_EXPIRED, APPROVAL_LATE}
    assert rows[APPROVAL_LATE].defect is True
    assert rows[APPROVAL_LATE].withdrawal == WITHDRAWAL_PERMITTED
    assert "LATE APPROVAL" in caplog.text
    assert journal.orders_for_approval(approval_id)[0].broker_order_id == "24090100000041"


def test_a_late_click_before_the_expired_row_is_not_a_normal_approval(
    watcher, queued, journal, openalgo, openalgo_db, execution_settings, today, caplog
):
    """A click that lands after the deadline but before the watcher wrote expired is late."""
    import logging

    from freezegun import freeze_time

    approval_id, pending_order_id = queued
    later = datetime.now(tz=UTC) + timedelta(
        seconds=execution_settings.approval_deadline_seconds + 1
    )
    _status_route(openalgo, status="open")
    openalgo.post("/api/v1/cancelorder").mock(
        return_value=httpx.Response(200, json={"status": "success", "orderid": "24090100000041"})
    )

    with freeze_time(later), caplog.at_level(logging.CRITICAL):
        approve_pending(openalgo_db, pending_order_id, by="amit")
        assert watcher.poll_once() == 1

    rows = {row.status: row for row in journal.list_approvals(today)}
    assert APPROVAL_APPROVED not in rows
    assert rows[APPROVAL_LATE].defect is True
    assert rows[APPROVAL_LATE].withdrawal == WITHDRAWAL_PERMITTED
    assert "LATE APPROVAL" in caplog.text
    assert journal.orders_for_approval(approval_id)[0].broker_order_id == "24090100000041"


def test_a_late_click_that_already_filled_is_shouted_about(
    watcher, queued, journal, openalgo, openalgo_db, execution_settings, today, caplog
):
    """AC-9: a filled late click still logs CRITICAL; cancel is skipped."""
    import logging

    from freezegun import freeze_time

    approval_id, pending_order_id = queued
    later = datetime.now(tz=UTC) + timedelta(
        seconds=execution_settings.approval_deadline_seconds + 1
    )
    with freeze_time(later):
        watcher.poll_once()

    approve_pending(openalgo_db, pending_order_id, by="amit")
    cancel = openalgo.post("/api/v1/cancelorder").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    _status_route(openalgo, status="complete", average_price=191.85)

    with caplog.at_level(logging.CRITICAL):
        watcher.poll_once()

    rows = {row.status: row for row in journal.list_approvals(today)}
    assert rows[APPROVAL_LATE].defect is True
    assert rows[APPROVAL_LATE].withdrawal == WITHDRAWAL_NOT_ATTEMPTED
    assert "LATE APPROVAL" in caplog.text
    assert "Close the position by hand" in caplog.text
    assert cancel.call_count == 0
    assert journal.orders_for_approval(approval_id)[-1].order_status == "complete"


def test_a_late_click_records_a_refused_withdrawal(
    watcher, queued, journal, openalgo, openalgo_db, execution_settings, today
):
    """Live semi-auto refuses the cancel; the desk records the refusal rather than claiming one."""
    from freezegun import freeze_time

    _, pending_order_id = queued
    with freeze_time(datetime.now(tz=UTC) + timedelta(seconds=400)):
        watcher.poll_once()
    approve_pending(openalgo_db, pending_order_id)
    _status_route(openalgo, status="open")
    openalgo.post("/api/v1/cancelorder").mock(
        return_value=httpx.Response(
            403, json={"status": "error", "message": "not allowed in Semi-Auto mode"}
        )
    )

    watcher.poll_once()

    rows = {row.status: row for row in journal.list_approvals(today)}
    assert rows[APPROVAL_LATE].withdrawal == WITHDRAWAL_REFUSED


def test_an_order_is_followed_to_its_fill(watcher, queued, journal, openalgo, openalgo_db):
    approval_id, pending_order_id = queued
    approve_pending(openalgo_db, pending_order_id)
    _status_route(openalgo, status="open")
    watcher.poll_once()

    _status_route(openalgo, status="complete", average_price=191.85)
    watcher.poll_once()
    watcher.poll_once()  # a terminal order is not watched again

    orders = journal.orders_for_approval(approval_id)
    assert [order.order_status for order in orders] == ["open", "complete"]
    assert orders[-1].average_price == pytest.approx(191.85)


def test_an_unfilled_order_is_cancelled_at_the_fill_deadline(
    watcher, queued, journal, openalgo, openalgo_db, execution_settings
):
    from freezegun import freeze_time

    approval_id, pending_order_id = queued
    approve_pending(openalgo_db, pending_order_id)
    _status_route(openalgo, status="open")
    watcher.poll_once()

    cancel = openalgo.post("/api/v1/cancelorder").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    later = datetime.now(tz=UTC) + timedelta(seconds=execution_settings.fill_deadline_seconds + 1)
    with freeze_time(later):
        watcher.poll_once()
        assert cancel.call_count == 1
        assert [order.order_status for order in journal.orders_for_approval(approval_id)] == [
            "open"
        ]
        _status_route(openalgo, status="cancelled")
        watcher.poll_once()

    assert journal.orders_for_approval(approval_id)[-1].order_status == "cancelled"


def test_a_refused_fill_deadline_cancel_is_shouted_about(
    watcher, queued, journal, openalgo, openalgo_db, execution_settings, caplog
):
    import logging

    from freezegun import freeze_time

    approval_id, pending_order_id = queued
    approve_pending(openalgo_db, pending_order_id)
    _status_route(openalgo, status="open")
    watcher.poll_once()

    cancel = openalgo.post("/api/v1/cancelorder").mock(
        return_value=httpx.Response(
            403, json={"status": "error", "message": "not allowed in Semi-Auto mode"}
        )
    )
    later = datetime.now(tz=UTC) + timedelta(seconds=execution_settings.fill_deadline_seconds + 1)
    with freeze_time(later), caplog.at_level(logging.CRITICAL):
        watcher.poll_once()

    assert cancel.call_count == 1
    assert "cancel refused" in caplog.text
    assert journal.orders_for_approval(approval_id)[-1].order_status == "open"


def test_an_unreadable_mirror_settles_nothing(watcher, queued, journal, openalgo_db, today):
    """A gate you cannot see is not a gate you may guess about."""
    openalgo_db.unlink()
    assert watcher.poll_once() == 0
    assert [row.status for row in journal.list_approvals(today)] == ["pending"]


def test_an_unreadable_queue_past_the_deadline_is_shouted_about(
    watcher, queued, journal, openalgo_db, execution_settings, today, caplog
):
    """AC-13: settling blind would release the hold, so the escalation is the log line."""
    import logging

    from freezegun import freeze_time

    _, pending_order_id = queued
    openalgo_db.unlink()
    later = datetime.now(tz=UTC) + timedelta(
        seconds=execution_settings.approval_deadline_seconds + 1
    )
    with freeze_time(later), caplog.at_level(logging.CRITICAL):
        assert watcher.poll_once() == 0

    assert f"APPROVAL QUEUE UNREADABLE: pending order {pending_order_id}" in caplog.text
    assert [row.status for row in journal.list_approvals(today)] == ["pending"]
