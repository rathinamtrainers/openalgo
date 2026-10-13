"""Freeze 09:21 S/R and proxy reversals. Pure: no I/O, no clock."""

from __future__ import annotations

from .reversal import REVERSAL_MODE, call_reversal, put_reversal, usable_premium
from .types import STRIKE_STEP, ChainSnapshot, FrozenLines, StrikeRow
from .walk import atm_spot, walk_resistance, walk_support


def _row(snapshot: ChainSnapshot, strike: int | None) -> StrikeRow | None:
    if strike is None:
        return None
    for row in snapshot.rows:
        if row.strike == strike:
            return row
    return None


def _eor(snapshot: ChainSnapshot, strike: int | None) -> float | None:
    row = _row(snapshot, strike)
    if row is None or not usable_premium(row.ce_ltp):
        return None
    return call_reversal(row.strike, row.ce_ltp)


def _eos(snapshot: ChainSnapshot, strike: int | None) -> float | None:
    row = _row(snapshot, strike)
    if row is None or not usable_premium(row.pe_ltp):
        return None
    return put_reversal(row.strike, row.pe_ltp)


def freeze_lines(snapshot: ChainSnapshot) -> FrozenLines | None:
    """Build today's frozen lines from one chain snapshot.

    Returns None when ATM cannot be resolved (no trade). A returned object may
    still have ``call_ok`` / ``put_ok`` false for missing walks or lines.
    """
    strikes = [row.strike for row in snapshot.rows]
    atm = atm_spot(snapshot.spot, strikes)
    if atm is None:
        return None

    resistance, r_volume = walk_resistance(snapshot, atm)
    support, s_volume = walk_support(snapshot, atm)

    eor = _eor(snapshot, resistance) if resistance is not None else None
    eor1 = _eor(snapshot, resistance + STRIKE_STEP) if resistance is not None else None
    eor2 = _eor(snapshot, resistance + 2 * STRIKE_STEP) if resistance is not None else None
    eos = _eos(snapshot, support) if support is not None else None
    eos1 = _eos(snapshot, support - STRIKE_STEP) if support is not None else None
    eos2 = _eos(snapshot, support - 2 * STRIKE_STEP) if support is not None else None

    tgt_ce = _eor(snapshot, support - STRIKE_STEP) if support is not None else None
    tgt_pe = _eos(snapshot, resistance + STRIKE_STEP) if resistance is not None else None
    sl_ce_spot = (eos2 - 10.0) if eos2 is not None else None
    sl_pe_spot = (eor2 + 10.0) if eor2 is not None else None

    put_ok = resistance is not None and eor is not None and eor1 is not None
    call_ok = support is not None and eos is not None and eos1 is not None

    if eor is not None and eos is not None and eor <= eos:
        put_ok = False
        call_ok = False

    return FrozenLines(
        spot_at_freeze=snapshot.spot,
        atm=atm,
        expiry=snapshot.expiry,
        resistance=resistance,
        support=support,
        r_volume=r_volume,
        s_volume=s_volume,
        eor=eor,
        eor1=eor1,
        eor2=eor2,
        eos=eos,
        eos1=eos1,
        eos2=eos2,
        tgt_ce=tgt_ce,
        tgt_pe=tgt_pe,
        sl_ce_spot=sl_ce_spot,
        sl_pe_spot=sl_pe_spot,
        call_ok=call_ok,
        put_ok=put_ok,
        reversal_mode=REVERSAL_MODE,
        as_of=snapshot.as_of,
    )
