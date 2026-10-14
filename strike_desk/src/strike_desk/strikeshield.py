"""StrikeShield: QuadShield rules on Strike Desk, fills on Tradetron Hands."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from .config import IST, Settings
from .openalgo_client import OpenAlgoClient
from .session import KILL_SWITCH, MARKET_CLOSED
from .tradetron_hands import (
    BUY_WINGS,
    EXIT_ALL,
    EXIT_SHORTS,
    SELL_SHORTS,
    TradetronHands,
)

logger = logging.getLogger(__name__)

WINGS_TIME = time(9, 15)
SHORTS_TIME = time(9, 16)
EXPIRY_NO_NEW = time(14, 20)
EXPIRY_FLAT = time(14, 30)
FIRST_MIN_START = time(9, 14)
FIRST_MIN_END = time(9, 25)
LATE_START = time(15, 0)
LATE_END = time(15, 40)


@dataclass
class ShieldState:
    set1: bool = False
    set2open: bool = False
    eup: float | None = None
    exit_time: float = 0.0
    date: str | None = None
    rollovers: int = 0


@dataclass(frozen=True)
class Indicators:
    spot: float
    atr: float
    prev_atr: float
    adx: float
    vix: float


def _wilder_smooth(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    seed = sum(values[:period]) / period
    out = [seed]
    for value in values[period:]:
        seed = (seed * (period - 1) + value) / period
        out.append(seed)
    return out


def true_range(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    ranges: list[float] = []
    for i in range(1, len(closes)):
        ranges.append(
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        )
    return ranges


def atr_series(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float]:
    return _wilder_smooth(true_range(highs, lows, closes), period)


def adx_last(highs: list[float], lows: list[float], closes: list[float], period: int) -> float:
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    tr_s = _wilder_smooth(true_range(highs, lows, closes), period)
    plus_s = _wilder_smooth(plus_dm, period)
    minus_s = _wilder_smooth(minus_dm, period)
    if not tr_s or len(plus_s) != len(tr_s):
        raise ValueError("not enough bars for ADX")
    dx: list[float] = []
    for tr, p_dm, m_dm in zip(tr_s, plus_s, minus_s, strict=True):
        if tr <= 0:
            dx.append(0.0)
            continue
        p_di = 100.0 * p_dm / tr
        m_di = 100.0 * m_dm / tr
        denom = p_di + m_di
        dx.append(0.0 if denom <= 0 else 100.0 * abs(p_di - m_di) / denom)
    smoothed = _wilder_smooth(dx, period)
    if not smoothed:
        raise ValueError("not enough bars for ADX")
    return smoothed[-1]


def _bar_float(row: dict[str, Any], *names: str) -> float:
    for name in names:
        if name in row and row[name] is not None:
            return float(row[name])
    raise ValueError(f"bar missing {names}")


def ohlc_columns(bars: list[dict[str, Any]]) -> tuple[list[float], list[float], list[float]]:
    highs = [_bar_float(row, "high", "h") for row in bars]
    lows = [_bar_float(row, "low", "l") for row in bars]
    closes = [_bar_float(row, "close", "c") for row in bars]
    return highs, lows, closes


def decide(
    now: datetime,
    indicators: Indicators,
    state: ShieldState,
    settings: Settings,
    *,
    is_expiry_day: bool,
) -> list[str]:
    """Return zero or more hands actions for this evaluation. Mind owns the sequence."""
    clock = now.astimezone(IST).time()
    actions: list[str] = []

    if is_expiry_day and clock >= EXPIRY_FLAT:
        if state.set1 or state.set2open:
            return [EXIT_ALL]
        return []

    if not state.set1 and clock >= WINGS_TIME:
        if not (is_expiry_day and clock >= EXPIRY_NO_NEW):
            actions.append(BUY_WINGS)
            return actions

    if state.set1 and not state.set2open and clock >= SHORTS_TIME:
        cooled = (
            state.exit_time <= 0
            or (now.timestamp() - state.exit_time) >= settings.strikeshield_short_cooldown_seconds
        )
        adx_ok = indicators.adx < settings.strikeshield_adx_entry
        vix_ok = settings.strikeshield_vix_min < indicators.vix < settings.strikeshield_vix_max
        if cooled and adx_ok and vix_ok:
            actions.append(SELL_SHORTS)
            return actions

    if state.set2open:
        reason = short_exit_reason(clock, indicators, state, settings)
        if reason:
            logger.info("strikeshield short exit: %s", reason)
            actions.append(EXIT_SHORTS)
    return actions


def short_exit_reason(
    clock: time,
    indicators: Indicators,
    state: ShieldState,
    settings: Settings,
) -> str | None:
    eup = state.eup if state.eup is not None else indicators.spot
    if abs(indicators.spot - eup) > settings.strikeshield_spot_atr_mult * indicators.atr:
        return f"spot move {abs(indicators.spot - eup):.1f}"
    if indicators.prev_atr > 0 and indicators.atr > indicators.prev_atr * settings.strikeshield_atr_spike:
        return "ATR spike"
    if indicators.adx > settings.strikeshield_adx_exit:
        return f"ADX>{settings.strikeshield_adx_exit:g}"
    window = (FIRST_MIN_START < clock < FIRST_MIN_END) or (LATE_START < clock < LATE_END)
    if window and abs(indicators.spot - eup) > settings.strikeshield_first_min_move:
        return "session-window move"
    return None


def apply_actions(state: ShieldState, actions: list[str], now: datetime, spot: float) -> ShieldState:
    next_state = ShieldState(**asdict(state))
    today = now.astimezone(IST).date().isoformat()
    next_state.date = today
    for action in actions:
        if action == BUY_WINGS:
            next_state.set1 = True
        elif action == SELL_SHORTS:
            next_state.set2open = True
            next_state.eup = spot
            next_state.exit_time = 0.0
        elif action == EXIT_SHORTS:
            next_state.set2open = False
            next_state.rollovers += 1
            next_state.exit_time = now.timestamp()
        elif action == EXIT_ALL:
            next_state.set1 = False
            next_state.set2open = False
            next_state.eup = None
    return next_state


def load_state(path, today: str) -> ShieldState:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        state = ShieldState(
            set1=bool(raw.get("set1")),
            set2open=bool(raw.get("set2open")),
            eup=float(raw["eup"]) if raw.get("eup") is not None else None,
            exit_time=float(raw.get("exit_time") or 0),
            date=raw.get("date"),
            rollovers=int(raw.get("rollovers") or 0),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        state = ShieldState()
    if state.date != today:
        return ShieldState(date=today)
    return state


def save_state(path, state: ShieldState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")


def _quote_ltp(client: OpenAlgoClient, symbol: str, exchange: str) -> float:
    data = client.quotes(symbol, exchange)
    for key in ("ltp", "LTP", "last", "close"):
        if key in data and data[key] not in (None, ""):
            return float(data[key])
    raise ValueError(f"quotes for {exchange}:{symbol} had no last price")


def read_indicators(client: OpenAlgoClient, settings: Settings, today: date) -> Indicators:
    start = today - timedelta(days=7)
    index = settings.index_symbol
    spot_ex = settings.index_spot_exchange
    bars = client.history(index, spot_ex, "5m", start, today)
    if len(bars) < settings.strikeshield_adx_period * 2 + 3:
        raise ValueError(f"need more 5m bars for ADX/ATR, got {len(bars)}")
    highs, lows, closes = ohlc_columns(bars)
    atrs = atr_series(highs, lows, closes, settings.strikeshield_atr_period)
    if len(atrs) < 2:
        raise ValueError("not enough ATR values")
    adx = adx_last(highs, lows, closes, settings.strikeshield_adx_period)
    vix_bars = client.history(settings.vix_symbol, spot_ex, "5m", start, today)
    if not vix_bars:
        raise ValueError("no VIX 5m bars")
    _vh, _vl, vix_closes = ohlc_columns(vix_bars)
    spot = _quote_ltp(client, index, spot_ex)
    return Indicators(
        spot=spot,
        atr=atrs[-1],
        prev_atr=atrs[-2],
        adx=adx,
        vix=vix_closes[-1],
    )


class StrikeShieldRunner:
    """Evaluate QuadShield rules and push Tradetron hands flags."""

    def __init__(
        self,
        settings: Settings,
        client: OpenAlgoClient,
        hands: TradetronHands,
        *,
        is_trading_day,
        expiry_date_for,
        trading_window=None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._hands = hands
        self._is_trading_day = is_trading_day
        self._expiry_date_for = expiry_date_for
        self._trading_window = trading_window

    def step(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(tz=IST)
        if self._settings.kill_switch_path.exists():
            logger.info("strikeshield skipped: %s", KILL_SWITCH)
            return []
        window = self._trading_window(now.date()) if self._trading_window is not None else None
        if window is None and not self._is_trading_day(now.date()):
            logger.info("strikeshield skipped: %s", MARKET_CLOSED)
            return []
        if window is not None:
            start, end = window
            if not (start <= now <= end):
                logger.info("strikeshield skipped: %s", MARKET_CLOSED)
                return []

        today = now.astimezone(IST).date()
        state = load_state(self._settings.strikeshield_state_path, today.isoformat())
        expiry = self._expiry_date_for(today)
        is_expiry = expiry == today
        try:
            indicators = read_indicators(self._client, self._settings, today)
        except Exception:
            logger.exception("strikeshield indicators unavailable")
            return []

        actions = decide(
            now,
            indicators,
            state,
            self._settings,
            is_expiry_day=is_expiry,
        )
        if not actions:
            logger.info(
                "strikeshield hold set1=%s set2=%s adx=%.1f vix=%.2f atr=%.1f spot=%.1f",
                state.set1,
                state.set2open,
                indicators.adx,
                indicators.vix,
                indicators.atr,
                indicators.spot,
            )
            return []

        sent: list[str] = []
        for action in actions:
            self._hands.send(action)
            sent.append(action)
            state = apply_actions(state, [action], now, indicators.spot)
        save_state(self._settings.strikeshield_state_path, state)
        return sent
