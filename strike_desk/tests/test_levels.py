"""The exit evaluator: every branch, every boundary, every precedence rule."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from strike_desk.config import IST
from strike_desk.errors import LevelsUnavailable
from strike_desk.levels import ExitLevels, distances, evaluate, resolve_time_stop

NOW = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)


def build(stop: float = 80.0, target: float = 140.0, minutes: float = 30.0) -> ExitLevels:
    return ExitLevels(
        stop_price=stop,
        target_price=target,
        time_stop_utc=NOW + timedelta(minutes=minutes),
        time_stop_reason="time-stop",
    )


def test_between_levels_holds():
    assert evaluate(build(), 110.0, NOW) is None


@pytest.mark.parametrize("price", [80.0, 79.99, 0.05])
def test_stop_breach_at_or_below(price):
    trigger = evaluate(build(), price, NOW)
    assert trigger is not None
    assert trigger.reason == "stop"
    assert trigger.level_price == 80.0
    assert trigger.observed_price == price


@pytest.mark.parametrize("price", [140.0, 140.01, 1000.0])
def test_target_breach_at_or_above(price):
    trigger = evaluate(build(), price, NOW)
    assert trigger is not None and trigger.reason == "target"


def test_time_stop_fires_regardless_of_price():
    levels = build(minutes=-1)
    trigger = evaluate(levels, 110.0, NOW)
    assert trigger is not None and trigger.reason == "time-stop"


def test_time_beats_price():
    levels = build(minutes=-1)
    trigger = evaluate(levels, 10.0, NOW)
    assert trigger.reason == "time-stop"


def test_stop_beats_target_when_both_breached():
    levels = ExitLevels(
        stop_price=100.0,
        target_price=100.0001,
        time_stop_utc=NOW + timedelta(minutes=30),
        time_stop_reason="time-stop",
    )
    assert evaluate(levels, 100.0, NOW).reason == "stop"


def test_missing_price_still_honours_the_clock():
    assert evaluate(build(), None, NOW) is None
    assert evaluate(build(minutes=-1), None, NOW).reason == "time-stop"


@pytest.mark.parametrize(
    "stop,target",
    [(0.0, 140.0), (-1.0, 140.0), (140.0, 80.0), (100.0, 100.0)],
)
def test_impossible_levels_are_refused(stop, target):
    with pytest.raises(LevelsUnavailable):
        ExitLevels(stop, target, NOW + timedelta(minutes=5), "time-stop")


def test_naive_time_stop_is_refused():
    with pytest.raises(LevelsUnavailable):
        ExitLevels(80.0, 140.0, datetime(2026, 9, 8, 12, 0), "time-stop")


def test_unknown_time_stop_reason_is_refused():
    with pytest.raises(LevelsUnavailable):
        ExitLevels(80.0, 140.0, NOW, "vibes")


def test_resolve_time_stop_takes_the_earlier_clock():
    day = date(2026, 9, 8)
    stamp, reason = resolve_time_stop("15:40", time(15, 10), day)
    assert reason == "session-deadline"
    assert stamp.astimezone(IST).strftime("%H:%M") == "15:10"

    stamp, reason = resolve_time_stop("14:20", time(15, 10), day)
    assert reason == "time-stop"
    assert stamp.astimezone(IST).strftime("%H:%M") == "14:20"


def test_resolve_time_stop_without_a_proposal_time():
    stamp, reason = resolve_time_stop(None, time(15, 10), date(2026, 9, 8))
    assert reason == "session-deadline"
    assert stamp.astimezone(IST).strftime("%H:%M") == "15:10"


def test_resolve_time_stop_rejects_garbage():
    with pytest.raises(LevelsUnavailable):
        resolve_time_stop("half past three", time(15, 10), date(2026, 9, 8))


def test_distances_are_signed_from_the_price():
    gaps = distances(build(), 110.0)
    assert gaps == {"to_stop": 30.0, "to_target": 30.0}
    assert distances(build(), None) == {"to_stop": None, "to_target": None}
