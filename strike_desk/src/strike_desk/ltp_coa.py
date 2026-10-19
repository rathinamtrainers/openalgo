"""Chart of Accuracy 1.0 — LTP Calculator rules, computed in Strike Desk.

Tradetron is execution only. This module never talks to a broker or a webhook.

Rules follow the public course at howtouseltpcalculator.in (Dr. Vinay Prakash Tiwari):
support / resistance from ATM-outward option-chain volume or OI, yellow-box WTT/WTB
at 75% of the column high, then the nine CoA 1.0 scenarios.

Extension-of-support / extension-of-resistance click-prices are proprietary to the
LTP Calculator app. We approximate EOS as support minus ``extension_pts`` and EOR
as resistance plus ``extension_pts``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

YELLOW_RATIO = 0.75
NIFTY_STRIKE_STEP = 50.0

LevelState = Literal["strong", "wtt", "wtb"]
Scenario = Literal["1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8", "1.9"]
Signal = Literal["BUY_CE", "BUY_PE", "FLAT"]
Column = Literal["volume", "oi"]


@dataclass(frozen=True)
class ChainStrike:
    strike: float
    ce_volume: float
    ce_oi: float
    pe_volume: float
    pe_oi: float


@dataclass(frozen=True)
class Level:
    strike: float
    state: LevelState
    column: Column
    high: float
    second_strike: float | None
    second: float
    yellow: bool


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
) -> CoaSnapshot:
    """Map a NIFTY option chain + spot onto a CoA 1.0 snapshot and a buyer signal."""
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
        )

    atm_strike = float(atm) if atm is not None else _nearest_strike(spot, strike_step)
    support = _find_level(chain, atm_strike, side="support", yellow_ratio=yellow_ratio)
    resistance = _find_level(chain, atm_strike, side="resistance", yellow_ratio=yellow_ratio)
    scenario = _scenario(support, resistance)
    allow_ce, allow_pe = _allows(scenario)
    eos = (support.strike - extension_pts) if support else None
    eor = (resistance.strike + extension_pts) if resistance else None
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
    )


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
    chosen_column, chosen_row = _closer_peak(atm, vol_peak, oi_peak)
    if chosen_row is None:
        return None

    getter = vol_of if chosen_column == "volume" else oi_of
    values = [(row.strike, getter(row)) for row in chain]
    high = getter(chosen_row)
    second_strike, second = _second_highest(values, chosen_row.strike)
    yellow = high > 0 and second >= yellow_ratio * high and second_strike is not None
    state: LevelState = "strong"
    if yellow and second_strike is not None:
        if second_strike > chosen_row.strike:
            state = "wtt"
        elif second_strike < chosen_row.strike:
            state = "wtb"
    return Level(
        strike=chosen_row.strike,
        state=state,
        column=chosen_column,
        high=high,
        second_strike=second_strike,
        second=second,
        yellow=yellow,
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


def _allows(scenario: Scenario | None) -> tuple[bool, bool]:
    if scenario is None or scenario in {"1.8", "1.9"}:
        return False, False
    if scenario == "1.6":
        return False, True
    if scenario == "1.7":
        return True, False
    return True, True


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
