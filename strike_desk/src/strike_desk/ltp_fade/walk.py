"""Volume-only S/R walk. First peak while walking OTM; closer max wins on a tie."""

from __future__ import annotations

from .reversal import usable_premium
from .types import STRIKE_STEP, WALK_CAP_STEPS, ChainSnapshot, StrikeRow


def atm_spot(spot: float, strikes: list[int]) -> int | None:
    """Closest listed strike to spot. Equal distance → lower strike."""
    if not strikes:
        return None
    unique = sorted(set(strikes))
    return min(unique, key=lambda strike: (abs(strike - spot), strike))


def _by_strike(snapshot: ChainSnapshot) -> dict[int, StrikeRow]:
    return {row.strike: row for row in snapshot.rows}


def _ce_ok(row: StrikeRow) -> bool:
    return usable_premium(row.ce_ltp) and row.ce_volume is not None and row.ce_volume > 0


def _pe_ok(row: StrikeRow) -> bool:
    return usable_premium(row.pe_ltp) and row.pe_volume is not None and row.pe_volume > 0


def walk_resistance(
    snapshot: ChainSnapshot, atm: int, *, step: int = STRIKE_STEP, cap: int = WALK_CAP_STEPS
) -> tuple[int | None, float | None]:
    """Walk calls from ATM outward. First highest volume wins (``>``, not ``>=``).

    Stops at the first non-increase among valid strikes (first peak). Invalid
    strikes are skipped and do not end the walk.
    """
    table = _by_strike(snapshot)
    best_strike: int | None = None
    best_volume: float | None = None
    seen_valid = False
    for offset in range(0, cap + 1):
        strike = atm + offset * step
        row = table.get(strike)
        if row is None or not _ce_ok(row):
            continue
        volume = float(row.ce_volume or 0)
        if best_volume is None or volume > best_volume:
            best_strike = strike
            best_volume = volume
            seen_valid = True
            continue
        if seen_valid:
            break
    return best_strike, best_volume


def walk_support(
    snapshot: ChainSnapshot, atm: int, *, step: int = STRIKE_STEP, cap: int = WALK_CAP_STEPS
) -> tuple[int | None, float | None]:
    """Walk puts from ATM downward. First highest volume wins (``>``, not ``>=``)."""
    table = _by_strike(snapshot)
    best_strike: int | None = None
    best_volume: float | None = None
    seen_valid = False
    for offset in range(0, cap + 1):
        strike = atm - offset * step
        row = table.get(strike)
        if row is None or not _pe_ok(row):
            continue
        volume = float(row.pe_volume or 0)
        if best_volume is None or volume > best_volume:
            best_strike = strike
            best_volume = volume
            seen_valid = True
            continue
        if seen_valid:
            break
    return best_strike, best_volume
