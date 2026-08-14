"""The event calendar: absent, valid, active, and malformed."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from strike_desk.config import IST
from strike_desk.errors import EventCalendarInvalid
from strike_desk.events import active_window, load_event_windows


def write(path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_absent_calendar_is_no_windows(tmp_path):
    assert load_event_windows(tmp_path / "events.json") == ()


def test_windows_are_parsed_and_ordered(tmp_path):
    path = tmp_path / "events.json"
    write(
        path,
        [
            {"name": "US CPI", "start": "2026-08-19T17:45:00", "end": "2026-08-19T18:30:00"},
            {"name": "RBI policy", "start": "2026-08-14T09:45:00", "end": "2026-08-14T11:00:00"},
        ],
    )
    windows = load_event_windows(path)
    assert [window.name for window in windows] == ["RBI policy", "US CPI"]
    assert windows[0].start.tzinfo is not None


def test_active_window_is_half_open(tmp_path):
    path = tmp_path / "events.json"
    start = datetime(2026, 8, 14, 9, 45, tzinfo=IST)
    end = datetime(2026, 8, 14, 11, 0, tzinfo=IST)
    write(path, [{"name": "RBI policy", "start": start.isoformat(), "end": end.isoformat()}])
    windows = load_event_windows(path)

    assert active_window(windows, start) is not None
    assert active_window(windows, start + timedelta(minutes=30)) is not None
    assert active_window(windows, end) is None
    assert active_window(windows, start - timedelta(seconds=1)) is None


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        json.dumps({"name": "x"}),
        json.dumps([{"start": "2026-08-14T09:45:00", "end": "2026-08-14T11:00:00"}]),
        json.dumps([{"name": "x", "start": "yesterday", "end": "2026-08-14T11:00:00"}]),
        json.dumps([{"name": "x", "start": "2026-08-14T11:00:00", "end": "2026-08-14T09:45:00"}]),
    ],
)
def test_malformed_calendar_raises(tmp_path, payload):
    path = tmp_path / "events.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(EventCalendarInvalid):
        load_event_windows(path)
