"""The unattended-mode guards: mode agreement, dead-man switch, loss cap, trade count.

Every function here answers with a verdict object rather than raising, because each one runs
inside the plan node before a token is spent and a decline is a journalled row, not an error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from .config import Settings
from .errors import MirrorUnavailable
from .journal import Journal
from .openalgo_mirror import ORDER_MODE_AUTO, ORDER_MODE_SEMI_AUTO, OpenAlgoMirror


@dataclass(frozen=True)
class AutonomyVerdict:
    """Whether an entry may be formed right now under the configured autonomy."""

    ok: bool
    reason: str | None = None
    detail: str = ""


def required_order_mode(settings: Settings) -> str:
    return ORDER_MODE_AUTO if settings.unattended else ORDER_MODE_SEMI_AUTO


def check_mode(settings: Settings, mirror: OpenAlgoMirror) -> AutonomyVerdict:
    """The desk's autonomy and OpenAlgo's order mode must agree before anything is sent."""
    wanted = required_order_mode(settings)
    try:
        actual = mirror.order_mode()
    except MirrorUnavailable as exc:
        return AutonomyVerdict(False, "autonomy-mode-mismatch", f"order mode unreadable: {exc}")
    if actual == wanted:
        return AutonomyVerdict(True)
    return AutonomyVerdict(
        False,
        "autonomy-mode-mismatch",
        f"autonomy is {settings.autonomy!r} which needs order mode {wanted!r}, "
        f"but OpenAlgo reports {actual!r}",
    )


def check_monitor(settings: Settings, monitor: Any | None, now: datetime) -> AutonomyVerdict:
    """The dead-man switch: unattended, nothing is entered that nothing is watching."""
    if not settings.unattended:
        return AutonomyVerdict(True)
    if monitor is None or not monitor.is_alive():
        return AutonomyVerdict(False, "monitor-unavailable", "the position monitor is not running")
    age = (now - monitor.heartbeat_utc).total_seconds()
    if age > settings.monitor_heartbeat_max_age_seconds:
        return AutonomyVerdict(
            False, "monitor-unavailable", f"monitor heartbeat is {age:.0f}s old"
        )
    return AutonomyVerdict(True)


def check_day(settings: Settings, journal: Journal, trading_day: date) -> AutonomyVerdict:
    """The two daily caps, both derived from the journal so a restart cannot reset them."""
    if not settings.unattended:
        return AutonomyVerdict(True)
    realised = journal.realised_pnl_for_day(trading_day)
    if realised <= -abs(settings.unattended_daily_loss_cap):
        return AutonomyVerdict(
            False,
            "daily-loss-cap",
            f"realised {realised:.2f} against a cap of "
            f"{-abs(settings.unattended_daily_loss_cap):.2f}",
        )
    entries = journal.entry_count_for_day(trading_day)
    if entries >= settings.unattended_max_trades_per_day:
        return AutonomyVerdict(
            False,
            "daily-trade-cap",
            f"{entries} unattended entries already taken, cap is "
            f"{settings.unattended_max_trades_per_day}",
        )
    return AutonomyVerdict(True)


def evaluate(
    settings: Settings,
    mirror: OpenAlgoMirror,
    journal: Journal,
    monitor: Any | None,
    trading_day: date,
    now: datetime | None = None,
) -> AutonomyVerdict:
    """All three guards in precedence order: agreement, then watching, then the day's budget."""
    now = now or datetime.now(UTC)
    for verdict in (
        check_mode(settings, mirror),
        check_monitor(settings, monitor, now),
        check_day(settings, journal, trading_day),
    ):
        if not verdict.ok:
            return verdict
    return AutonomyVerdict(True)
