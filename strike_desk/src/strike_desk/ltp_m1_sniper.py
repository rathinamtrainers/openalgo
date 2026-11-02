"""BTC M1 Sniper — second mind on the CoA BTC Hands book.

CoA stays the option-chain map (ltp_* flags). This module never posts those.
Long = ATM daily CE, short = ATM daily PE, same 0.01 contract as CoA Hands.
NSE/BSE/MCX do not import this.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal

from .config import IST

Signal = Literal["BUY_CE", "BUY_PE", "FLAT"]
Trend = Literal["long", "short", "flat"]

EMA_FAST = 9
EMA_SLOW = 21
EMA_TREND = 50
BB_PERIOD = 20
BB_K = 2.0
STOCH_PERIOD = 14
STOCH_K = 3
STOCH_D = 3
STOCH_OS = 20.0
STOCH_OB = 80.0
WICK_BUFFER = 2.0
TARGET_R = 1.5
SESSION_OPEN = time(17, 30)
SESSION_CLOSE = time(21, 30)
FRIDAY_CUTOFF = time(20, 0)
TWO_STRIKES = 2


@dataclass(frozen=True)
class M1Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class M1Snapshot:
    signal: Signal
    reason: str
    trend: Trend
    sl: float | None = None
    tp: float | None = None
    entry: float | None = None
    ema9: float | None = None
    ema21: float | None = None
    stoch_k: float | None = None
    stoch_d: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None


def in_m1_window(now: datetime) -> bool:
    """London/NY overlap 17:30–21:30 IST. Friday scalp stops at 20:00 IST."""
    stamp = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
    clock = stamp.timetz().replace(tzinfo=None)
    if stamp.weekday() == 4 and clock >= FRIDAY_CUTOFF:
        return False
    return SESSION_OPEN <= clock <= SESSION_CLOSE


def halt_until(now: datetime) -> str:
    """Next IST calendar day after two consecutive stops."""
    stamp = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
    return (stamp.date() + timedelta(days=1)).isoformat()


def evaluate_m1(
    m1: list[M1Bar],
    m5: list[M1Bar],
    now: datetime,
    *,
    consecutive_stops: int = 0,
    halted_until: str = "",
) -> M1Snapshot:
    """Closed-bar M1 trigger with M5 50-EMA bias. Never loosens CoA 1.8/1.9."""
    stamp = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
    if halted_until and stamp.date().isoformat() < halted_until:
        return M1Snapshot("FLAT", "two-strike halt", "flat")
    if consecutive_stops >= TWO_STRIKES:
        return M1Snapshot("FLAT", "two-strike halt", "flat")
    if not in_m1_window(stamp):
        return M1Snapshot("FLAT", "outside London/NY overlap", "flat")
    if len(m1) < BB_PERIOD + STOCH_K + STOCH_D or len(m5) < EMA_TREND:
        return M1Snapshot("FLAT", "warming indicators", "flat")

    m5_close = [bar.close for bar in m5]
    trend_ema = _ema(m5_close, EMA_TREND)[-1]
    if trend_ema is None:
        return M1Snapshot("FLAT", "warming M5 50 EMA", "flat")
    m5_last = m5[-1].close
    if m5_last > trend_ema:
        trend: Trend = "long"
    elif m5_last < trend_ema:
        trend = "short"
    else:
        return M1Snapshot("FLAT", "M5 50 EMA flat", "flat")

    closes = [bar.close for bar in m1]
    highs = [bar.high for bar in m1]
    lows = [bar.low for bar in m1]
    ema9 = _ema(closes, EMA_FAST)[-1]
    ema21 = _ema(closes, EMA_SLOW)[-1]
    k_line, d_line = _stochastic(highs, lows, closes)
    bb_mid, bb_up, bb_dn = _bollinger(closes)
    if None in (ema9, ema21, k_line[-1], d_line[-1], bb_up, bb_dn) or len(k_line) < 2:
        return M1Snapshot("FLAT", "warming M1 indicators", trend)

    bar = m1[-1]
    k_now = float(k_line[-1])
    d_now = float(d_line[-1])
    k_prev = float(k_line[-2])
    d_prev = float(d_line[-2])
    snap_base = {
        "trend": trend,
        "ema9": ema9,
        "ema21": ema21,
        "stoch_k": k_now,
        "stoch_d": d_now,
        "bb_upper": bb_up,
        "bb_lower": bb_dn,
        "entry": bar.close,
    }

    if trend == "long":
        if not _touched(bar, float(ema9), float(ema21)):
            return M1Snapshot("FLAT", "no 9/21 EMA pullback", **snap_base)
        if not (k_now < STOCH_OS and _cross_up(k_prev, d_prev, k_now, d_now)):
            return M1Snapshot("FLAT", "stoch not oversold cross-up", **snap_base)
        sl = bar.low - WICK_BUFFER
        risk = bar.close - sl
        if risk <= 0:
            return M1Snapshot("FLAT", "zero long risk", **snap_base)
        tp = min(float(bb_up), bar.close + TARGET_R * risk)
        return M1Snapshot(
            "BUY_CE",
            "M1 long: M5 above 50 EMA, pullback, stoch cross-up",
            sl=sl,
            tp=tp,
            **snap_base,
        )

    if not _touched(bar, float(ema9), float(ema21)):
        return M1Snapshot("FLAT", "no 9/21 EMA rally", **snap_base)
    if not (k_now > STOCH_OB and _cross_down(k_prev, d_prev, k_now, d_now)):
        return M1Snapshot("FLAT", "stoch not overbought cross-down", **snap_base)
    sl = bar.high + WICK_BUFFER
    risk = sl - bar.close
    if risk <= 0:
        return M1Snapshot("FLAT", "zero short risk", **snap_base)
    tp = max(float(bb_dn), bar.close - TARGET_R * risk)
    return M1Snapshot(
        "BUY_PE",
        "M1 short: M5 below 50 EMA, rally, stoch cross-down",
        sl=sl,
        tp=tp,
        **snap_base,
    )


def decide_m1_exit(spot: float, side: str, sl: float, tp: float) -> str | None:
    if spot <= 0:
        return None
    if side == "CE":
        if spot <= sl:
            return "M1 SL below entry wick"
        if spot >= tp:
            return "M1 TP"
        return None
    if spot >= sl:
        return "M1 SL above entry wick"
    if spot <= tp:
        return "M1 TP"
    return None


def _touched(bar: M1Bar, ema9: float, ema21: float) -> bool:
    return _spans(bar, ema9) or _spans(bar, ema21)


def _spans(bar: M1Bar, level: float) -> bool:
    return bar.low <= level <= bar.high


def _cross_up(k_prev: float, d_prev: float, k_now: float, d_now: float) -> bool:
    return k_prev <= d_prev and k_now > d_now


def _cross_down(k_prev: float, d_prev: float, k_now: float, d_now: float) -> bool:
    return k_prev >= d_prev and k_now < d_now


def _ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    alpha = 2.0 / (period + 1)
    acc = seed
    for i in range(period, len(values)):
        acc = values[i] * alpha + acc * (1 - alpha)
        out[i] = acc
    return out


def _sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    window = 0.0
    for i, price in enumerate(values):
        window += price
        if i >= period:
            window -= values[i - period]
        if i >= period - 1:
            out[i] = window / period
    return out


def _bollinger(closes: list[float]) -> tuple[float | None, float | None, float | None]:
    mid_line = _sma(closes, BB_PERIOD)
    mid = mid_line[-1]
    if mid is None or len(closes) < BB_PERIOD:
        return None, None, None
    window = closes[-BB_PERIOD:]
    mean = sum(window) / BB_PERIOD
    var = sum((item - mean) ** 2 for item in window) / BB_PERIOD
    std = var**0.5
    return mid, mid + BB_K * std, mid - BB_K * std


def _stochastic(
    highs: list[float], lows: list[float], closes: list[float]
) -> tuple[list[float | None], list[float | None]]:
    raw: list[float | None] = [None] * len(closes)
    for i in range(len(closes)):
        if i + 1 < STOCH_PERIOD:
            continue
        hh = max(highs[i + 1 - STOCH_PERIOD : i + 1])
        ll = min(lows[i + 1 - STOCH_PERIOD : i + 1])
        span = hh - ll
        raw[i] = 50.0 if span <= 0 else 100.0 * (closes[i] - ll) / span
    k_line = _smooth_filled(raw, STOCH_K)
    d_line = _smooth_filled(k_line, STOCH_D)
    return k_line, d_line


def _smooth_filled(values: list[float | None], period: int) -> list[float | None]:
    filled: list[float] = []
    index_map: list[int] = []
    for i, val in enumerate(values):
        if val is None:
            continue
        filled.append(val)
        index_map.append(i)
    sma = _sma(filled, period)
    out: list[float | None] = [None] * len(values)
    for src, dest in enumerate(index_map):
        out[dest] = sma[src]
    return out
