"""Typed snapshots for the 9:20 fade. No I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Literal

StateName = Literal["PREOPEN", "FREEZING", "FROZEN", "NO_TRADE", "IN_TRADE", "DONE"]
Side = Literal["NONE", "CE", "PE"]
ExitReason = Literal["premium_sl", "target", "level_stop", "eod", ""]

STRIKE_STEP = 50
MIN_PREMIUM = 1.0
WALK_CAP_STEPS = 20
REVERSAL_MODE = "proxy_k_plus_minus_ltp"

FREEZE_TIME = time(9, 21)
FREEZE_DEADLINE = time(9, 30)
LAST_ENTRY = time(15, 0)
FLATTEN_TIME = time(15, 29)


@dataclass(frozen=True)
class StrikeRow:
    """One weekly strike with the fields the walk and reversals need."""

    strike: int
    ce_ltp: float | None
    pe_ltp: float | None
    ce_volume: float | None
    pe_volume: float | None
    ce_symbol: str = ""
    pe_symbol: str = ""
    lot_size: int = 0
    ce_oi: float | None = None
    pe_oi: float | None = None


@dataclass(frozen=True)
class ChainSnapshot:
    """Spot + current weekly chain at one instant."""

    spot: float
    expiry: str
    rows: tuple[StrikeRow, ...]
    as_of: datetime


@dataclass(frozen=True)
class FrozenLines:
    """09:21 lines. Reversals are proxy_k_plus_minus_ltp."""

    spot_at_freeze: float
    atm: int
    expiry: str
    resistance: int | None
    support: int | None
    r_volume: float | None
    s_volume: float | None
    eor: float | None
    eor1: float | None
    eor2: float | None
    eos: float | None
    eos1: float | None
    eos2: float | None
    tgt_ce: float | None
    tgt_pe: float | None
    sl_ce_spot: float | None
    sl_pe_spot: float | None
    call_ok: bool
    put_ok: bool
    reversal_mode: str = REVERSAL_MODE
    as_of: datetime | None = None


@dataclass
class SessionState:
    """Persisted per IST session. Reset on a new calendar date or PREOPEN."""

    session_date: date | None = None
    state: StateName = "PREOPEN"
    lines: FrozenLines | None = None
    entered_today: bool = False
    side: Side = "NONE"
    entry_symbol: str = ""
    entry_fill: float | None = None
    entry_time: datetime | None = None
    lot_size: int = 0
    freeze_attempts: int = 0
    logs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Decision:
    """What the engine should do this tick. Paper or live applies it."""

    action: Literal["none", "enter", "exit"]
    reason: str
    side: Side = "NONE"
    symbol: str = ""
    lots: int = 1
    lot_size: int = 0
    quantity: int = 0
    fill_premium: float | None = None
    detail: str = ""
