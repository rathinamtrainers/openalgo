"""Locked v1 knobs. Trading rules live in the rulebook, not here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class LtpFadeConfig:
    symbol: str = "NIFTY"
    exchange: str = "NFO"
    index_symbol: str = "NIFTY"
    index_exchange: str = "NSE_INDEX"
    expiry: str = "current_week"
    lots: int = 1
    product: str = "NRML"
    freeze: time = time(9, 21)
    last_entry: time = time(15, 0)
    flatten: time = time(15, 29)
    sr_mode: str = "volume"
    reversal_mode: str = "k_plus_minus_ltp"
    strike_step: int = 50
    walk_cap_steps: int = 20
    hysteresis: str = "first_touch"
