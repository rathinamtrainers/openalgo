"""Chart of Accuracy 1.0 — LTP Calculator rules, computed in Strike Desk.

Tradetron is execution only. This module never talks to a broker or a webhook.

Rules follow the public course at howtouseltpcalculator.in (Dr. Vinay Prakash Tiwari):
support / resistance from ATM-outward option-chain volume or OI, yellow-box WTT/WTB
at 75% of the column high, then the nine CoA 1.0 scenarios.

EOS / EOR: the public course gets the print by clicking volume on the LTP app.
On our live chain we use the break-even that click is described as:
EOS = support − put LTP / |Δ| (raw put LTP if delta is missing),
EOR = resistance + call LTP / Δ. Fixed ``extension_pts`` is fallback only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Literal

YELLOW_RATIO = 0.75
NIFTY_STRIKE_STEP = 50.0
MIN_ABS_DELTA = 0.20

LevelState = Literal["strong", "wtt", "wtb"]
Scenario = Literal["1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8", "1.9"]
Signal = Literal["BUY_CE", "BUY_PE", "FLAT"]
Column = Literal["volume", "oi", "both"]
YellowScope = Literal["chain", "side"]

# Handbook COA 1.0 "Trade at EOS / EOR" columns. Buy CE only at EOS, buy PE only at EOR.
_TRADE_AT: dict[Scenario, tuple[bool, bool]] = {
    "1.1": (True, True),
    "1.2": (False, True),
    "1.3": (True, False),
    "1.4": (False, True),
    "1.5": (True, False),
    "1.6": (False, True),
    "1.7": (True, False),
    "1.8": (False, False),
    "1.9": (False, False),
}


@dataclass(frozen=True)
class ChainStrike:
    strike: float
    ce_volume: float
    ce_oi: float
    pe_volume: float
    pe_oi: float
    ce_ltp: float = 0.0
    pe_ltp: float = 0.0
    ce_delta: float = 0.0
    pe_delta: float = 0.0


@dataclass(frozen=True)
class Level:
    strike: float
    state: LevelState
    column: Column
    high: float
    second_strike: float | None
    second: float
    yellow: bool
    yellow_pct: float = 0.0
    dual: bool = False


@dataclass(frozen=True)
class CoaSnapshot:
    spot: float
    atm: float
    support: Level | None
    resistance: Level | None
    scenario: Scenario | None
    eos: float | None
    eor: float | None
    allow_ce: bool
    allow_pe: bool
    signal: Signal
    reason: str
    action: str = "Stand aside."
    blocker: str | None = None
    game_bias: str | None = None
    soc: str | None = None
    coa2: str | None = None


def parse_chain(rows: list[dict[str, Any]]) -> list[ChainStrike]:
    """Accept OpenAlgo ``/api/v1/optionchain`` ``chain`` rows (or a test double)."""
    out: list[ChainStrike] = []
    for row in rows:
        try:
            strike = float(row["strike"])
        except (KeyError, TypeError, ValueError):
            continue
        ce = row.get("ce") if isinstance(row.get("ce"), dict) else {}
        pe = row.get("pe") if isinstance(row.get("pe"), dict) else {}
        out.append(
            ChainStrike(
                strike=strike,
                ce_volume=_num(ce.get("volume")),
                ce_oi=_num(ce.get("oi")),
                pe_volume=_num(pe.get("volume")),
                pe_oi=_num(pe.get("oi")),
                ce_ltp=_num(ce.get("ltp")),
                pe_ltp=_num(pe.get("ltp")),
                ce_delta=_num(ce.get("delta")),
                pe_delta=_num(pe.get("delta")),
            )
        )
    out.sort(key=lambda item: item.strike)
    return out


def evaluate(
    rows: list[dict[str, Any]] | list[ChainStrike],
    spot: float,
    *,
    atm: float | None = None,
    strike_step: float = NIFTY_STRIKE_STEP,
    extension_pts: float = 20.0,
    yellow_ratio: float = YELLOW_RATIO,
    expiry_kind: str = "weekly",
    yellow_scope: YellowScope = "chain",
) -> CoaSnapshot:
    """Map a NIFTY option chain + spot onto a CoA 1.0 snapshot and a buyer signal.

    ``yellow_scope='chain'`` is the handbook default (cash and MCX). BTC 0DTE
    passes ``'side'`` so PE volume above ATM cannot yellow support.
    """
    chain = rows if rows and isinstance(rows[0], ChainStrike) else parse_chain(rows)  # type: ignore[arg-type]
    if not chain or spot <= 0:
        return CoaSnapshot(
            spot=spot,
            atm=atm or 0.0,
            support=None,
            resistance=None,
            scenario=None,
            eos=None,
            eor=None,
            allow_ce=False,
            allow_pe=False,
            signal="FLAT",
            reason="empty chain",
            action="Stand aside — the chain is empty.",
        )

    atm_strike = float(atm) if atm is not None else _nearest_strike(spot, strike_step)
    support = _find_level(
        chain,
        atm_strike,
        side="support",
        yellow_ratio=yellow_ratio,
        yellow_scope=yellow_scope,
    )
    resistance = _find_level(
        chain,
        atm_strike,
        side="resistance",
        yellow_ratio=yellow_ratio,
        yellow_scope=yellow_scope,
    )
    scenario = _scenario(support, resistance)
    allow_ce, allow_pe = _allows(scenario)
    pe_ltp, pe_delta = _premium_at(chain, support.strike, "pe") if support else (0.0, 0.0)
    ce_ltp, ce_delta = (
        _premium_at(chain, resistance.strike, "ce") if resistance else (0.0, 0.0)
    )
    eos = (
        extension_level(
            support.strike, pe_ltp, pe_delta, below=True, fallback=extension_pts
        )
        if support
        else None
    )
    eor = (
        extension_level(
            resistance.strike, ce_ltp, ce_delta, below=False, fallback=extension_pts
        )
        if resistance
        else None
    )
    signal, reason = _signal(spot, scenario, allow_ce, allow_pe, eos, eor)
    return CoaSnapshot(
        spot=spot,
        atm=atm_strike,
        support=support,
        resistance=resistance,
        scenario=scenario,
        eos=eos,
        eor=eor,
        allow_ce=allow_ce,
        allow_pe=allow_pe,
        signal=signal,
        reason=reason,
        action=_action_text(signal, scenario, allow_ce, allow_pe, expiry_kind),
    )


def extension_level(
    strike: float,
    ltp: float,
    delta: float,
    *,
    below: bool,
    fallback: float,
) -> float:
    """Spot extension from strike ± LTP/|Δ|, else ± LTP, else ± fallback pts."""
    offset = float(fallback)
    premium = max(float(ltp or 0), 0.0)
    if premium > 0:
        abs_delta = abs(float(delta or 0))
        offset = premium / abs_delta if abs_delta >= MIN_ABS_DELTA else premium
    return round(float(strike) - offset, 2) if below else round(float(strike) + offset, 2)


def apply_premium_extension(
    snap: CoaSnapshot,
    rows: list[dict[str, Any]] | list[ChainStrike],
    *,
    fallback_pts: float,
    expiry_kind: str = "weekly",
) -> CoaSnapshot:
    """Recompute EOS/EOR from live LTP (and delta if present) on the raw chain."""
    if snap.support is None and snap.resistance is None:
        return snap
    chain = rows if rows and isinstance(rows[0], ChainStrike) else parse_chain(rows)  # type: ignore[arg-type]
    pe_ltp, pe_delta = (
        _premium_at(chain, snap.support.strike, "pe") if snap.support else (0.0, 0.0)
    )
    ce_ltp, ce_delta = (
        _premium_at(chain, snap.resistance.strike, "ce") if snap.resistance else (0.0, 0.0)
    )
    eos = (
        extension_level(
            snap.support.strike, pe_ltp, pe_delta, below=True, fallback=fallback_pts
        )
        if snap.support
        else None
    )
    eor = (
        extension_level(
            snap.resistance.strike,
            ce_ltp,
            ce_delta,
            below=False,
            fallback=fallback_pts,
        )
        if snap.resistance
        else None
    )
    if eos == snap.eos and eor == snap.eor:
        return snap
    signal, reason = _signal(
        snap.spot, snap.scenario, snap.allow_ce, snap.allow_pe, eos, eor
    )
    return replace(
        snap,
        eos=eos,
        eor=eor,
        signal=signal,
        reason=reason,
        action=_action_text(
            signal, snap.scenario, snap.allow_ce, snap.allow_pe, expiry_kind
        ),
    )


def _premium_at(
    chain: list[ChainStrike], strike: float, side: Literal["ce", "pe"]
) -> tuple[float, float]:
    for row in chain:
        if abs(row.strike - float(strike)) > 0.01:
            continue
        if side == "pe":
            return row.pe_ltp, row.pe_delta
        return row.ce_ltp, row.ce_delta
    return 0.0, 0.0


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _nearest_strike(spot: float, step: float) -> float:
    return round(spot / step) * step


def _find_level(
    chain: list[ChainStrike],
    atm: float,
    *,
    side: Literal["support", "resistance"],
    yellow_ratio: float,
    yellow_scope: YellowScope = "chain",
) -> Level | None:
    """Highest volume or OI closest to ATM, walking OTM from the imaginary line."""
    if side == "support":
        window = [row for row in chain if row.strike <= atm]

        def vol_of(row: ChainStrike) -> float:
            return row.pe_volume

        def oi_of(row: ChainStrike) -> float:
            return row.pe_oi
    else:
        window = [row for row in chain if row.strike >= atm]

        def vol_of(row: ChainStrike) -> float:
            return row.ce_volume

        def oi_of(row: ChainStrike) -> float:
            return row.ce_oi

    if not window:
        return None

    vol_peak = _peak(window, vol_of)
    oi_peak = _peak(window, oi_of)
    if vol_peak is None and oi_peak is None:
        return None

    dual = (
        vol_peak is not None
        and oi_peak is not None
        and vol_peak.strike == oi_peak.strike
    )
    if dual:
        chosen_row = vol_peak
        chosen_column: Column = "both"
    else:
        chosen_column, chosen_row = _closer_peak(atm, vol_peak, oi_peak)
        if chosen_row is None:
            return None

    yellow_rows = window if yellow_scope == "side" else chain
    vol_second_strike, vol_second = _second_highest(
        [(row.strike, vol_of(row)) for row in yellow_rows], chosen_row.strike
    )
    oi_second_strike, oi_second = _second_highest(
        [(row.strike, oi_of(row)) for row in yellow_rows], chosen_row.strike
    )
    vol_high = vol_of(chosen_row)
    oi_high = oi_of(chosen_row)
    vol_state = _column_state(
        chosen_row.strike, vol_high, vol_second_strike, vol_second, yellow_ratio
    )
    oi_state = _column_state(
        chosen_row.strike, oi_high, oi_second_strike, oi_second, yellow_ratio
    )

    if dual:
        state = _combine_dual(side, vol_state, oi_state)
        high = max(vol_high, oi_high)
        if vol_high >= oi_high:
            second_strike, second = vol_second_strike, vol_second
        else:
            second_strike, second = oi_second_strike, oi_second
    else:
        if chosen_column == "volume":
            state = vol_state
            high, second_strike, second = vol_high, vol_second_strike, vol_second
            other_state, other_second = oi_state, oi_second_strike
        else:
            state = oi_state
            high, second_strike, second = oi_high, oi_second_strike, oi_second
            other_state, other_second = vol_state, vol_second_strike
        if state == "strong" and _towards_line(side, chosen_row.strike, other_second):
            # Other factor yellow only counts if it has crossed past S/R toward the line.
            if other_state != "strong":
                state = "wtt" if side == "support" else "wtb"

    yellow = high > 0 and second >= yellow_ratio * high and second_strike is not None
    yellow_pct = (second / high) if high > 0 and yellow else 0.0
    return Level(
        strike=chosen_row.strike,
        state=state,
        column=chosen_column,
        high=high,
        second_strike=second_strike,
        second=second,
        yellow=yellow,
        yellow_pct=yellow_pct,
        dual=dual,
    )


def _peak(
    window: list[ChainStrike], getter: Callable[[ChainStrike], float]
) -> ChainStrike | None:
    best: ChainStrike | None = None
    best_val = 0.0
    for row in window:
        val = getter(row)
        if val > best_val:
            best = row
            best_val = val
    return best if best_val > 0 else None


def _closer_peak(
    atm: float,
    vol_peak: ChainStrike | None,
    oi_peak: ChainStrike | None,
) -> tuple[Column, ChainStrike | None]:
    if vol_peak is None and oi_peak is None:
        return "oi", None
    if vol_peak is None:
        return "oi", oi_peak
    if oi_peak is None:
        return "volume", vol_peak
    vol_dist = abs(vol_peak.strike - atm)
    oi_dist = abs(oi_peak.strike - atm)
    if oi_dist < vol_dist:
        return "oi", oi_peak
    return "volume", vol_peak


def _second_highest(
    values: list[tuple[float, float]], winner_strike: float
) -> tuple[float | None, float]:
    second = 0.0
    second_strike: float | None = None
    for strike, val in values:
        if strike == winner_strike:
            continue
        if val > second:
            second = val
            second_strike = strike
    return second_strike, second


def _scenario(support: Level | None, resistance: Level | None) -> Scenario | None:
    if support is None or resistance is None:
        return None
    key = (support.state, resistance.state)
    mapping: dict[tuple[LevelState, LevelState], Scenario] = {
        ("strong", "strong"): "1.1",
        ("strong", "wtb"): "1.2",
        ("strong", "wtt"): "1.3",
        ("wtb", "strong"): "1.4",
        ("wtt", "strong"): "1.5",
        ("wtb", "wtb"): "1.6",
        ("wtt", "wtt"): "1.7",
        ("wtb", "wtt"): "1.8",
        ("wtt", "wtb"): "1.9",
    }
    return mapping[key]


def _column_state(
    chosen: float,
    high: float,
    second_strike: float | None,
    second: float,
    yellow_ratio: float,
) -> LevelState:
    yellow = high > 0 and second >= yellow_ratio * high and second_strike is not None
    if not yellow or second_strike is None:
        return "strong"
    if second_strike > chosen:
        return "wtt"
    if second_strike < chosen:
        return "wtb"
    return "strong"


def _towards_line(
    side: Literal["support", "resistance"], chosen: float, yellow_strike: float | None
) -> bool:
    if yellow_strike is None:
        return False
    if side == "support":
        return yellow_strike > chosen
    return yellow_strike < chosen


def _combine_dual(
    side: Literal["support", "resistance"], vol_state: LevelState, oi_state: LevelState
) -> LevelState:
    """Both volume and OI at one strike. Away-from-line weakness is ignored."""
    if vol_state == "strong" and oi_state == "strong":
        return "strong"
    towards = "wtt" if side == "support" else "wtb"
    if vol_state == towards or oi_state == towards:
        return towards
    return "strong"


def _allows(scenario: Scenario | None) -> tuple[bool, bool]:
    if scenario is None:
        return False, False
    return _TRADE_AT[scenario]


def atm_tenure(kind: str) -> str:
    if kind == "monthly":
        return "monthly"
    if kind == "daily":
        return "daily"
    return "weekly"


def _action_text(
    signal: Signal,
    scenario: Scenario | None,
    allow_ce: bool,
    allow_pe: bool,
    expiry_kind: str = "weekly",
) -> str:
    if scenario is None:
        return "Stand aside — support or resistance is missing."
    if scenario in {"1.8", "1.9"}:
        return "Stand aside until one side turns Strong."
    tenure = atm_tenure(expiry_kind)
    if signal == "BUY_CE":
        return f"Buy 1 lot ATM {tenure} CE now — extension of support is tagged."
    if signal == "BUY_PE":
        return f"Buy 1 lot ATM {tenure} PE now — extension of resistance is tagged."
    bits: list[str] = []
    if allow_ce:
        bits.append("wait for a print at or below the call-buy zone (EOS)")
    if allow_pe:
        bits.append("wait for a print at or above the put-buy zone (EOR)")
    if not bits:
        return "No buy is allowed on this map."
    return "Watch: " + "; ".join(bits) + "."


def _signal(
    spot: float,
    scenario: Scenario | None,
    allow_ce: bool,
    allow_pe: bool,
    eos: float | None,
    eor: float | None,
) -> tuple[Signal, str]:
    if scenario is None:
        return "FLAT", "no support/resistance"
    if scenario in {"1.8", "1.9"}:
        return "FLAT", f"CoA {scenario} not tradable until one side is strong"
    at_eos = eos is not None and spot <= eos
    at_eor = eor is not None and spot >= eor
    if at_eos and at_eor:
        return "FLAT", f"CoA {scenario} both extensions tagged — skip"
    if allow_ce and at_eos:
        return "BUY_CE", f"CoA {scenario} spot at EOS"
    if allow_pe and at_eor:
        return "BUY_PE", f"CoA {scenario} spot at EOR"
    return "FLAT", f"CoA {scenario} waiting for EOS/EOR"
