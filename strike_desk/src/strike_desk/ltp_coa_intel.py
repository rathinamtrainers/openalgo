"""Time-domain LTP Calculator overlays on a CoA 1.0 snapshot.

Chart of Accuracy 1.0 is one chain print. Game of %, State of Confusion, and
CoA 2.0 need a session tape. This module refines the snapshot in place (new
frozen copy) before Hands fire.

Writer-only weekly range (L1–L3) is not a buy filter and is not applied here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from typing import Any, Literal

from .config import IST
from .ltp_coa import CoaSnapshot, Level, Signal, atm_tenure

OI_STABLE = 0.02
SOC_1H = timedelta(hours=1)
SOC_2H = timedelta(hours=2)
SOC_3H = timedelta(hours=3)
COA2_OPEN = time(9, 45)
COA2_CLOSE = time(14, 30)
YELLOW_ON = 0.75


@dataclass
class IntelState:
    session_date: str = ""
    support_strike: float | None = None
    resistance_strike: float | None = None
    support_state: str = "strong"
    resistance_state: str = "strong"
    support_pct: float = 0.0
    resistance_pct: float = 0.0
    prev_support_pct: float = 0.0
    prev_resistance_pct: float = 0.0
    weak_support_since: str | None = None
    weak_resistance_since: str | None = None
    weak_support_open_pct: float = 0.0
    weak_resistance_open_pct: float = 0.0
    support_pct_min: float = 0.0
    support_pct_max: float = 0.0
    resistance_pct_min: float = 0.0
    resistance_pct_max: float = 0.0
    shifted_support: bool = False
    shifted_resistance: bool = False
    call_oi: float = 0.0
    put_oi: float = 0.0
    prev_call_oi: float = 0.0
    prev_put_oi: float = 0.0
    call_oi_dir: Literal["increasing", "stable", "decreasing"] = "stable"
    put_oi_dir: Literal["increasing", "stable", "decreasing"] = "stable"
    eos_hits: int = 0
    eor_hits: int = 0
    eos_strike: float | None = None
    eor_strike: float | None = None


def apply_handbook(
    snap: CoaSnapshot,
    state: IntelState | dict[str, Any] | None,
    now: datetime,
    *,
    call_oi: float = 0.0,
    put_oi: float = 0.0,
    expiry_kind: str = "weekly",
) -> tuple[CoaSnapshot, IntelState]:
    """Overlay Game of %, SOC, CoA 2.0, and first-hit on a CoA 1.0 snapshot."""
    intel = _from_raw(state)
    now = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
    day = now.date().isoformat()
    if intel.session_date != day:
        intel = IntelState(session_date=day)

    support = snap.support
    resistance = snap.resistance
    support_pct = _pct(support)
    resistance_pct = _pct(resistance)
    support_state = support.state if support else "strong"
    resistance_state = resistance.state if resistance else "strong"
    support_strike = None if support is None else support.strike
    resistance_strike = None if resistance is None else resistance.strike

    if (
        intel.support_strike is not None
        and support_strike is not None
        and support_strike != intel.support_strike
        and support_state != "strong"
    ):
        intel.shifted_support = True
        intel.eos_hits = 0
    if (
        intel.resistance_strike is not None
        and resistance_strike is not None
        and resistance_strike != intel.resistance_strike
        and resistance_state != "strong"
    ):
        intel.shifted_resistance = True
        intel.eor_hits = 0

    game_bias = _game_bias(
        support_state,
        resistance_state,
        support_pct,
        resistance_pct,
        intel.support_state,
        intel.resistance_state,
        intel.support_pct,
        intel.resistance_pct,
    )
    soc = _soc(intel, support_state, resistance_state, now)
    clock = now.timetz().replace(tzinfo=None)
    call_dir = _oi_dir(intel.call_oi or intel.prev_call_oi, call_oi)
    put_dir = _oi_dir(intel.put_oi or intel.prev_put_oi, put_oi)
    coa2 = _coa2(clock, call_dir, put_dir)

    allow_ce = snap.allow_ce
    allow_pe = snap.allow_pe
    blocker: str | None = None

    if game_bias == "bearish":
        allow_ce = False
        if snap.signal == "BUY_CE":
            blocker = "Game of % bearish (rising WTB or vanishing WTT)"
    elif game_bias == "bullish":
        allow_pe = False
        if snap.signal == "BUY_PE":
            blocker = "Game of % bullish (rising WTT or vanishing WTB)"

    if soc == "support":
        allow_ce = False
        if snap.signal == "BUY_CE":
            blocker = "State of Confusion in support"
    elif soc == "resistance":
        allow_pe = False
        if snap.signal == "BUY_PE":
            blocker = "State of Confusion in resistance"

    if coa2 == "avoid":
        allow_ce = False
        allow_pe = False
        if snap.signal != "FLAT":
            blocker = "CoA 2.0 both OI falling"

    signal: Signal = snap.signal
    reason = snap.reason
    if signal == "BUY_CE" and not allow_ce:
        signal, reason = "FLAT", blocker or "handbook blocked CE"
    elif signal == "BUY_PE" and not allow_pe:
        signal, reason = "FLAT", blocker or "handbook blocked PE"

    if signal == "BUY_CE":
        same = intel.eos_strike == support_strike
        if intel.eos_hits >= 1 and same:
            signal, reason = "FLAT", "first EOS hit already taken at this support"
            blocker = reason
        else:
            intel.eos_hits += 1
            intel.eos_strike = support_strike
    elif signal == "BUY_PE":
        same = intel.eor_strike == resistance_strike
        if intel.eor_hits >= 1 and same:
            signal, reason = "FLAT", "first EOR hit already taken at this resistance"
            blocker = reason
        else:
            intel.eor_hits += 1
            intel.eor_strike = resistance_strike

    intel.prev_support_pct = intel.support_pct
    intel.prev_resistance_pct = intel.resistance_pct
    intel.support_pct = support_pct
    intel.resistance_pct = resistance_pct
    intel.support_state = support_state
    intel.resistance_state = resistance_state
    intel.support_strike = support_strike
    intel.resistance_strike = resistance_strike
    _roll_weak(
        intel,
        "support",
        support_state,
        support_pct,
        now,
    )
    _roll_weak(
        intel,
        "resistance",
        resistance_state,
        resistance_pct,
        now,
    )
    intel.prev_call_oi = intel.call_oi
    intel.prev_put_oi = intel.put_oi
    intel.call_oi = call_oi
    intel.put_oi = put_oi
    intel.call_oi_dir = call_dir
    intel.put_oi_dir = put_dir

    action = snap.action
    tenure = atm_tenure(expiry_kind)
    if blocker and signal == "FLAT":
        action = f"Stand aside — {blocker}."
    elif signal == "BUY_CE":
        action = f"Buy 1 lot ATM {tenure} CE now — handbook confirmation."
    elif signal == "BUY_PE":
        action = f"Buy 1 lot ATM {tenure} PE now — handbook confirmation."

    refined = replace(
        snap,
        allow_ce=allow_ce,
        allow_pe=allow_pe,
        signal=signal,
        reason=reason,
        action=action,
        blocker=blocker,
        game_bias=game_bias,
        soc=soc,
        coa2=coa2,
    )
    return refined, intel


def atm_oi(rows: list[dict[str, Any]], atm: float) -> tuple[float, float]:
    """CE and PE open interest at the ATM strike."""
    for row in rows:
        try:
            strike = float(row.get("strike") or 0)
        except (TypeError, ValueError):
            continue
        if abs(strike - float(atm)) > 0.01:
            continue
        ce = row.get("ce") if isinstance(row.get("ce"), dict) else {}
        pe = row.get("pe") if isinstance(row.get("pe"), dict) else {}
        return _num(ce.get("oi")), _num(pe.get("oi"))
    return 0.0, 0.0


def intel_to_dict(state: IntelState) -> dict[str, Any]:
    return state.__dict__.copy()


def _from_raw(raw: IntelState | dict[str, Any] | None) -> IntelState:
    if isinstance(raw, IntelState):
        return raw
    if not raw:
        return IntelState()
    known = {field: raw[field] for field in IntelState.__dataclass_fields__ if field in raw}
    return IntelState(**known)


def _pct(level: Level | None) -> float:
    if level is None:
        return 0.0
    return float(level.yellow_pct or 0.0)


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _oi_dir(prev: float, now: float) -> Literal["increasing", "stable", "decreasing"]:
    if prev <= 0:
        return "stable"
    change = (now - prev) / prev
    if change > OI_STABLE:
        return "increasing"
    if change < -OI_STABLE:
        return "decreasing"
    return "stable"


def _game_bias(
    support_state: str,
    resistance_state: str,
    support_pct: float,
    resistance_pct: float,
    prev_support_state: str,
    prev_resistance_state: str,
    prev_support: float,
    prev_resistance: float,
) -> str | None:
    """Rising WTB / vanishing WTT = bearish. Rising WTT / vanishing WTB = bullish."""
    rising_wtb = _rising(support_state, support_pct, prev_support, "wtb") or _rising(
        resistance_state, resistance_pct, prev_resistance, "wtb"
    )
    rising_wtt = _rising(support_state, support_pct, prev_support, "wtt") or _rising(
        resistance_state, resistance_pct, prev_resistance, "wtt"
    )
    vanish_wtt = _vanished(
        prev_support_state, prev_support, support_pct, "wtt"
    ) or _vanished(prev_resistance_state, prev_resistance, resistance_pct, "wtt")
    vanish_wtb = _vanished(
        prev_support_state, prev_support, support_pct, "wtb"
    ) or _vanished(prev_resistance_state, prev_resistance, resistance_pct, "wtb")
    if rising_wtb or vanish_wtt:
        return "bearish"
    if rising_wtt or vanish_wtb:
        return "bullish"
    return None


def _rising(state: str, pct: float, prev: float, want: str) -> bool:
    return state == want and prev > 0 and pct > prev


def _vanished(prev_state: str, prev_pct: float, pct: float, want: str) -> bool:
    return prev_state == want and prev_pct >= YELLOW_ON and pct < YELLOW_ON


def _soc(
    intel: IntelState,
    support_state: str,
    resistance_state: str,
    now: datetime,
) -> str | None:
    """SOC only when the other side is Strong. Never when S/R is Strong."""
    if support_state != "strong" and resistance_state != "strong":
        return None
    if support_state != "strong" and resistance_state == "strong":
        if _soc_ready(intel.weak_support_since, intel.shifted_support, now):
            return "support"
    if resistance_state != "strong" and support_state == "strong":
        if _soc_ready(intel.weak_resistance_since, intel.shifted_resistance, now):
            return "resistance"
    return None


def _soc_ready(since_iso: str | None, shifted: bool, now: datetime) -> bool:
    if not since_iso:
        return False
    since = datetime.fromisoformat(since_iso)
    if since.tzinfo is None:
        since = since.replace(tzinfo=IST)
    elapsed = now - since
    if shifted:
        return elapsed >= SOC_3H
    return elapsed >= SOC_1H


def _coa2(
    clock: time, call_dir: str, put_dir: str
) -> str | None:
    if not (COA2_OPEN <= clock <= COA2_CLOSE):
        return None
    if call_dir == "decreasing" and put_dir == "decreasing":
        return "avoid"
    return f"call {call_dir} / put {put_dir}"


def _roll_weak(
    intel: IntelState,
    side: Literal["support", "resistance"],
    state: str,
    pct: float,
    now: datetime,
) -> None:
    since_attr = f"weak_{side}_since"
    open_attr = f"weak_{side}_open_pct"
    min_attr = f"{side}_pct_min"
    max_attr = f"{side}_pct_max"
    if state == "strong":
        setattr(intel, since_attr, None)
        setattr(intel, open_attr, 0.0)
        if side == "support":
            intel.shifted_support = False
        else:
            intel.shifted_resistance = False
        return
    if getattr(intel, since_attr) is None:
        setattr(intel, since_attr, now.isoformat())
        setattr(intel, open_attr, pct)
        setattr(intel, min_attr, pct)
        setattr(intel, max_attr, pct)
        return
    setattr(intel, min_attr, min(getattr(intel, min_attr) or pct, pct))
    setattr(intel, max_attr, max(getattr(intel, max_attr) or pct, pct))
