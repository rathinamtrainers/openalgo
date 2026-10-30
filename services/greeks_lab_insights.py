"""QuadShield-style desk read from the live chain plus 5m ADX/ATR/VIX.

Same bands StrikeShield uses: shorts only when ADX < 40 and India VIX is
inside 11–40; ADX > 45 or an ATR spike means pause shorts. Not an order.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from utils.logging import get_logger

logger = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")
MINUTES_PER_YEAR = 365.0 * 24.0 * 60.0
ADX_ENTRY = 40.0
ADX_EXIT = 45.0
ADX_PERIOD = 15
ATR_PERIOD = 14
ATR_SPIKE = 1.5
VIX_MIN = 11.0
VIX_MAX = 40.0
SESSION_OPEN = time(9, 15)
SESSION_MINUTES = 375
EXPIRY_NO_NEW = time(14, 20)
EXPIRY_FLAT = time(14, 30)
ADX_SLOPE_BARS = 6  # 30 minutes of 5m bars
ADX_SLOPE_PTS = 1.5


def _index_exchange(exchange: str) -> str:
    code = (exchange or "").upper()
    if code in ("NFO", "NSE", "NSE_INDEX"):
        return "NSE_INDEX"
    if code in ("BFO", "BSE", "BSE_INDEX"):
        return "BSE_INDEX"
    return code


def _parse_expiry(expiry_date: str) -> date | None:
    try:
        return datetime.strptime((expiry_date or "").upper(), "%d%b%y").date()
    except ValueError:
        return None


def _bar_float(row: dict[str, Any], *names: str) -> float | None:
    for name in names:
        if name in row and row[name] not in (None, ""):
            try:
                return float(row[name])
            except (TypeError, ValueError):
                continue
    return None


def _wilder_smooth(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    seed = sum(values[:period]) / period
    out = [seed]
    for value in values[period:]:
        seed = (seed * (period - 1) + value) / period
        out.append(seed)
    return out


def _true_range(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    ranges: list[float] = []
    for i in range(1, len(closes)):
        ranges.append(
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        )
    return ranges


def atr_series(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float]:
    return _wilder_smooth(_true_range(highs, lows, closes), period)


def adx_snapshot(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int,
) -> tuple[float, float, float] | None:
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    tr_s = _wilder_smooth(_true_range(highs, lows, closes), period)
    plus_s = _wilder_smooth(plus_dm, period)
    minus_s = _wilder_smooth(minus_dm, period)
    if not tr_s or len(plus_s) != len(tr_s):
        return None
    dx: list[float] = []
    plus_di: list[float] = []
    minus_di: list[float] = []
    for tr, p_dm, m_dm in zip(tr_s, plus_s, minus_s, strict=True):
        if tr <= 0:
            plus_di.append(0.0)
            minus_di.append(0.0)
            dx.append(0.0)
            continue
        p_di = 100.0 * p_dm / tr
        m_di = 100.0 * m_dm / tr
        plus_di.append(p_di)
        minus_di.append(m_di)
        denom = p_di + m_di
        dx.append(0.0 if denom <= 0 else 100.0 * abs(p_di - m_di) / denom)
    smoothed = _wilder_smooth(dx, period)
    if not smoothed:
        return None
    return smoothed[-1], plus_di[-1], minus_di[-1]


def _num(leg: dict[str, Any] | None, key: str) -> float | None:
    if not isinstance(leg, dict):
        return None
    raw = leg.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _chip(state: str, label: str, detail: str) -> dict[str, str]:
    return {"state": state, "label": label, "detail": detail}


def build_desk_insights(
    *,
    now: datetime,
    spot: float,
    atm_iv: float | None,
    ce: dict[str, Any] | None,
    pe: dict[str, Any] | None,
    adx: float | None,
    adx_prev: float | None,
    plus_di: float | None,
    minus_di: float | None,
    atr: float | None,
    prev_atr: float | None,
    vix: float | None,
    day_high: float | None,
    day_low: float | None,
    is_expiry: bool,
    dte: float | None,
) -> dict[str, Any]:
    """Classify decay / QuadShield / session / trend. Pure; no I/O."""
    clock = now.astimezone(IST).time()
    ce_ltp = _num(ce, "ltp") or 0.0
    pe_ltp = _num(pe, "ltp") or 0.0
    straddle = ce_ltp + pe_ltp
    theta = (_num(ce, "theta") or 0.0) + (_num(pe, "theta") or 0.0)
    theta_pct = abs(theta) / straddle * 100.0 if straddle > 0 else None
    day_range = (
        day_high - day_low if day_high is not None and day_low is not None else None
    )

    open_dt = now.astimezone(IST).replace(
        hour=SESSION_OPEN.hour, minute=SESSION_OPEN.minute, second=0, microsecond=0
    )
    elapsed = max(30.0, min(SESSION_MINUTES, (now.astimezone(IST) - open_dt).total_seconds() / 60.0))
    expected_so_far = None
    if spot > 0 and atm_iv and atm_iv > 0:
        expected_so_far = spot * (atm_iv / 100.0) * math.sqrt(elapsed / MINUTES_PER_YEAR)

    atr_spike = bool(prev_atr and prev_atr > 0 and atr and atr > prev_atr * ATR_SPIKE)
    realized_hot = bool(
        expected_so_far and day_range is not None and day_range > 1.2 * expected_so_far
    )
    realized_quiet = bool(
        expected_so_far and day_range is not None and day_range < 0.8 * expected_so_far
    )

    if adx is not None and adx > ADX_EXIT:
        decay_state = "off"
    elif atr_spike or realized_hot:
        decay_state = "off"
    elif adx is not None and adx < ADX_ENTRY and (realized_quiet or adx < 25):
        decay_state = "on"
    elif adx is None and realized_quiet:
        decay_state = "on"
    elif adx is None and realized_hot:
        decay_state = "off"
    else:
        decay_state = "mixed"

    if adx is None or adx_prev is None:
        if adx is not None and adx < 20:
            trend_state = "sideways"
        elif adx is None:
            trend_state = "unknown"
        else:
            trend_state = "sideways"
    elif adx >= adx_prev + ADX_SLOPE_PTS:
        trend_state = "stronger"
    elif adx <= adx_prev - ADX_SLOPE_PTS:
        trend_state = "fading"
    elif adx < 20:
        trend_state = "sideways"
    else:
        trend_state = "sideways"

    di_gap = None
    if plus_di is not None and minus_di is not None:
        di_gap = plus_di - minus_di
    directional = di_gap is not None and abs(di_gap) >= 10 and (adx or 0) >= 20

    if decay_state == "off" or (trend_state == "stronger" and directional):
        session_state = "buying_day"
    elif decay_state == "on" and not directional:
        session_state = "selling_day"
    else:
        session_state = "mixed"

    vix_ok = vix is not None and VIX_MIN < vix < VIX_MAX
    if is_expiry and clock >= EXPIRY_FLAT:
        qs_state = "pause"
        qs_why = "Expiry flatten window (after 14:30 IST)."
    elif vix is not None and not vix_ok:
        qs_state = "pause"
        qs_why = f"India VIX {vix:.1f} is outside the 11–40 shorts band."
    elif is_expiry and clock >= EXPIRY_NO_NEW:
        qs_state = "pause_shorts"
        qs_why = "Expiry day — no new shorts after 14:20 IST."
    elif adx is not None and adx > ADX_EXIT:
        qs_state = "pause_shorts"
        qs_why = f"ADX {adx:.0f} is above the 45 exit band — trend is in control."
    elif adx is not None and adx >= ADX_ENTRY:
        qs_state = "pause_shorts"
        qs_why = f"ADX {adx:.0f} is at/above 40 — do not sell the ATM shorts."
    elif decay_state == "off":
        qs_state = "pause_shorts"
        qs_why = "Decay is off; premium is not working for the short straddle."
    elif vix_ok and decay_state == "on" and (adx is None or adx < ADX_ENTRY):
        qs_state = "run"
        qs_why = "ADX and VIX are inside QuadShield short bands and decay is on."
    else:
        qs_state = "pause_shorts"
        qs_why = "Conditions are mixed — keep wings, do not lean on new shorts."

    decay_labels = {"on": "Decay ON", "off": "Decay OFF", "mixed": "Decay mixed"}
    qs_labels = {
        "run": "QuadShield RUN",
        "pause_shorts": "Pause shorts",
        "pause": "QuadShield PAUSE",
    }
    session_labels = {
        "selling_day": "Selling day",
        "buying_day": "Buying day",
        "mixed": "Mixed day",
    }
    trend_labels = {
        "stronger": "Trend stronger",
        "fading": "Trend fading",
        "sideways": "Sideways",
        "unknown": "Trend n/a",
    }

    decay_bits = []
    if theta_pct is not None:
        decay_bits.append(f"ATM straddle {straddle:.0f}, θ {theta:.1f}/day ({theta_pct:.1f}% of premium)")
    if day_range is not None and expected_so_far:
        decay_bits.append(f"range {day_range:.0f} vs ~{expected_so_far:.0f} implied so far")
    if atr_spike:
        decay_bits.append("ATR spike")
    decay_detail = "; ".join(decay_bits) if decay_bits else "Not enough tape yet."

    trend_bits = []
    if adx is not None:
        trend_bits.append(f"ADX {adx:.0f}")
        if adx_prev is not None:
            trend_bits.append(f"was {adx_prev:.0f} 30m ago")
        if plus_di is not None and minus_di is not None:
            side = "calls" if plus_di > minus_di else "puts"
            trend_bits.append(f"+DI {plus_di:.0f} / −DI {minus_di:.0f} ({side})")
    trend_detail = "; ".join(trend_bits) if trend_bits else "Need 5m bars for ADX."

    session_detail = {
        "selling_day": "Range is contained and the tape is not one-sided — premium selling has room.",
        "buying_day": "Expansion or a one-sided trend — buyers of direction have the better map.",
        "mixed": "Neither a clean premium-selling day nor a clean directional day.",
    }[session_state]
    if vix is not None:
        qs_why = f"{qs_why} VIX {vix:.1f}." if "VIX" not in qs_why else qs_why

    return {
        "as_of_ist": now.astimezone(IST).strftime("%d %b %Y, %H:%M IST"),
        "straddle": round(straddle, 2) if straddle else None,
        "theta": round(theta, 2),
        "adx": None if adx is None else round(adx, 1),
        "vix": None if vix is None else round(vix, 2),
        "dte": dte,
        "is_expiry": is_expiry,
        "decay": _chip(decay_state, decay_labels[decay_state], decay_detail),
        "quadshield": _chip(qs_state, qs_labels[qs_state], qs_why),
        "session": _chip(session_state, session_labels[session_state], session_detail),
        "trend": _chip(trend_state, trend_labels[trend_state], trend_detail),
        "disclaimer": (
            "Desk read from the live ATM straddle plus 5m ADX/ATR and India VIX. "
            "QuadShield bands: shorts when ADX < 40 and VIX 11–40; pause shorts if "
            "ADX > 45 or decay is off. Not an order."
        ),
    }


def _quote_ltp(payload: dict[str, Any]) -> float | None:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return None
    return _bar_float(data, "ltp", "LTP", "last", "close")


def _today_hl(bars: list[dict[str, Any]], today: date) -> tuple[float | None, float | None]:
    todays: list[dict[str, Any]] = []
    for row in bars:
        ts = row.get("time") or row.get("timestamp") or row.get("datetime")
        if not ts:
            continue
        try:
            parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=IST)
            if parsed.astimezone(IST).date() == today:
                todays.append(row)
        except ValueError:
            continue
    use = todays or bars[-75:]
    highs: list[float] = []
    lows: list[float] = []
    for row in use:
        high = _bar_float(row, "high", "h")
        low = _bar_float(row, "low", "l")
        if high is not None:
            highs.append(high)
        if low is not None:
            lows.append(low)
    if not highs or not lows:
        return None, None
    return max(highs), min(lows)


def collect_desk_insights(
    *,
    underlying: str,
    exchange: str,
    expiry_date: str,
    api_key: str,
    spot: float,
    atm_iv: float | None,
    atm_ce: dict[str, Any] | None,
    atm_pe: dict[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch 5m tape + VIX, then classify. Degrades if history is down."""
    from services.history_service import get_history
    from services.quotes_service import get_quotes

    now = now or datetime.now(IST)
    today = now.astimezone(IST).date()
    expiry = _parse_expiry(expiry_date)
    is_expiry = bool(expiry and expiry == today)
    dte = None
    if atm_ce and atm_ce.get("days_to_expiry") is not None:
        dte = float(atm_ce["days_to_expiry"])
    elif expiry:
        dte = max(0.0, (expiry - today).days + (1.0 - now.astimezone(IST).hour / 24.0))

    adx = adx_prev = plus_di = minus_di = atr = prev_atr = None
    day_high = day_low = vix = None
    index_ex = _index_exchange(exchange)
    hist_symbol = underlying
    if (exchange or "").upper() in ("MCX", "CDS"):
        try:
            from services.option_symbol_service import resolve_futures_quote_symbol

            fut = resolve_futures_quote_symbol(underlying, expiry_date, exchange.upper())
            if fut:
                hist_symbol = fut
        except Exception:
            logger.exception("MCX/CDS futures resolve for desk read failed")
    start = (today - timedelta(days=7)).isoformat()
    end = today.isoformat()
    try:
        ok, hist, _status = get_history(
            symbol=hist_symbol,
            exchange=index_ex,
            interval="5m",
            start_date=start,
            end_date=end,
            api_key=api_key,
        )
        bars = hist.get("data") if ok and isinstance(hist, dict) else None
        if isinstance(bars, list) and len(bars) >= ADX_PERIOD * 2 + 3:
            highs, lows, closes = [], [], []
            for row in bars:
                if not isinstance(row, dict):
                    continue
                high = _bar_float(row, "high", "h")
                low = _bar_float(row, "low", "l")
                close = _bar_float(row, "close", "c")
                if high is None or low is None or close is None:
                    continue
                highs.append(high)
                lows.append(low)
                closes.append(close)
            snap = adx_snapshot(highs, lows, closes, ADX_PERIOD)
            if snap:
                adx, plus_di, minus_di = snap
            if len(highs) > ADX_SLOPE_BARS + ADX_PERIOD * 2:
                prev = adx_snapshot(
                    highs[:-ADX_SLOPE_BARS],
                    lows[:-ADX_SLOPE_BARS],
                    closes[:-ADX_SLOPE_BARS],
                    ADX_PERIOD,
                )
                if prev:
                    adx_prev = prev[0]
            atrs = atr_series(highs, lows, closes, ATR_PERIOD)
            if len(atrs) >= 2:
                atr, prev_atr = atrs[-1], atrs[-2]
            day_high, day_low = _today_hl(bars, today)
    except Exception:
        logger.exception("Greeks lab history/ADX failed")

    try:
        ok, quoted, _status = get_quotes("INDIAVIX", "NSE_INDEX", api_key=api_key)
        if ok:
            vix = _quote_ltp(quoted)
    except Exception:
        logger.exception("Greeks lab VIX quote failed")

    return build_desk_insights(
        now=now,
        spot=spot,
        atm_iv=atm_iv,
        ce=atm_ce,
        pe=atm_pe,
        adx=adx,
        adx_prev=adx_prev,
        plus_di=plus_di,
        minus_di=minus_di,
        atr=atr,
        prev_atr=prev_atr,
        vix=vix,
        day_high=day_high,
        day_low=day_low,
        is_expiry=is_expiry,
        dte=dte,
    )
