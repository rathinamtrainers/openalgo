"""Entry and exit decisions. Pure: no I/O."""

from __future__ import annotations

from datetime import datetime

from .types import (
    FLATTEN_TIME,
    FREEZE_TIME,
    LAST_ENTRY,
    ChainSnapshot,
    Decision,
    SessionState,
    Side,
)
from .walk import atm_spot


def _in_entry_window(now: datetime) -> bool:
    clock = now.time()
    return FREEZE_TIME <= clock < LAST_ENTRY


def _contract(snapshot: ChainSnapshot, side: Side) -> tuple[str, int, float | None]:
    atm = atm_spot(snapshot.spot, [row.strike for row in snapshot.rows])
    if atm is None:
        return "", 0, None
    for row in snapshot.rows:
        if row.strike != atm:
            continue
        if side == "CE":
            return row.ce_symbol, row.lot_size, row.ce_ltp
        return row.pe_symbol, row.lot_size, row.pe_ltp
    return "", 0, None


def evaluate_entry(
    now: datetime,
    spot: float,
    snapshot: ChainSnapshot,
    state: SessionState,
) -> Decision:
    """First-touch fade after freeze. Dual tag → neither. One ticket per day."""
    none = Decision(action="none", reason="no_entry")
    if state.state != "FROZEN" or state.lines is None:
        return Decision(action="none", reason="not_frozen")
    if state.entered_today or state.side != "NONE":
        return Decision(action="none", reason="already_entered")
    if not _in_entry_window(now):
        return Decision(action="none", reason="outside_entry_window")

    lines = state.lines
    call_tag = bool(lines.call_ok and lines.eos1 is not None and spot <= lines.eos1)
    put_tag = bool(lines.put_ok and lines.eor1 is not None and spot >= lines.eor1)
    if call_tag and put_tag:
        return Decision(action="none", reason="dual_tag", detail="skip dual_tag")
    if not call_tag and not put_tag:
        return none

    side: Side = "CE" if call_tag else "PE"
    symbol, lot_size, premium = _contract(snapshot, side)
    if not symbol or lot_size <= 0:
        return Decision(action="none", reason="no_atm_contract", side=side)
    if premium is None or premium <= 1:
        return Decision(action="none", reason="atm_premium_invalid", side=side, symbol=symbol)

    reason = "eos1" if side == "CE" else "eor1"
    return Decision(
        action="enter",
        reason=reason,
        side=side,
        symbol=symbol,
        lots=1,
        lot_size=lot_size,
        quantity=lot_size,
        fill_premium=float(premium),
        detail=f"enter {side} @ {reason} spot={spot} atm_symbol={symbol} lot={lot_size}",
    )


def evaluate_exit(
    now: datetime,
    spot: float,
    option_ltp: float | None,
    state: SessionState,
) -> Decision:
    """OR of premium stop, target, level+10, 15:29 flatten."""
    if state.state != "IN_TRADE" or state.lines is None or state.side == "NONE":
        return Decision(action="none", reason="not_in_trade")
    lines = state.lines
    side = state.side
    symbol = state.entry_symbol
    fill = state.entry_fill

    if now.time() >= FLATTEN_TIME:
        return Decision(
            action="exit",
            reason="eod",
            side=side,
            symbol=symbol,
            lots=1,
            lot_size=state.lot_size,
            quantity=state.lot_size,
            detail=f"exit eod t={now.time().isoformat(timespec='minutes')}",
        )

    if fill is not None and option_ltp is not None and option_ltp < 0.50 * fill:
        return Decision(
            action="exit",
            reason="premium_sl",
            side=side,
            symbol=symbol,
            lots=1,
            lot_size=state.lot_size,
            quantity=state.lot_size,
            fill_premium=option_ltp,
            detail=f"exit premium_sl fill={fill} ltp={option_ltp}",
        )

    if side == "CE" and lines.tgt_ce is not None and spot >= lines.tgt_ce:
        return Decision(
            action="exit",
            reason="target",
            side=side,
            symbol=symbol,
            lots=1,
            lot_size=state.lot_size,
            quantity=state.lot_size,
            detail=f"exit target spot={spot} tgt_ce={lines.tgt_ce}",
        )
    if side == "PE" and lines.tgt_pe is not None and spot <= lines.tgt_pe:
        return Decision(
            action="exit",
            reason="target",
            side=side,
            symbol=symbol,
            lots=1,
            lot_size=state.lot_size,
            quantity=state.lot_size,
            detail=f"exit target spot={spot} tgt_pe={lines.tgt_pe}",
        )

    if side == "CE" and lines.sl_ce_spot is not None and spot < lines.sl_ce_spot:
        return Decision(
            action="exit",
            reason="level_stop",
            side=side,
            symbol=symbol,
            lots=1,
            lot_size=state.lot_size,
            quantity=state.lot_size,
            detail=f"exit level_stop spot={spot} sl_ce_spot={lines.sl_ce_spot}",
        )
    if side == "PE" and lines.sl_pe_spot is not None and spot > lines.sl_pe_spot:
        return Decision(
            action="exit",
            reason="level_stop",
            side=side,
            symbol=symbol,
            lots=1,
            lot_size=state.lot_size,
            quantity=state.lot_size,
            detail=f"exit level_stop spot={spot} sl_pe_spot={lines.sl_pe_spot}",
        )

    return Decision(action="none", reason="hold", side=side, symbol=symbol)
