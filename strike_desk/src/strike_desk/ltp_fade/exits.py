"""Fade exit trailer and evaluation. Used by the monitor after a fade fill."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time

from .types import FLATTEN_TIME

_TRAILER = re.compile(
    r"\[ltp_fade\] side=(CE|PE) tgt_spot=([0-9.]+|none) sl_spot=([0-9.]+|none) "
    r"flatten=(\d{2}:\d{2}) reversal=([a-z0-9_]+)"
)


@dataclass(frozen=True)
class FadeExits:
    side: str
    tgt_spot: float | None
    sl_spot: float | None
    flatten: time
    reversal_mode: str


def format_trailer(
    side: str,
    tgt_spot: float | None,
    sl_spot: float | None,
    flatten: time = FLATTEN_TIME,
    reversal_mode: str = "proxy_k_plus_minus_ltp",
) -> str:
    tgt = "none" if tgt_spot is None else f"{tgt_spot:.2f}"
    sl = "none" if sl_spot is None else f"{sl_spot:.2f}"
    return (
        f"[ltp_fade] side={side} tgt_spot={tgt} sl_spot={sl} "
        f"flatten={flatten.strftime('%H:%M')} reversal={reversal_mode}"
    )


def parse_trailer(rationale: str | None) -> FadeExits | None:
    if not rationale:
        return None
    match = _TRAILER.search(rationale)
    if match is None:
        return None
    hour, minute = match.group(4).split(":")
    return FadeExits(
        side=match.group(1),
        tgt_spot=None if match.group(2) == "none" else float(match.group(2)),
        sl_spot=None if match.group(3) == "none" else float(match.group(3)),
        flatten=time(int(hour), int(minute)),
        reversal_mode=match.group(5),
    )


def fade_exit_reason(
    now: datetime,
    spot: float | None,
    option_ltp: float | None,
    fill: float | None,
    exits: FadeExits,
) -> str | None:
    """OR of premium 50%, spot target, spot level, 15:29. None = hold."""
    if now.tzinfo is None:
        return None
    clock = now.time()
    if clock >= exits.flatten:
        return "eod"
    if fill is not None and option_ltp is not None and option_ltp < 0.50 * fill:
        return "premium_sl"
    if spot is None:
        return None
    if exits.side == "CE":
        if exits.tgt_spot is not None and spot >= exits.tgt_spot:
            return "target"
        if exits.sl_spot is not None and spot < exits.sl_spot:
            return "level_stop"
    if exits.side == "PE":
        if exits.tgt_spot is not None and spot <= exits.tgt_spot:
            return "target"
        if exits.sl_spot is not None and spot > exits.sl_spot:
            return "level_stop"
    return None
