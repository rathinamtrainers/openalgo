"""The frozen risk regression set: scenario -> verdict -> limit -> the trader's sentence."""

from __future__ import annotations

from typing import Any

import pytest

from strike_desk.graph import (
    OUTCOME_DECLINE,
    OUTCOME_ENTER,
    REASON_RISK_CLEARED,
    REASON_RISK_INPUT_UNAVAILABLE,
    REASON_RISK_VETO,
    _decide_outcome,
)
from strike_desk.playbook import Playbook
from strike_desk.risk_officer import RiskLimits, adjudicate

from .chain_fixtures import FIXED_NOW, valid_proposal
from .risk_fixtures import REDUCES, ROOMY, VETOES, book_with, position, two_lots

#: Each case: what the book and the proposal are, and exactly what the desk must answer.
SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "clears-at-full-size",
        "capital": ROOMY,
        "lots": 2,
        "entries_today": 0,
        "positions": (),
        "verdict": "pass",
        "limit": None,
        "outcome": OUTCOME_ENTER,
        "code": REASON_RISK_CLEARED,
        "sentence": (
            "Intent: buy 2 lot(s) of NIFTY02SEP2624800CE at up to 192.00, risking Rs 6,000 "
            "to the 152.00 stop against a Rs 1,500,000 capital base. This is an intent, not "
            "an order."
        ),
    },
    {
        "id": "reduced-to-fit-the-per-trade-cap",
        "capital": REDUCES,
        "lots": 2,
        "entries_today": 0,
        "positions": (),
        "verdict": "reduce",
        "limit": "per-trade-loss-cap",
        "outcome": OUTCOME_ENTER,
        "code": REASON_RISK_CLEARED,
        "sentence": (
            "Intent: buy 1 lot(s) of NIFTY02SEP2624800CE at up to 192.00, cut from 2 lot(s) "
            "to fit the per-trade-loss-cap limit of Rs 4,000. Risking Rs 3,000 to the 152.00 "
            "stop. This is an intent, not an order."
        ),
    },
    {
        "id": "no-size-clears",
        "capital": VETOES,
        "lots": 2,
        "entries_today": 0,
        "positions": (),
        "verdict": "veto",
        "limit": "per-trade-loss-cap",
        "outcome": OUTCOME_DECLINE,
        "code": REASON_RISK_VETO,
        "sentence": (
            "Declined: even one lot of NIFTY02SEP2624800CE trips the per-trade-loss-cap "
            "limit - configured Rs 2,500, observed Rs 3,000. There is no size this desk may "
            "take."
        ),
    },
    {
        "id": "the-days-fourth-trade",
        "capital": ROOMY,
        "lots": 2,
        "entries_today": 3,
        "positions": (),
        "verdict": "veto",
        "limit": "max-trades-per-day",
        "outcome": OUTCOME_DECLINE,
        "code": REASON_RISK_VETO,
        "sentence": (
            "Declined: NIFTY02SEP2624800CE trips the max-trades-per-day limit - configured "
            "3, observed 4. The veto is arithmetic and is not negotiated."
        ),
    },
    {
        "id": "a-position-is-already-open",
        "capital": ROOMY,
        "lots": 2,
        "entries_today": 0,
        "positions": (position(),),
        "verdict": "veto",
        "limit": "max-concurrent-positions",
        "outcome": OUTCOME_DECLINE,
        "code": REASON_RISK_VETO,
        "sentence": (
            "Declined: NIFTY02SEP2624800CE trips the max-concurrent-positions limit - "
            "configured 1, observed 2. The veto is arithmetic and is not negotiated."
        ),
    },
    {
        "id": "the-capital-base-is-unreadable",
        "capital": 0.0,
        "lots": 2,
        "entries_today": 0,
        "positions": (),
        "verdict": "hold",
        "limit": "capital-base",
        "outcome": OUTCOME_DECLINE,
        "code": REASON_RISK_INPUT_UNAVAILABLE,
        "sentence": (
            "Declined: the capital-base limit could not be evaluated (capital base Rs 0 is "
            "at or below the Rs 50,000 floor). A proposal is held, never assumed safe."
        ),
    },
)


def _run(case: dict[str, Any], settings: Any) -> tuple[Any, tuple[str, str, str]]:
    proposal = two_lots() if case["lots"] == 2 else valid_proposal()
    verdict = adjudicate(
        proposal,
        book_with(capital=case["capital"], positions=case["positions"]),
        RiskLimits.from_settings(settings),
        Playbook.from_settings(settings),
        now_ist=FIXED_NOW,
        entries_today=case["entries_today"],
    )
    state = {
        "regime_label": "trending",
        "regime_confidence": 0.8,
        "proposal_status": "proposed",
        "proposal": proposal.model_dump(),
        "risk": verdict.as_dict(),
    }
    return verdict, _decide_outcome(state, settings)


@pytest.mark.parametrize("case", SCENARIOS, ids=lambda case: case["id"])
def test_the_frozen_scenario_produces_its_pinned_verdict(case, settings) -> None:
    verdict, _ = _run(case, settings)
    assert verdict.verdict == case["verdict"]
    assert (verdict.tripped.limit if verdict.tripped else None) == case["limit"]


@pytest.mark.parametrize("case", SCENARIOS, ids=lambda case: case["id"])
def test_the_frozen_scenario_reads_the_way_it_is_pinned(case, settings) -> None:
    """A wording change is a deliberate change, made in this file or not at all."""
    _, (outcome, code, text) = _run(case, settings)
    assert (outcome, code) == (case["outcome"], case["code"])
    assert text == case["sentence"]
    assert len(text) <= settings.reason_text_max_chars


def test_every_clearing_scenario_says_it_is_not_an_order(settings) -> None:
    for case in SCENARIOS:
        if case["outcome"] == OUTCOME_ENTER:
            assert "not an order" in case["sentence"]
