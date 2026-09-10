"""From a cleared intent to a settled approval, through the real graph."""

from __future__ import annotations

import httpx
import pytest

from strike_desk.approval_watcher import ApprovalWatcher
from strike_desk.graph import (
    OUTCOME_DECLINE,
    OUTCOME_ENTER,
    OUTCOME_HOLD,
    REASON_APPROVAL_GATE_UNAVAILABLE,
    REASON_APPROVAL_PENDING,
)
from strike_desk.journal import APPROVAL_APPROVED, APPROVAL_PENDING
from strike_desk.specialists import ROLE_REGIME, ROLE_STRATEGIST

from .approval_fixtures import (
    USER,
    approve_pending,
    proposed_payload,
    queue_pending_order,
    regime_payload,
    set_order_mode,
)
from .conftest import StubSpecialist


@pytest.fixture
def entering_desk(execution_runner, execution_deps, registry, openalgo, openalgo_db, rich_account):
    """A desk whose specialists always produce the recorded contract, and a queue to put it in."""
    registry.register(StubSpecialist(role=ROLE_REGIME, payload=regime_payload()))
    registry.register(StubSpecialist(role=ROLE_STRATEGIST, payload=proposed_payload()))
    pending_order_id = queue_pending_order(openalgo_db)
    openalgo.post("/api/v1/placeorder").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "mode": "semi_auto", "pending_order_id": pending_order_id},
        )
    )
    openalgo.post("/api/v1/orderstatus").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"order_status": "open", "average_price": 0.0, "quantity": 150},
            },
        )
    )
    return execution_runner, pending_order_id


def test_an_enter_reaches_the_queue_after_its_decision_row(entering_desk, journal, today):
    """AC-1 and AC-12: the decision is journalled first, and the approval names it."""
    runner, pending_order_id = entering_desk

    tick_id = runner.run_tick("manual")

    decision = journal.list_decisions(today)[0]
    assert decision.outcome == OUTCOME_ENTER and decision.tick_id == tick_id
    approval = journal.list_approvals(today)[0]
    assert approval.status == APPROVAL_PENDING
    assert approval.tick_id == tick_id
    assert approval.pending_order_id == pending_order_id
    assert approval.id > 0 and approval.created_at_utc >= decision.created_at_utc
    assert approval.verdict_id and journal.risk_verdict(approval.verdict_id) is not None
    assert approval.proposal_id and journal.proposal(approval.proposal_id) is not None


def test_the_next_tick_holds_while_the_intent_waits(entering_desk, journal, today):
    """AC-10: no specialist, no token, no second intent."""
    runner, _ = entering_desk
    runner.run_tick("manual")
    reads_before = len(journal.list_regime_reads(today))

    runner.run_tick("manual")

    decisions = journal.list_decisions(today)
    assert decisions[0].outcome == OUTCOME_HOLD
    assert decisions[0].reason_code == REASON_APPROVAL_PENDING
    assert decisions[0].token_cost_micros == 0
    assert len(journal.list_regime_reads(today)) == reads_before
    assert len(journal.list_approvals(today)) == 1


def test_the_graph_settles_its_own_approval(
    entering_desk, journal, execution_settings, mirror, execution_client, openalgo_db, today
):
    """AC-6 and AC-14: the watcher resumes the suspended tick, which writes the terminal rows."""
    runner, pending_order_id = entering_desk
    runner.run_tick("manual")
    approve_pending(openalgo_db, pending_order_id, by=USER)

    watcher = ApprovalWatcher(
        execution_settings, journal, mirror, execution_client, runner.resume_approval
    )
    assert watcher.poll_once() == 1

    rows = {row.status: row for row in journal.list_approvals(today)}
    assert set(rows) == {APPROVAL_PENDING, APPROVAL_APPROVED}
    assert rows[APPROVAL_APPROVED].approved_by == USER
    approval_id = rows[APPROVAL_APPROVED].approval_id
    assert len(journal.orders_for_approval(approval_id)) == 1

    # A second poll finds nothing outstanding and settles nothing twice.
    assert watcher.poll_once() == 0
    assert len(journal.list_approvals(today)) == 2


def test_a_stale_intent_holds_as_a_defect(entering_desk, journal, execution_settings, today):
    """AC-13: past its deadline and still unsettled is a defect, not a routine hold."""
    from datetime import UTC, datetime, timedelta

    from freezegun import freeze_time

    from strike_desk.decline_taxonomy import describe
    from strike_desk.graph import REASON_APPROVAL_QUEUE_STALE

    runner, _ = entering_desk
    runner.run_tick("manual")

    later = datetime.now(tz=UTC) + timedelta(
        seconds=execution_settings.approval_deadline_seconds + 1
    )
    with freeze_time(later):
        runner.run_tick("manual")

    decision = journal.list_decisions(today)[0]
    assert decision.outcome == OUTCOME_HOLD
    assert decision.reason_code == REASON_APPROVAL_QUEUE_STALE
    assert describe(REASON_APPROVAL_QUEUE_STALE).disposition == "defect"


def test_a_submission_that_raises_does_not_poison_the_tick(
    entering_desk, execution_deps, journal, today, monkeypatch
):
    """One decision row, one terminal approval row, and no false journal-unwritable alarm."""
    from strike_desk.errors import JournalWriteError
    from strike_desk.journal import APPROVAL_SUBMIT_FAILED

    runner, _ = entering_desk

    def _explode(_context):
        raise JournalWriteError("disk full")

    monkeypatch.setattr(execution_deps.gate, "submit", _explode)

    tick_id = runner.run_tick("manual")

    decisions = journal.list_decisions(today)
    assert [d.tick_id for d in decisions] == [tick_id]
    assert decisions[0].outcome == OUTCOME_ENTER
    assert [row.status for row in journal.list_approvals(today)] == [APPROVAL_SUBMIT_FAILED]


def test_auto_mode_declines_before_a_token_is_spent(entering_desk, journal, openalgo_db, today):
    """AC-2: the gate check lives in plan, so a broken gate costs nothing."""
    runner, _ = entering_desk
    set_order_mode(openalgo_db, "auto")

    runner.run_tick("manual")

    decision = journal.list_decisions(today)[0]
    assert decision.outcome == OUTCOME_DECLINE
    assert decision.reason_code == REASON_APPROVAL_GATE_UNAVAILABLE
    assert journal.list_regime_reads(today) == []
    assert journal.list_approvals(today) == []


def test_execution_disabled_stops_at_the_intent(
    entering_desk, execution_deps, journal, today, monkeypatch
):
    """The iteration-05 posture is one setting away and still reachable."""
    runner, _ = entering_desk
    monkeypatch.setattr(execution_deps.settings, "execution_enabled", False)

    runner.run_tick("manual")

    assert journal.list_decisions(today)[0].outcome == OUTCOME_ENTER
    assert journal.list_approvals(today) == []


def test_the_trace_carries_the_submission(entering_desk, journal, today):
    """AC-15: the submission is on the tick's own trace, with its numbers on it."""
    runner, pending_order_id = entering_desk
    tick_id = runner.run_tick("manual")

    decision = journal.list_decisions(today)[0]
    assert decision.tick_id == tick_id
    spans = {span.name: span for span in journal.spans_for_trace(decision.trace_id)}
    assert "tick.submit" in spans
    attributes = spans["tick.submit"].attributes_json
    assert '"approval.status": "pending"' in attributes
    assert f'"approval.pending_order_id": {pending_order_id}' in attributes
    assert '"approval.limit_price": 192.0' in attributes
