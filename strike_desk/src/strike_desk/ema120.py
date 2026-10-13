"""120-minute 21-EMA high/low signal. Arithmetic only — no model call.

Pine: buy when the last closed close is above the EMA of highs, sell when it is
below the EMA of lows. The forming bar is dropped so a live candle cannot fire.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from .config import IST, Settings
from .errors import OpenAlgoError
from .openalgo_client import OpenAlgoClient

Side = Literal["long", "short"]

TRAILER_RE = re.compile(
    r"\[ema120\]\s+side=(long|short)\s+high=(?P<high>[-0-9.]+)\s+low=(?P<low>[-0-9.]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EmaBar:
    """One OHLCV bar after the timestamp has been parsed."""

    start: datetime
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class EmaSignal:
    """The last closed bar against the two bands. ``side`` is None when flat."""

    side: Side | None
    ema_high: float
    ema_low: float
    close: float
    bar_start: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "ema_high": self.ema_high,
            "ema_low": self.ema_low,
            "close": self.close,
            "bar_start": self.bar_start.isoformat(),
        }


@dataclass(frozen=True)
class EmaExits:
    """Bands the monitor holds a live NRML against."""

    side: Side
    ema_high: float
    ema_low: float


def history_interval(minutes: int) -> str:
    """Map a minute count to OpenAlgo's history token. 120 minutes is ``2h``."""
    if minutes <= 0:
        raise ValueError("ema interval must be positive minutes")
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def ema_series(values: list[float], length: int) -> list[float]:
    """Wilder-style seed: SMA of the first ``length`` points, then EMA."""
    if length < 1:
        raise ValueError("ema length must be at least 1")
    if len(values) < length:
        raise ValueError(f"need {length} values for a {length}-EMA, got {len(values)}")
    seed = sum(values[:length]) / length
    out = [seed]
    alpha = 2.0 / (length + 1)
    running = seed
    for value in values[length:]:
        running = value * alpha + running * (1.0 - alpha)
        out.append(running)
    return out


def _as_float(raw: Any) -> float | None:
    try:
        if raw is None or raw == "":
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_start(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=IST)
    return moment


def parse_bars(raw: dict[str, Any] | list[Any]) -> list[EmaBar]:
    """Accept the OpenAlgo history list (or a ``{candles: [...]}`` wrapper)."""
    rows: Any = raw
    if isinstance(raw, dict):
        rows = raw.get("data") or raw.get("candles") or raw.get("history") or []
    if not isinstance(rows, list):
        return []
    bars: list[EmaBar] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        start = _parse_start(item.get("timestamp") or item.get("time") or item.get("datetime"))
        high = _as_float(item.get("high"))
        low = _as_float(item.get("low"))
        close = _as_float(item.get("close"))
        if start is None or high is None or low is None or close is None:
            continue
        if high <= 0 or low <= 0 or close <= 0:
            continue
        bars.append(EmaBar(start=start.astimezone(IST), high=high, low=low, close=close))
    bars.sort(key=lambda bar: bar.start)
    return bars


def closed_bars(bars: list[EmaBar], now: datetime, interval_minutes: int) -> list[EmaBar]:
    """Drop the forming candle. ``timestamp`` is treated as the bar open."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(IST)
    width = timedelta(minutes=interval_minutes)
    return [bar for bar in bars if bar.start + width <= now]


def signal_from_bars(
    bars: list[EmaBar], *, length: int, now: datetime, interval_minutes: int
) -> EmaSignal | None:
    """Last closed close vs the two EMAs. None when there is not enough history."""
    closed = closed_bars(bars, now, interval_minutes)
    if len(closed) < length:
        return None
    highs = ema_series([bar.high for bar in closed], length)
    lows = ema_series([bar.low for bar in closed], length)
    last = closed[-1]
    ema_high = highs[-1]
    ema_low = lows[-1]
    side: Side | None = None
    if last.close > ema_high:
        side = "long"
    elif last.close < ema_low:
        side = "short"
    return EmaSignal(
        side=side,
        ema_high=ema_high,
        ema_low=ema_low,
        close=last.close,
        bar_start=last.start,
    )


def format_trailer(side: Side, ema_high: float, ema_low: float) -> str:
    """Stable token the monitor can parse back out of a proposal rationale."""
    return f"[ema120] side={side} high={ema_high:.2f} low={ema_low:.2f}"


def parse_trailer(text: str | None) -> EmaExits | None:
    if not text:
        return None
    match = TRAILER_RE.search(text)
    if match is None:
        return None
    return EmaExits(
        side=match.group(1).lower(),  # type: ignore[arg-type]
        ema_high=float(match.group("high")),
        ema_low=float(match.group("low")),
    )


def band_exit(exits: EmaExits, *, high: float | None, low: float | None, ltp: float | None) -> bool:
    """Pine: long exits when the live bar's low tags emaLow; short when high tags emaHigh."""
    live_low = low if low is not None else ltp
    live_high = high if high is not None else ltp
    if exits.side == "long":
        return live_low is not None and live_low < exits.ema_low
    return live_high is not None and live_high > exits.ema_high


def read_signal(
    client: OpenAlgoClient, settings: Settings, *, now: datetime | None = None
) -> EmaSignal:
    """Fetch NIFTY spot history and return the closed-bar signal. Raises on a dead feed."""
    moment = now or datetime.now(tz=UTC)
    interval = history_interval(settings.ema_interval)
    end = moment.astimezone(IST).date()
    start = end - timedelta(days=40)
    raw = client.history(
        settings.index_symbol,
        settings.index_spot_exchange,
        interval,
        start.isoformat(),
        end.isoformat(),
    )
    bars = parse_bars(raw)
    signal = signal_from_bars(
        bars, length=settings.ema_length, now=moment, interval_minutes=settings.ema_interval
    )
    if signal is None:
        raise OpenAlgoError(
            f"ema120: need {settings.ema_length} closed {interval} bars on "
            f"{settings.index_symbol}"
        )
    return signal
