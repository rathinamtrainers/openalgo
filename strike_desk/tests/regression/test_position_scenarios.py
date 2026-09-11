"""Replay the frozen scenarios through the real evaluator."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from strike_desk.levels import ExitLevels, evaluate

SCENARIOS = json.loads((Path(__file__).parent / "position_scenarios.json").read_text("utf-8"))
START = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)


@pytest.mark.parametrize("case", SCENARIOS, ids=[case["name"] for case in SCENARIOS])
def test_scenario(case):
    levels = ExitLevels(
        stop_price=case["stop"],
        target_price=case["target"],
        time_stop_utc=START + timedelta(minutes=case["minutes_to_time_stop"]),
        time_stop_reason="time-stop",
    )
    fired = None
    for index, (price, offset) in enumerate(case["observations"]):
        trigger = evaluate(levels, price, START + timedelta(seconds=offset))
        if trigger is not None:
            fired = (index, trigger)
            break

    expected = case["expect"]
    if expected is None:
        assert fired is None, f"{case['name']} should have held, fired {fired}"
        return
    assert fired is not None, f"{case['name']} should have exited and did not"
    index, trigger = fired
    assert index == expected["at_index"]
    assert trigger.reason == expected["reason"]
