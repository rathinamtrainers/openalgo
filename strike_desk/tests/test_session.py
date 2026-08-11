"""Session gate: hours, windows, expiry, kill switch."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from freezegun import freeze_time

from strike_desk.config import IST
from strike_desk.errors import OpenAlgoError
from strike_desk.session import (
    CALENDAR_UNAVAILABLE,
    EXPIRY_CUTOFF,
    KILL_SWITCH,
    MARKET_CLOSED,
    NO_TRADE_WINDOW,
    SessionGate,
)
from tests.conftest import epoch_ms


class FakeClient:
    """Returns NFO timings for open days and nothing for closed ones."""

    def __init__(self, open_days: set[date], fail: bool = False) -> None:
        self.open_days = open_days
        self.fail = fail
        self.calls: list[date] = []

    def market_timings(self, day: date):
        self.calls.append(day)
        if self.fail:
            raise OpenAlgoError("/api/v1/market/timings: transport failure")
        if day not in self.open_days:
            return []
        return [
            {
                "exchange": "NFO",
                "start_time": epoch_ms(day, time(9, 15)),
                "end_time": epoch_ms(day, time(15, 30)),
            }
        ]


WEDNESDAY = date(2026, 7, 22)
TUESDAY = date(2026, 7, 21)
MONDAY = date(2026, 7, 20)


def gate_for(settings, open_days, fail=False):
    settings.no_trade_windows = "09:15-09:30,15:15-15:30"
    settings.expiry_cutoff = "14:00"
    return SessionGate(FakeClient(open_days, fail), settings), settings


@pytest.mark.parametrize(
    ("clock", "blocked_by"),
    [
        ("2026-07-22 09:14:59+05:30", MARKET_CLOSED),
        ("2026-07-22 09:15:00+05:30", NO_TRADE_WINDOW),
        ("2026-07-22 09:29:59+05:30", NO_TRADE_WINDOW),
        ("2026-07-22 09:30:00+05:30", None),
        ("2026-07-22 15:14:59+05:30", None),
        ("2026-07-22 15:15:00+05:30", NO_TRADE_WINDOW),
        ("2026-07-22 15:30:01+05:30", MARKET_CLOSED),
    ],
)
def test_window_boundaries(settings, clock, blocked_by):
    gate, settings = gate_for(settings, {WEDNESDAY})
    # Keep expiry out of the way — this case is only about market / no-trade windows.
    settings.expiry_cutoff = "23:59"
    with freeze_time(clock):
        verdict = gate.evaluate(datetime.now(tz=IST))
    assert verdict.allowed is (blocked_by is None)
    assert verdict.blocked_by == blocked_by


def test_holiday_is_market_closed(settings):
    gate, _ = gate_for(settings, set())
    with freeze_time("2026-07-22 11:00:00+05:30"):
        verdict = gate.evaluate(datetime.now(tz=IST))
    assert verdict.blocked_by == MARKET_CLOSED


def test_kill_switch_wins_over_every_other_gate(settings):
    gate, settings = gate_for(settings, {WEDNESDAY})
    settings.kill_switch_path.write_text("2026-07-22T05:30:00+00:00 manual test")
    with freeze_time("2026-07-22 11:00:00+05:30"):
        verdict = gate.evaluate(datetime.now(tz=IST))
    assert verdict.blocked_by == KILL_SWITCH
    assert "manual test" in verdict.detail


def test_expiry_cutoff_blocks_after_the_configured_time(settings):
    gate, settings = gate_for(settings, {TUESDAY})
    settings.expiry_weekday = TUESDAY.weekday()
    with freeze_time("2026-07-21 13:59:00+05:30"):
        assert gate.evaluate(datetime.now(tz=IST)).allowed is True
    with freeze_time("2026-07-21 14:00:00+05:30"):
        assert gate.evaluate(datetime.now(tz=IST)).blocked_by == EXPIRY_CUTOFF


def test_expiry_walks_back_over_a_holiday(settings):
    """When the expiry weekday is a holiday, expiry is the previous trading day."""
    gate, settings = gate_for(settings, {MONDAY})  # Tuesday closed
    settings.expiry_weekday = TUESDAY.weekday()
    assert gate.expiry_date_for(MONDAY) == MONDAY


def test_calendar_failure_blocks_rather_than_trades(settings):
    gate, _ = gate_for(settings, {WEDNESDAY}, fail=True)
    with freeze_time("2026-07-22 11:00:00+05:30"):
        verdict = gate.evaluate(datetime.now(tz=IST))
    assert verdict.allowed is False
    assert verdict.blocked_by == CALENDAR_UNAVAILABLE


def test_timings_are_cached_per_day(settings):
    gate, _ = gate_for(settings, {WEDNESDAY})
    client = gate._client  # noqa: SLF001
    with freeze_time("2026-07-22 11:00:00+05:30"):
        gate.evaluate(datetime.now(tz=IST))
        first = len(client.calls)
        gate.evaluate(datetime.now(tz=IST))
    assert len(client.calls) == first
