"""Frozen decision-table regression gate. A changed verdict must change this file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from strike_desk.graph import _decide_outcome

SUITE = json.loads((Path(__file__).parent / "tick_scenarios.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("scenario", SUITE["scenarios"], ids=lambda s: s["id"])
def test_frozen_scenario(settings, scenario):
    settings.min_regime_confidence = SUITE["min_regime_confidence"]
    outcome, reason_code, reason_text = _decide_outcome(scenario["state"], settings)
    assert outcome == scenario["expect"]["outcome"], scenario["note"]
    assert reason_code == scenario["expect"]["reason_code"], scenario["note"]
    assert reason_text.strip(), "every verdict must carry a human-readable sentence"


def test_no_scenario_permits_an_entry(settings):
    for scenario in SUITE["scenarios"]:
        assert scenario["expect"]["outcome"] != "enter"


def test_every_reason_code_is_covered_by_the_suite():
    """The frozen suite must exercise every reason code the table can produce."""
    from strike_desk import graph

    reachable = {
        value
        for name, value in vars(graph).items()
        if name.startswith("REASON_") and name != "REASON_INTERNAL_ERROR"
    }
    covered = {scenario["expect"]["reason_code"] for scenario in SUITE["scenarios"]}
    assert reachable <= covered, f"uncovered reason codes: {sorted(reachable - covered)}"
