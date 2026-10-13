"""Whether a directional regime is strong enough to buy premium.

A daily side without strength is how this desk buys a put or call that then
sits and decays. The analyst still reports `trending` when +DI and -DI
disagree; this module is the supervisor gate that says the long is not
worth the theta.

The EMA120 book skips this gate: its entry is the 120-minute close through a
band, not a premium-buying ADX read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .config import Settings


@dataclass(frozen=True)
class StrengthVerdict:
    """Pass, or the one clause the trader should read."""

    ok: bool
    detail: str


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text


def _side(plus: float | None, minus: float | None) -> str | None:
    if plus is None or minus is None or plus == minus:
        return None
    return "bullish" if plus > minus else "bearish"


def assess_trend_strength(
    snapshot: Mapping[str, Any] | None, settings: Settings
) -> StrengthVerdict:
    """Fail closed on a present snapshot. Skip when the tick never attached one."""
    if not snapshot:
        return StrengthVerdict(ok=True, detail="not assessed")

    daily_adx = _num(snapshot.get("daily_adx"))
    daily_plus = _num(snapshot.get("daily_plus_di"))
    daily_minus = _num(snapshot.get("daily_minus_di"))
    ten_adx = _num(snapshot.get("ten_adx"))
    ten_plus = _num(snapshot.get("ten_plus_di"))
    ten_minus = _num(snapshot.get("ten_minus_di"))
    adx_floor = settings.trend_strength_daily_adx_min
    spread_floor = settings.trend_strength_di_spread_min
    ten_floor = settings.trend_strength_ten_adx_min

    if daily_adx is None:
        return StrengthVerdict(ok=False, detail="daily ADX was not readable")
    if daily_adx < adx_floor:
        return StrengthVerdict(
            ok=False,
            detail=f"daily ADX {_fmt(daily_adx)} is below the {_fmt(adx_floor)} floor",
        )

    if daily_plus is None or daily_minus is None:
        return StrengthVerdict(ok=False, detail="daily DMI has no side")
    daily_side = _side(daily_plus, daily_minus)
    if daily_side is None:
        return StrengthVerdict(ok=False, detail="daily DMI has no side")
    spread = abs(daily_plus - daily_minus)
    if spread < spread_floor:
        return StrengthVerdict(
            ok=False,
            detail=(f"daily DI spread {_fmt(spread)} is below the {_fmt(spread_floor)} floor"),
        )

    if ten_adx is None or ten_plus is None or ten_minus is None:
        return StrengthVerdict(ok=False, detail="10m DMI was not readable")
    ten_side = _side(ten_plus, ten_minus)
    if ten_side is None:
        return StrengthVerdict(ok=False, detail="10m DMI has no side")
    if ten_side != daily_side:
        return StrengthVerdict(
            ok=False,
            detail=f"10m side {ten_side} disagrees with daily {daily_side}",
        )
    if ten_adx < ten_floor:
        return StrengthVerdict(
            ok=False,
            detail=f"10m ADX {_fmt(ten_adx)} is below the {_fmt(ten_floor)} floor",
        )
    return StrengthVerdict(ok=True, detail="strong enough to buy premium")
