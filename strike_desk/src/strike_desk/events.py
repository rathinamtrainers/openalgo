"""The event calendar: announcement windows the desk refuses to reason through."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import IST
from .errors import EventCalendarInvalid


@dataclass(frozen=True)
class EventWindow:
    """One named window, in IST, that the desk treats as event-driven."""

    name: str
    start: datetime
    end: datetime

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end

    def as_evidence(self) -> dict[str, str]:
        return {
            "tool": "event-calendar",
            "field": self.name,
            "value": f"{self.start.isoformat()} to {self.end.isoformat()}",
        }


def _as_ist(raw: Any, label: str) -> datetime:
    if not isinstance(raw, str):
        raise EventCalendarInvalid(f"{label} must be an ISO-8601 string, got {type(raw).__name__}")
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise EventCalendarInvalid(f"{label} is not an ISO-8601 datetime: {raw!r}") from exc
    return moment.replace(tzinfo=IST) if moment.tzinfo is None else moment.astimezone(IST)


def load_event_windows(path: Path) -> tuple[EventWindow, ...]:
    """Read the calendar. Absent means no windows; unreadable means we do not know."""
    if not path.exists():
        return ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EventCalendarInvalid(f"{path}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(raw, list):
        raise EventCalendarInvalid(f"{path}: expected a JSON array of window objects")

    windows: list[EventWindow] = []
    for position, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise EventCalendarInvalid(f"{path}: entry {position} is not an object")
        name = str(entry.get("name", "")).strip()
        if not name:
            raise EventCalendarInvalid(f"{path}: entry {position} has no name")
        start = _as_ist(entry.get("start"), f"{path} entry {position} start")
        end = _as_ist(entry.get("end"), f"{path} entry {position} end")
        if start >= end:
            raise EventCalendarInvalid(f"{path}: entry {position} must start before it ends")
        windows.append(EventWindow(name=name, start=start, end=end))
    return tuple(sorted(windows, key=lambda window: window.start))


def active_window(windows: tuple[EventWindow, ...], now_ist: datetime) -> EventWindow | None:
    """The window containing this IST moment, if any."""
    return next((window for window in windows if window.contains(now_ist)), None)
