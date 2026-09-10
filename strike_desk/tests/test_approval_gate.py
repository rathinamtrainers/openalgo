"""Pricing, sizing, the preflight, the bypass, and settlement that runs twice safely."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from strike_desk.approval_gate import (
    WITHDRAWAL_NOT_QUEUED,
    Resolution,
    align_price,
    build_intent,
    settle_approval,
    strategy_tag,
)
from strike_desk.errors import InvalidOrderPayload, UnpriceableBand
from strike_desk.journal import (
    APPROVAL_APPROVED,
    APPROVAL_GATE_BYPASSED,
    APPROVAL_GATE_UNAVAILABLE,
    APPROVAL_PENDING,
    APPROVAL_SUBMIT_FAILED,
    APPROVAL_UNPRICEABLE,
)

from .approval_fixtures import (
    BAND_HIGH,
    BAND_LOW,
    cleared_context,
    cleared_proposal,
    set_order_mode,
)

QUEUED = {"status": "success", "mode": "semi_auto", "pending_order_id": 41}


def _queue_route(openalgo, body=None, status_code=200):
    return openalgo.post("/api/v1/placeorder").mock(
        return_value=httpx.Response(status_code, json=body if body is not None else QUEUED)
    )


@pytest.mark.parametrize(
    ("low", "high", "tick", "expected"),
    [
        (188.0, 192.0, 0.05, 192.0),
        (188.0, 192.03, 0.05, 192.0),
        (188.0, 192.049, 0.05, 192.0),
        (0.10, 0.14, 0.05, 0.10),
        (11.9, 12.0, 0.1, 12.0),
    ],
)
def test_the_limit_price_is_the_top_of_the_band_on_the_grid(low, high, tick, expected):
    """AC-4: floored to the tick, never above the band, exact in Decimal."""
    assert align_price(low, high, tick) == pytest.approx(expected)


@pytest.mark.parametrize(("low", "high"), [(192.01, 192.04), (192.06, 192.09), (192.0, 191.0)])
def test_a_band_with_no_tick_in_it_is_refused(low, high):
    with pytest.raises(UnpriceableBand):
        align_price(low, high, 0.05)


def test_the_quantity_comes_from_the_verdict_not_the_proposal(execution_settings):
    """AC-4: the model's own quantity is ignored; the cleared lot count is not."""
    context = cleared_context(lots=1, proposal=cleared_proposal(lots=2, quantity=150))
    intent = build_intent(context, execution_settings, datetime.now(tz=UTC))
    assert intent.lots == 1
    assert intent.quantity == intent.lot_size
    assert intent.payload()["quantity"] == intent.lot_size
    assert intent.payload()["pricetype"] == "LIMIT"
    assert intent.payload()["action"] == "BUY"
    assert intent.payload()["product"] == execution_settings.order_product
    assert intent.strategy == strategy_tag(execution_settings, context.tick_id)


def test_an_unusable_intent_raises_before_anything_is_built(execution_settings):
    with pytest.raises(InvalidOrderPayload):
        build_intent(cleared_context(lots=0), execution_settings, datetime.now(tz=UTC))


def test_a_queued_intent_is_journalled_once(gate, journal, openalgo, today):
    route = _queue_route(openalgo)
    result = gate.submit(cleared_context())

    assert route.call_count == 1
    assert result.status == APPROVAL_PENDING and result.pending_order_id == 41
    rows = journal.list_approvals(today)
    assert [row.status for row in rows] == [APPROVAL_PENDING]
    row = rows[0]
    assert row.pending_order_id == 41
    assert row.quantity == row.lots * row.lot_size
    assert row.limit_price == BAND_HIGH
    assert (row.band_low, row.band_high) == (BAND_LOW, BAND_HIGH)
    assert row.proposal_id == "proposal-0001" and row.verdict_id == "verdict-0001"
    assert row.defect is False


def test_auto_mode_stops_the_submission_before_the_wire(
    gate, journal, openalgo, openalgo_db, today
):
    """AC-2: the preflight is a read, and a failed read means nothing is sent."""
    route = _queue_route(openalgo)
    set_order_mode(openalgo_db, "auto")

    result = gate.submit(cleared_context())

    assert route.call_count == 0
    assert result.status == APPROVAL_GATE_UNAVAILABLE
    row = journal.list_approvals(today)[0]
    assert row.status == APPROVAL_GATE_UNAVAILABLE and row.defect is True


def test_a_bypassed_gate_engages_the_kill_switch(
    gate, journal, openalgo, execution_settings, today
):
    """AC-3: an order that reached a broker without a click stops the desk."""
    _queue_route(openalgo, {"status": "success", "orderid": "24090100000041"})
    cancel = openalgo.post("/api/v1/cancelorder").mock(
        return_value=httpx.Response(
            403, json={"status": "error", "message": "not allowed in Semi-Auto mode"}
        )
    )

    result = gate.submit(cleared_context())

    assert result.status == APPROVAL_GATE_BYPASSED
    assert execution_settings.kill_switch_path.exists()
    assert "bypassed" in execution_settings.kill_switch_path.read_text(encoding="utf-8")
    row = journal.list_approvals(today)[0]
    assert row.status == APPROVAL_GATE_BYPASSED and row.defect is True
    assert row.broker_order_id == "24090100000041"
    assert cancel.call_count == 1


def test_a_failed_submission_is_journalled_not_retried(gate, journal, openalgo, today):
    route = _queue_route(openalgo, {"status": "error", "message": "broker session expired"})
    result = gate.submit(cleared_context())
    assert route.call_count == 1
    assert result.status == APPROVAL_SUBMIT_FAILED
    assert journal.list_approvals(today)[0].detail.endswith("broker session expired'")


def test_an_unpriceable_band_never_reaches_the_wire(gate, journal, openalgo, today):
    route = _queue_route(openalgo)
    result = gate.submit(
        cleared_context(proposal=cleared_proposal(entry_price_low=192.01, entry_price_high=192.04))
    )
    assert route.call_count == 0
    assert result.status == APPROVAL_UNPRICEABLE
    assert journal.list_approvals(today)[0].defect is True


def test_settlement_is_idempotent(gate, journal, openalgo, today):
    """AC-11: the second write is refused by the schema and read as already-settled."""
    _queue_route(openalgo)
    submitted = gate.submit(cleared_context())
    resolution = Resolution(
        approval_id=submitted.approval_id,
        tick_id="tick-0001",
        status=APPROVAL_APPROVED,
        detail="approved by amit",
        approved_by="amit",
        broker_order_id="24090100000041",
        order_status="open",
        wait_seconds=12.0,
    )

    assert settle_approval(journal, resolution, "1" * 32) is True
    assert settle_approval(journal, resolution, "1" * 32) is False

    rows = journal.list_approvals(today)
    assert [row.status for row in rows] == [APPROVAL_PENDING, APPROVAL_APPROVED]
    assert len(journal.orders_for_approval(submitted.approval_id)) == 1


def test_settling_an_unknown_approval_is_a_no_op(journal):
    assert (
        settle_approval(
            journal,
            Resolution(approval_id="nope", tick_id="t", status=APPROVAL_APPROVED),
            "1" * 32,
        )
        is False
    )


def test_a_rejection_writes_no_order_row(gate, journal, openalgo, today):
    _queue_route(openalgo)
    submitted = gate.submit(cleared_context())
    settle_approval(
        journal,
        Resolution(
            approval_id=submitted.approval_id,
            tick_id="tick-0001",
            status="rejected",
            detail="rejected by amit: strike too far OTM",
            approved_by="amit",
            withdrawal=WITHDRAWAL_NOT_QUEUED,
        ),
        "1" * 32,
    )
    assert journal.orders_for_approval(submitted.approval_id) == []
