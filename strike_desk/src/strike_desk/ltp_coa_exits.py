"""CoA live/replay exits. Mind-owned — Tradetron only receives ltp_exit.

Public CoA is an entry map (EOS/EOR + nine scenarios). Exits below are the
locked house overlay: opposite S/R, break of the entry extension, live-level
trail after reclaim, then premium TSL arm +40% / lock +20% only after reclaim,
70% fuse, 15:15 flatten. Re-entry 5 minutes after a non-session exit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from .ltp_coa import CoaSnapshot

FLAT_TIME = time(15, 15)
ENTRY_OPEN = time(9, 20)
ENTRY_CLOSE = time(14, 0)

EOS_SL_BUFFER = 15.0
EOR_SL_BUFFER = 15.0
RECLAIM_TRAIL = 10.0
PREMIUM_FUSE = 0.70
TSL_ARM = 1.40
TSL_LOCK = 1.20
TSL_GIVEBACK = TSL_ARM - TSL_LOCK  # 0.20 of entry from peak
REENTRY = timedelta(minutes=5)


@dataclass
class OpenTrade:
    side: str
    entry: float
    entry_ts: datetime
    peak: float
    eos: float | None
    eor: float | None
    support: float | None
    resistance: float | None
    scenario: str | None
    reclaimed: bool = False
    strike: float = 0.0

    def note_ltp(self, ltp: float) -> None:
        if ltp > self.peak:
            self.peak = ltp


def can_reenter(now: datetime, last_exit: datetime | None) -> bool:
    if last_exit is None:
        return True
    return now >= last_exit + REENTRY


def in_entry_window(
    clock: time, *, start: time = ENTRY_OPEN, end: time = ENTRY_CLOSE
) -> bool:
    return start <= clock <= end


def decide_exit(
    clock: time,
    spot: float,
    ltp: float,
    trade: OpenTrade,
    live: CoaSnapshot,
    *,
    spot_sl_buffer: float = EOS_SL_BUFFER,
    reclaim_trail: float = RECLAIM_TRAIL,
    flatten_at: time | None = FLAT_TIME,
) -> str | None:
    """First matching rule wins. Empty string never returned."""
    if flatten_at is not None and clock >= flatten_at:
        return f"{flatten_at.strftime('%H:%M')} flatten"
    if ltp <= 0 or trade.entry <= 0:
        return None

    if _scenario_kill(trade.side, live):
        return f"scenario kill CoA {live.scenario or '?'}"

    if _spot_target(trade, live, spot):
        return "spot target opposite S/R"

    if _hard_spot_sl(trade, spot, spot_sl_buffer):
        return "spot SL extension broken"

    _mark_reclaim(trade, live, spot)
    if _reclaim_trail(trade, live, spot, reclaim_trail):
        return "spot TSL live S/R"

    if ltp <= trade.entry * PREMIUM_FUSE:
        return "premium fuse"

    tsl = _premium_tsl(trade, ltp)
    if tsl:
        return tsl
    return None


def _scenario_kill(side: str, live: CoaSnapshot) -> bool:
    if live.scenario in {"1.8", "1.9"}:
        return True
    if side == "CE" and not live.allow_ce:
        return True
    if side == "PE" and not live.allow_pe:
        return True
    return False


def _spot_target(trade: OpenTrade, live: CoaSnapshot, spot: float) -> bool:
    resistance = _level(live.resistance) or trade.resistance
    support = _level(live.support) or trade.support
    if trade.side == "CE" and resistance is not None and spot >= resistance:
        return True
    if trade.side == "PE" and support is not None and spot <= support:
        return True
    return False


def _hard_spot_sl(trade: OpenTrade, spot: float, buffer: float = EOS_SL_BUFFER) -> bool:
    if trade.side == "CE" and trade.eos is not None:
        return spot < trade.eos - buffer
    if trade.side == "PE" and trade.eor is not None:
        return spot > trade.eor + buffer
    return False


def _mark_reclaim(trade: OpenTrade, live: CoaSnapshot, spot: float) -> None:
    support = _level(live.support) or trade.support
    resistance = _level(live.resistance) or trade.resistance
    if trade.side == "CE" and support is not None and spot > support:
        trade.reclaimed = True
    if trade.side == "PE" and resistance is not None and spot < resistance:
        trade.reclaimed = True


def _reclaim_trail(
    trade: OpenTrade,
    live: CoaSnapshot,
    spot: float,
    trail: float = RECLAIM_TRAIL,
) -> bool:
    if not trade.reclaimed:
        return False
    support = _level(live.support) or trade.support
    resistance = _level(live.resistance) or trade.resistance
    if trade.side == "CE" and support is not None:
        return spot < support - trail
    if trade.side == "PE" and resistance is not None:
        return spot > resistance + trail
    return False


def _premium_tsl(trade: OpenTrade, ltp: float) -> str | None:
    if not trade.reclaimed:
        return None
    if trade.peak < trade.entry * TSL_ARM:
        return None
    floor = trade.entry * TSL_LOCK
    chase = trade.peak - TSL_GIVEBACK * trade.entry
    stop = max(floor, chase)
    if ltp <= stop:
        return "premium TSL lock 20% chase 40%"
    return None


def _level(level) -> float | None:
    if level is None:
        return None
    return float(level.strike)
