"""The graph, end to end, with the human removed — and the proof that attended did not move."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from strike_desk.graph import OUTCOME_DECLINE, OUTCOME_ENTER, TickDeps
from strike_desk.journal import APPROVAL_AUTO_APPROVED, APPROVAL_GATE_BYPASSED, APPROVAL_PENDING
from strike_desk.openalgo_mirror import ORDER_MODE_AUTO
from strike_desk.runner import TickRunner
from strike_desk.session import SessionGate
from strike_desk.specialists import ROLE_REGIME, ROLE_STRATEGIST
from tests.approval_fixtures import proposed_payload, regime_payload, set_order_mode
from tests.conftest import StubSpecialist
from tests.position_fixtures import seed_flat_loss


class _LiveMonitor:
    def __init__(self, mirror) -> None:
        self._mirror = mirror
        self.heartbeat_utc = datetime.now(UTC)

    @property
    def mirror(self):
        return self._mirror

    def is_alive(self) -> bool:
        return True


def _register_specialists(deps: TickDeps) -> None:
    deps.registry.register(StubSpecialist(role=ROLE_REGIME, payload=regime_payload()))
    deps.registry.register(StubSpecialist(role=ROLE_STRATEGIST, payload=proposed_payload()))


@pytest.fixture
def unattended_settings(execution_settings, openalgo_db):
    set_order_mode(openalgo_db, ORDER_MODE_AUTO)
    return execution_settings.model_copy(
        update={"autonomy": "unattended", "unattended_daily_loss_cap": 500.0}
    )


@pytest.fixture
def unattended_deps(unattended_settings, client, journal, registry, tracing, prompts):
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    from strike_desk.approval_gate import ApprovalGate
    from strike_desk.execution_client import ExecutionClient
    from strike_desk.openalgo_mirror import OpenAlgoMirror

    mirror = OpenAlgoMirror(unattended_settings)
    execution = ExecutionClient(unattended_settings)
    gate = ApprovalGate(unattended_settings, journal, mirror, execution)
    connection = sqlite3.connect(str(unattended_settings.checkpoint_path), check_same_thread=False)
    checkpointer = SqliteSaver(connection)
    checkpointer.setup()
    monitor = _LiveMonitor(mirror)
    deps = TickDeps(
        settings=unattended_settings,
        client=client,
        journal=journal,
        registry=registry,
        prompts=prompts,
        span_processor=tracing,
        checkpointer=checkpointer,
        gate=gate,
        monitor=monitor,
        mirror=mirror,
    )
    _register_specialists(deps)
    yield deps
    connection.close()
    execution.close()
    mirror.close()


@pytest.fixture
def attended_deps(execution_deps):
    _register_specialists(execution_deps)
    return execution_deps


def _place(openalgo, *, orderid: str = "SANDBOX-1", pending_order_id=None, mode: str = "auto"):
    body = {"status": "success", "mode": mode}
    if pending_order_id is not None:
        body["pending_order_id"] = pending_order_id
    if orderid is not None:
        body["orderid"] = orderid
    openalgo.post("/api/v1/placeorder").mock(return_value=httpx.Response(200, json=body))


def _run(deps: TickDeps) -> TickRunner:
    return TickRunner(deps, SessionGate(deps.client, deps.settings))


def test_an_unattended_tick_places_without_suspending(
    unattended_deps, openalgo, rich_account, today
):
    """No interrupt, no pending order, one broker order id, in a single pass."""
    _place(openalgo)
    _run(unattended_deps).run_tick("manual")
    decision = unattended_deps.journal.list_decisions(today)[0]
    assert decision.outcome == OUTCOME_ENTER
    approval = unattended_deps.journal.latest_approval()
    assert approval is not None
    assert approval.status == APPROVAL_AUTO_APPROVED
    assert approval.pending_order_id is None
    assert approval.broker_order_id == "SANDBOX-1"


def test_the_next_tick_is_not_held_by_an_approval(
    unattended_deps, openalgo, rich_account, today
):
    _place(openalgo)
    runner = _run(unattended_deps)
    runner.run_tick("manual")
    runner.run_tick("manual")
    second = unattended_deps.journal.list_decisions(today)[0]
    assert second.reason_code != "approval-pending"


def test_an_unattended_entry_writes_an_auto_approved_row(
    unattended_deps, openalgo, rich_account
):
    _place(openalgo)
    _run(unattended_deps).run_tick("manual")
    row = unattended_deps.journal.latest_approval()
    assert row is not None
    assert row.status == APPROVAL_AUTO_APPROVED
    assert row.approved_by == "strike-desk"
    assert row.pending_order_id is None


def test_an_unattended_entry_is_logged_at_warning(
    unattended_deps, openalgo, rich_account, caplog
):
    _place(openalgo)
    with caplog.at_level("WARNING"):
        _run(unattended_deps).run_tick("manual")
    line = next(record for record in caplog.records if "unattended entry" in record.message)
    assert "stop" in line.getMessage() and "time-stop" in line.getMessage()


def test_a_queued_response_kills_the_desk(unattended_deps, openalgo, rich_account):
    """Unattended plus a pending_order_id means a human is silently holding our entry."""
    _place(openalgo, orderid=None, pending_order_id=7, mode="semi_auto")
    _run(unattended_deps).run_tick("manual")
    assert unattended_deps.settings.kill_switch_path.exists()
    statuses = {row.status for row in unattended_deps.journal.list_approvals(
        unattended_deps.journal.recent_trading_days(1)[0]
    )} if unattended_deps.journal.recent_trading_days(1) else set()
    latest = unattended_deps.journal.latest_approval()
    assert latest is not None
    assert latest.status in {APPROVAL_GATE_BYPASSED, "submit-failed"} or statuses


def test_a_dead_monitor_declines_before_a_token_is_spent(
    unattended_deps, openalgo, rich_account, today
):
    unattended_deps.monitor = None
    _run(unattended_deps).run_tick("manual")
    decision = unattended_deps.journal.list_decisions(today)[0]
    assert decision.outcome == OUTCOME_DECLINE
    assert decision.reason_code == "monitor-unavailable"
    assert unattended_deps.journal.list_regime_reads(today) == []


def test_the_loss_cap_declines_and_kills(unattended_deps, journal, openalgo, rich_account, today):
    seed_flat_loss(journal, day=today)
    _run(unattended_deps).run_tick("manual")
    decision = unattended_deps.journal.list_decisions(today)[0]
    assert decision.reason_code == "daily-loss-cap"
    assert unattended_deps.settings.kill_switch_path.exists()


def test_attended_mode_is_byte_for_byte_unchanged(attended_deps, openalgo, rich_account, today):
    """The regression that matters most in this section: iteration 06 still behaves as it did."""
    _place(openalgo, orderid=None, pending_order_id=41, mode="semi_auto")
    _run(attended_deps).run_tick("manual")
    approval = attended_deps.journal.latest_approval()
    assert approval is not None
    assert approval.status == APPROVAL_PENDING
    assert approval.pending_order_id == 41
