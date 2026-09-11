"""The exit levels and the arithmetic over them. Pure: no I/O, no clock of its own.

The agentic layer chose these numbers at proposal time. This module only compares them, and
that separation is the reason an exit never waits on a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from .config import IST
from .errors import LevelsUnavailable
from .journal import EXIT_SESSION_DEADLINE, EXIT_STOP, EXIT_TARGET, EXIT_TIME_STOP


@dataclass(frozen=True)
class ExitLevels:
    """The three exits a long option is held against, stamped at adoption."""

    stop_price: float
    target_price: float
    time_stop_utc: datetime
    time_stop_reason: str

    def __post_init__(self) -> None:
        if not (self.stop_price > 0 and self.target_price > 0):
            raise LevelsUnavailable("stop and target must both be positive premiums")
        if self.stop_price >= self.target_price:
            raise LevelsUnavailable(
                f"stop {self.stop_price} must sit below target {self.target_price}"
            )
        if self.time_stop_utc.tzinfo is None:
            raise LevelsUnavailable("time stop must be timezone-aware")
        if self.time_stop_reason not in {EXIT_TIME_STOP, EXIT_SESSION_DEADLINE}:
            raise LevelsUnavailable(f"unknown time-stop reason {self.time_stop_reason!r}")

    def as_dict(self) -> dict[str, object]:
        return {
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "time_stop_utc": self.time_stop_utc.isoformat(),
            "time_stop_reason": self.time_stop_reason,
        }


@dataclass(frozen=True)
class ExitTrigger:
    """A breached level: why we are getting out, and the two numbers that say so."""

    reason: str
    level_price: float | None
    observed_price: float | None


def resolve_time_stop(
    time_stop_ist: str | None,
    session_deadline: time,
    trading_day: date,
) -> tuple[datetime, str]:
    """The earlier of the proposal's time-stop and the session deadline, as UTC.

    Raises LevelsUnavailable when the proposal's time-stop is unreadable — a position whose
    clock cannot be established is a position the monitor cannot manage.
    """
    deadline = datetime.combine(trading_day, session_deadline, tzinfo=IST)
    if not time_stop_ist:
        return deadline.astimezone(UTC), EXIT_SESSION_DEADLINE
    try:
        proposed = time.fromisoformat(time_stop_ist.strip())
    except ValueError as exc:
        raise LevelsUnavailable(f"time_stop_ist {time_stop_ist!r} is not HH:MM") from exc
    candidate = datetime.combine(trading_day, proposed, tzinfo=IST)
    if candidate <= deadline:
        return candidate.astimezone(UTC), EXIT_TIME_STOP
    return deadline.astimezone(UTC), EXIT_SESSION_DEADLINE


def evaluate(
    levels: ExitLevels,
    price: float | None,
    now_utc: datetime,
) -> ExitTrigger | None:
    """Return the exit this observation demands, or None to keep holding.

    Precedence is safety-first and fixed: time beats price, and the stop beats the target.
    A None price means the observation carried no usable premium — the clock still applies.
    """
    if now_utc >= levels.time_stop_utc:
        return ExitTrigger(levels.time_stop_reason, None, price)
    if price is None:
        return None
    if price <= levels.stop_price:
        return ExitTrigger(EXIT_STOP, levels.stop_price, price)
    if price >= levels.target_price:
        return ExitTrigger(EXIT_TARGET, levels.target_price, price)
    return None


def distances(levels: ExitLevels, price: float | None) -> dict[str, float | None]:
    """How far the last observation sits from each price level, for the operator view."""
    if price is None:
        return {"to_stop": None, "to_target": None}
    return {
        "to_stop": round(price - levels.stop_price, 2),
        "to_target": round(levels.target_price - price, 2),
    }
