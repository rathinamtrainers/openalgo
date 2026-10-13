"""Strike reversals. Proxy only — not the official LTP Calculator Greeks formula."""

from __future__ import annotations

from .types import MIN_PREMIUM, REVERSAL_MODE


def call_reversal(strike: int, ce_ltp: float) -> float:
    """``eor_of(K) = K + CE_LTP(K)``. Label: proxy_k_plus_minus_ltp."""
    return float(strike) + float(ce_ltp)


def put_reversal(strike: int, pe_ltp: float) -> float:
    """``eos_of(K) = K - PE_LTP(K)``. Label: proxy_k_plus_minus_ltp."""
    return float(strike) - float(pe_ltp)


def usable_premium(ltp: float | None) -> bool:
    """Fail-closed: missing or LTP ≤ 1 is not a quote we may reverse."""
    return ltp is not None and ltp > MIN_PREMIUM


def reversal_label() -> str:
    return REVERSAL_MODE
