"""The session gate: may this tick run at all?"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from .config import IST, Settings
from .errors import OpenAlgoError
from .openalgo_client import OpenAlgoClient

logger = logging.getLogger(__name__)

KILL_SWITCH = "kill-switch"
MARKET_CLOSED = "market-closed"
NO_TRADE_WINDOW = "no-trade-window"
EXPIRY_CUTOFF = "expiry-cutoff"
CALENDAR_UNAVAILABLE = "calendar-unavailable"
OVERLAP = "overlap"

_CACHE_LIMIT = 14


def engage_kill_switch(settings: Settings, reason: str) -> None:
    """Write the kill switch file. Every subsequent gate evaluation blocks the tick."""
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    settings.kill_switch_path.write_text(
        f"{datetime.now(tz=UTC).isoformat()} {reason}", encoding="utf-8"
    )
    logger.critical("kill switch engaged: %s", reason)


@dataclass(frozen=True)
class GateVerdict:
    allowed: bool
    blocked_by: str | None = None
    detail: str = ""


class SessionGate:
    """Evaluates every precondition that must hold before a tick runs."""

    def __init__(self, client: OpenAlgoClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._windows: dict[date, tuple[datetime, datetime] | None] = {}

    def trading_window(self, day: date) -> tuple[datetime, datetime] | None:
        """The configured exchange's IST open/close for ``day``, or None if closed."""
        if day in self._windows:
            return self._windows[day]

        window: tuple[datetime, datetime] | None = None
        exchange = self._settings.option_exchange.upper()
        for row in self._client.market_timings(day):
            if not isinstance(row, dict) or str(row.get("exchange", "")).upper() != exchange:
                continue
            start = datetime.fromtimestamp(int(row["start_time"]) / 1000, tz=IST)
            end = datetime.fromtimestamp(int(row["end_time"]) / 1000, tz=IST)
            window = (start, end)
            break

        if len(self._windows) >= _CACHE_LIMIT:
            self._windows.pop(next(iter(self._windows)))
        self._windows[day] = window
        return window

    def is_trading_day(self, day: date) -> bool:
        return self.trading_window(day) is not None

    def expiry_date_for(self, day: date) -> date | None:
        """The expiry date of the weekly contract covering ``day``.

        Walks forward to the configured expiry weekday, then backwards over holidays
        to the last day the exchange is actually open.
        """
        offset = (self._settings.expiry_weekday - day.weekday()) % 7
        candidate = day + timedelta(days=offset)
        for _ in range(7):
            if self.is_trading_day(candidate):
                return candidate
            candidate -= timedelta(days=1)
        return None

    def evaluate(self, now: datetime) -> GateVerdict:
        """Apply every gate in order and return the first one that blocks."""
        kill_path = self._settings.kill_switch_path
        if kill_path.exists():
            try:
                detail = kill_path.read_text(encoding="utf-8").strip()[:200]
            except OSError:
                detail = ""
            return GateVerdict(False, KILL_SWITCH, detail or "kill switch engaged")

        try:
            window = self.trading_window(now.date())
            expiry = self.expiry_date_for(now.date())
        except OpenAlgoError as exc:
            logger.error("session calendar unavailable: %s", exc)
            return GateVerdict(False, CALENDAR_UNAVAILABLE, str(exc))

        if window is None:
            return GateVerdict(
                False, MARKET_CLOSED, f"{self._settings.option_exchange} is closed on {now.date()}"
            )
        start, end = window
        if not (start <= now <= end):
            return GateVerdict(
                False,
                MARKET_CLOSED,
                f"{now.time().isoformat(timespec='seconds')} is outside "
                f"{start.time().isoformat(timespec='minutes')}–"
                f"{end.time().isoformat(timespec='minutes')}",
            )

        for window_start, window_end in self._settings.no_trade_window_times:
            if window_start <= now.time() < window_end:
                return GateVerdict(
                    False,
                    NO_TRADE_WINDOW,
                    f"inside {window_start.isoformat(timespec='minutes')}–"
                    f"{window_end.isoformat(timespec='minutes')}",
                )

        if expiry == now.date() and now.time() >= self._settings.expiry_cutoff_time:
            return GateVerdict(
                False,
                EXPIRY_CUTOFF,
                (
                    "expiry day, past "
                    f"{self._settings.expiry_cutoff_time.isoformat(timespec='minutes')}"
                ),
            )

        return GateVerdict(True)
