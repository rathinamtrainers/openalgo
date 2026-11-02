"""Which cash-index expiries and dates to keep in Historify for CoA replay.

The live mind needs the front listed ATM band (Nifty weekly, Banknifty monthly,
Sensex weekly). Expired contracts vanish from the broker master, so this archive
must run while they are still listed.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from .ltp_coa import NIFTY_STRIKE_STEP

LOOKBACK_DAYS = 3
STRIKE_PAD = 10


def lookback_window(today: date, days: int = LOOKBACK_DAYS) -> tuple[date, date]:
    """Inclusive calendar window ending today."""
    if days < 0:
        raise ValueError("lookback days must be >= 0")
    return today - timedelta(days=days), today


def weeklies_to_archive(today: date, listed: Sequence[date]) -> list[date]:
    """Nearest listed expiry still on the master. On expiry day, also keep the next."""
    upcoming = [day for day in listed if day >= today]
    if not upcoming:
        return []
    keep = [upcoming[0]]
    if upcoming[0] == today and len(upcoming) > 1:
        keep.append(upcoming[1])
    return keep


def strike_band(
    low: float,
    high: float,
    pad: int = STRIKE_PAD,
    *,
    step: float = NIFTY_STRIKE_STEP,
) -> tuple[float, float]:
    """ATM-outward band covering a spot range, padded by ``pad`` strikes."""
    width = float(step or NIFTY_STRIKE_STEP)
    lo = round(low / width) * width - pad * width
    hi = round(high / width) * width + pad * width
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi
