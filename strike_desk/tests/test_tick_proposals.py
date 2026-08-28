"""Routing and classification through a real graph, with a stubbed strategist."""

from __future__ import annotations

import pytest

from strike_desk.decline_taxonomy import describe
from strike_desk.errors import JournalWriteError
from strike_desk.graph import (
    OUTCOME_DECLINE,
    REASON_DATA_QUALITY,
    REASON_NO_VIABLE_CONTRACT,
    REASON_PROPOSAL_INVALID,
    REASON_PROPOSAL_UNGROUNDED,
    REASON_SPECIALIST_UNAVAILABLE,
    _decide_outcome,
)
from strike_desk.options_strategist import (
    STATUS_DEGRADED,
    STATUS_INVALID,
    STATUS_NO_CONTRACT,
    STATUS_PROPOSED,
    STATUS_UNGROUNDED,
)


@pytest.mark.parametrize(
    ("status", "code", "category", "disposition"),
    [
        (STATUS_PROPOSED, REASON_SPECIALIST_UNAVAILABLE, "specialist", "degraded"),
        (STATUS_NO_CONTRACT, REASON_NO_VIABLE_CONTRACT, "contract", "routine"),
        (STATUS_UNGROUNDED, REASON_PROPOSAL_UNGROUNDED, "contract", "defect"),
        (STATUS_INVALID, REASON_PROPOSAL_INVALID, "contract", "defect"),
        (STATUS_DEGRADED, REASON_DATA_QUALITY, "data", "degraded"),
    ],
)
def test_each_status_classifies(settings, tick_state, status, code, category, disposition) -> None:
    state = {
        **tick_state,
        "regime_label": "trending",
        "regime_confidence": 0.8,
        "proposal_status": status,
        "proposal": {"symbol": "NIFTY02SEP2624800CE"},
    }
    outcome, reason_code, text = _decide_outcome(state, settings)
    assert outcome == OUTCOME_DECLINE
    assert reason_code == code
    entry = describe(reason_code)
    assert (entry.category, entry.disposition) == (category, disposition)
    assert text and len(text) <= settings.reason_text_max_chars


def test_a_refusal_never_makes_the_day_look_broken(settings, tick_state) -> None:
    """no-viable-contract is routine: `strike-desk declines` must still exit 0."""
    assert describe(REASON_NO_VIABLE_CONTRACT).disposition == "routine"


def test_a_non_directional_regime_never_consults(tick_harness) -> None:
    """AC-6: gating happens before spending, not after."""
    tick_harness.set_regime(label="range-bound", confidence=0.9)
    decision = tick_harness.run_tick()
    assert tick_harness.strategist_calls == 0
    assert decision.reason_code == REASON_NO_VIABLE_CONTRACT
    assert "not directional" in decision.reason_text
    assert tick_harness.proposal_rows() == []


def test_a_low_confidence_directional_regime_never_consults(tick_harness) -> None:
    tick_harness.set_regime(label="trending", confidence=0.2)
    tick_harness.run_tick()
    assert tick_harness.strategist_calls == 0


def test_the_proposal_row_is_written_before_the_decision(tick_harness) -> None:
    """A proposal that was not journalled did not happen."""
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=STATUS_PROPOSED, journal_fails=True)
    with pytest.raises(JournalWriteError):
        tick_harness.run_tick()
    assert tick_harness.decision_rows() == []
