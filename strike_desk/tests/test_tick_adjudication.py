"""Routing, latching, journal ordering and spans through a real graph."""

from __future__ import annotations

import inspect

import pytest

from strike_desk.errors import JournalWriteError
from strike_desk.graph import (
    OUTCOME_DECLINE,
    OUTCOME_ENTER,
    REASON_RISK_CLEARED,
    REASON_RISK_INPUT_UNAVAILABLE,
    REASON_RISK_SESSION_STOPPED,
    REASON_RISK_VETO,
    _decide_outcome,
)
from strike_desk.options_strategist import (
    STATUS_DEGRADED,
    STATUS_NO_CONTRACT,
    STATUS_PROPOSED,
)

from .risk_fixtures import REDUCES, ROOMY, VETOES


def test_a_cleared_proposal_becomes_an_intent(tick_harness) -> None:
    """AC-8, behaviourally."""
    tick_harness.set_book(capital=ROOMY)
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=STATUS_PROPOSED)
    decision = tick_harness.run_tick()
    assert decision.outcome == OUTCOME_ENTER
    assert decision.reason_code == REASON_RISK_CLEARED
    assert len(tick_harness.verdict_rows()) == 1
    assert tick_harness.verdict_rows()[0].verdict == "pass"


def test_a_reduced_intent_records_both_sizes(tick_harness) -> None:
    tick_harness.set_book(capital=REDUCES)
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=STATUS_PROPOSED, lots=2)
    decision = tick_harness.run_tick()
    row = tick_harness.verdict_rows()[0]
    assert decision.outcome == OUTCOME_ENTER
    assert (row.lots_requested, row.lots_cleared) == (2, 1)
    assert row.tripped_limit == "per-trade-loss-cap"
    assert "cut from 2 lot(s)" in decision.reason_text


def test_a_veto_declines_and_names_the_limit(tick_harness) -> None:
    tick_harness.set_book(capital=VETOES)
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=STATUS_PROPOSED, lots=2)
    decision = tick_harness.run_tick()
    assert (decision.outcome, decision.reason_code) == (OUTCOME_DECLINE, REASON_RISK_VETO)
    assert tick_harness.verdict_rows()[0].verdict == "veto"


def test_an_unreadable_capital_base_holds_the_proposal(tick_harness) -> None:
    tick_harness.set_book(capital=0.0)
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=STATUS_PROPOSED)
    decision = tick_harness.run_tick()
    assert decision.reason_code == REASON_RISK_INPUT_UNAVAILABLE
    assert decision.outcome == OUTCOME_DECLINE


@pytest.mark.parametrize("status", [STATUS_NO_CONTRACT, STATUS_DEGRADED])
def test_only_a_proposed_contract_is_adjudicated(status, tick_harness) -> None:
    tick_harness.set_book(capital=ROOMY)
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=status)
    decision = tick_harness.run_tick()
    assert decision.outcome == OUTCOME_DECLINE
    assert tick_harness.verdict_rows() == []


def test_a_stopped_session_never_consults_a_specialist(tick_harness) -> None:
    """AC-6: the gate is before the spend, not after it."""
    tick_harness.set_book(capital=ROOMY, realised=-40_000.0)
    decision = tick_harness.run_tick()
    assert decision.reason_code == REASON_RISK_SESSION_STOPPED
    assert (tick_harness.analyst_calls, tick_harness.strategist_calls) == (0, 0)
    assert decision.token_cost_micros == 0
    assert tick_harness.proposal_rows() == []


def test_the_session_stop_is_latched_once_a_day(tick_harness) -> None:
    tick_harness.set_book(capital=ROOMY, realised=-40_000.0)
    tick_harness.run_tick()
    tick_harness.run_tick()
    latched = [row for row in tick_harness.verdict_rows() if row.session_stop]
    assert len(latched) == 1
    assert len(tick_harness.decision_rows()) == 2


def test_the_latch_survives_a_recovered_book(tick_harness) -> None:
    """A session stop is for the session; it is not re-argued every fifteen minutes."""
    tick_harness.set_book(capital=ROOMY, realised=-40_000.0)
    tick_harness.run_tick()
    tick_harness.set_book(capital=ROOMY, realised=5_000.0)
    decision = tick_harness.run_tick()
    assert decision.reason_code == REASON_RISK_SESSION_STOPPED
    assert tick_harness.analyst_calls == 0


def test_an_open_position_still_holds_rather_than_stopping(tick_harness) -> None:
    """The stop takes the desk out of new entries; managing the book comes first."""
    from .risk_fixtures import position

    tick_harness.set_book(capital=ROOMY, realised=-40_000.0, positions=(position(),))
    decision = tick_harness.run_tick()
    assert decision.outcome == "hold"
    assert decision.reason_code == "position-open"


def test_the_verdict_row_is_written_before_the_decision(tick_harness, monkeypatch) -> None:
    """AC-1. An intent that was not journalled did not happen."""

    def boom(**_: object) -> str:
        raise JournalWriteError("forced")

    tick_harness.set_book(capital=ROOMY)
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=STATUS_PROPOSED)
    monkeypatch.setattr(tick_harness.journal, "record_decision", boom)
    with pytest.raises(JournalWriteError):
        tick_harness.run_tick()
    assert len(tick_harness.verdict_rows()) == 1
    assert tick_harness.decision_rows() == []


def test_enter_requires_a_cleared_verdict(tick_harness) -> None:
    """AC-8, structurally. Replaces iteration 04's test_enter_is_unreachable."""
    source = inspect.getsource(_decide_outcome)
    head, _, tail = source.partition('risk["verdict"] == VERDICT_VETO')
    assert "OUTCOME_ENTER" not in head, "an enter branch sits before the veto check"
    assert tail.count("OUTCOME_ENTER") == 1, "exactly one branch may return an intent"


def test_no_verdict_at_all_still_declines(settings, tick_state) -> None:
    """Fail closed: an unadjudicated proposal is never an intent."""
    state = {
        **tick_state,
        "regime_label": "trending",
        "regime_confidence": 0.8,
        "proposal_status": STATUS_PROPOSED,
        "proposal": {"symbol": "NIFTY02SEP2624800CE"},
    }
    outcome, code, text = _decide_outcome(state, settings)
    assert outcome == OUTCOME_DECLINE
    assert "'risk'" in text


def test_the_spans_carry_what_ac12_requires(tick_harness, journal) -> None:
    tick_harness.set_book(capital=REDUCES)
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=STATUS_PROPOSED, lots=2)
    decision = tick_harness.run_tick()
    spans = {span.name: span for span in journal.spans_for_trace(decision.trace_id)}
    assert {"risk.session", "tick.adjudicate"} <= set(spans)
    adjudicate = spans["tick.adjudicate"].attributes_json
    for attribute in (
        "risk.verdict",
        "risk.limit",
        "risk.configured",
        "risk.observed",
        "risk.capital_base",
        "risk.lots_requested",
        "risk.lots_cleared",
        "risk.latency_us",
        "risk.limits_artifact",
    ):
        assert attribute in adjudicate


def test_no_write_path_is_ever_touched(tick_harness) -> None:
    """AC-8. An intent is not an order, and nothing here can become one."""
    tick_harness.set_book(capital=ROOMY)
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=STATUS_PROPOSED)
    tick_harness.run_tick()
    assert tick_harness.requested_paths() <= {
        "/api/v1/funds",
        "/api/v1/positionbook",
        "/api/v1/market/timings",
        "/api/v1/ping",
    }
