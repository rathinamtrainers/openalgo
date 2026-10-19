"""Public Chart of Accuracy brief for zerotouchalgo.com/ltp.

Facts and a plain-English paragraph from Strike Desk's CoA reading.
Never Hands SIDs, tokens, broker, Analyze, or house exits.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from .config import IST
from .ltp_coa import CoaSnapshot
from .ltp_coa_exits import in_entry_window

Stance = Literal["watching", "in a trade", "standing aside"]
OppStatus = Literal["waiting", "tagged", "blocked"]
OppSide = Literal["call", "put"]

DISCLAIMER = (
    "Map, not advice. Levels are Strike Desk's Chart of Accuracy reading "
    "(call-buy zone = floor minus 20 points, put-buy zone = ceiling plus 20 points), "
    "not the LTP Calculator app. ZeroTouchAlgo is not a SEBI-registered "
    "Investment Adviser or Research Analyst."
)

# Public mood. Scenario codes stay off the page.
_MOOD = {
    "1.1": "both the floor and the ceiling are holding",
    "1.2": "the floor is holding and the ceiling is softening lower",
    "1.3": "the floor is holding and the ceiling is softening higher",
    "1.4": "the floor is softening lower and the ceiling is holding",
    "1.5": "the floor is softening higher and the ceiling is holding",
    "1.6": "both sides are softening lower, so only a put buy can arm",
    "1.7": "both sides are softening higher, so only a call buy can arm",
    "1.8": "the map is mixed",
    "1.9": "the map is mixed",
}

_ASIDE = frozenset({"1.8", "1.9"})


@dataclass
class SessionTape:
    session_date: str
    open_spot: float | None = None
    high: float | None = None
    low: float | None = None
    tagged_eos: bool = False
    tagged_eor: bool = False
    tagged_support: bool = False
    tagged_resistance: bool = False


def update_tape(raw: dict[str, Any] | None, snap: CoaSnapshot, now: datetime) -> SessionTape:
    """Roll a session tape. New IST day starts a fresh tape."""
    day = now.astimezone(IST).date().isoformat()
    tape = _tape_from_dict(raw)
    if tape.session_date != day:
        tape = SessionTape(session_date=day)
    spot = float(snap.spot or 0)
    if spot > 0:
        if tape.open_spot is None:
            tape.open_spot = spot
        tape.high = spot if tape.high is None else max(tape.high, spot)
        tape.low = spot if tape.low is None else min(tape.low, spot)
    if snap.signal == "BUY_CE":
        tape.tagged_eos = True
    if snap.signal == "BUY_PE":
        tape.tagged_eor = True
    support = None if snap.support is None else snap.support.strike
    resistance = None if snap.resistance is None else snap.resistance.strike
    if support is not None and spot > 0 and abs(spot - support) <= 5:
        tape.tagged_support = True
    if resistance is not None and spot > 0 and abs(spot - resistance) <= 5:
        tape.tagged_resistance = True
    return tape


def build_brief(
    snap: CoaSnapshot,
    now: datetime,
    tape: SessionTape,
    *,
    in_window: bool | None = None,
) -> dict[str, Any]:
    """Public JSON for /ltp. No scenario codes, no private Hands fields."""
    clock = now.astimezone(IST).time()
    window = in_entry_window(clock) if in_window is None else in_window
    stance = _stance(snap, window)
    opportunities = _opportunities(snap, window)
    support = None if snap.support is None else snap.support.strike
    resistance = None if snap.resistance is None else snap.resistance.strike
    return {
        "as_of_ist": now.astimezone(IST).strftime("%d-%b-%Y %H:%M"),
        "session_date": tape.session_date,
        "stance": stance,
        "spot": _num(snap.spot),
        "support": _num(support),
        "resistance": _num(resistance),
        "eos": _num(snap.eos),
        "eor": _num(snap.eor),
        "mood": _MOOD.get(snap.scenario or "", "the map is not ready"),
        "body": _body(snap, tape, window),
        "opportunities": opportunities,
        "disclaimer": DISCLAIMER,
        "destination": "https://zerotouchalgo.com/ltp",
    }


def write_public_brief(path: Path, payload: dict[str, Any]) -> None:
    """Atomic world-readable write. Never raises to the Hands loop."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass


def tape_to_dict(tape: SessionTape) -> dict[str, Any]:
    return asdict(tape)


def _tape_from_dict(raw: dict[str, Any] | None) -> SessionTape:
    if not raw:
        return SessionTape(session_date="")
    return SessionTape(
        session_date=str(raw.get("session_date") or ""),
        open_spot=_maybe_float(raw.get("open_spot")),
        high=_maybe_float(raw.get("high")),
        low=_maybe_float(raw.get("low")),
        tagged_eos=bool(raw.get("tagged_eos")),
        tagged_eor=bool(raw.get("tagged_eor")),
        tagged_support=bool(raw.get("tagged_support")),
        tagged_resistance=bool(raw.get("tagged_resistance")),
    )


def _stance(snap: CoaSnapshot, window: bool) -> Stance:
    if snap.scenario is None or snap.scenario in _ASIDE:
        return "standing aside"
    if snap.signal in {"BUY_CE", "BUY_PE"}:
        return "in a trade"
    if not window:
        return "standing aside"
    return "watching"


def _opportunities(snap: CoaSnapshot, window: bool) -> list[dict[str, Any]]:
    if snap.scenario is None or snap.scenario in _ASIDE:
        return []
    rows: list[dict[str, Any]] = []
    if snap.allow_ce and snap.eos is not None:
        rows.append(_opportunity("call", snap, window, tagged=snap.signal == "BUY_CE"))
    if snap.allow_pe and snap.eor is not None:
        rows.append(_opportunity("put", snap, window, tagged=snap.signal == "BUY_PE"))
    return rows


def _opportunity(side: OppSide, snap: CoaSnapshot, window: bool, *, tagged: bool) -> dict[str, Any]:
    level = snap.eos if side == "call" else snap.eor
    relation = "at or below" if side == "call" else "at or above"
    status: OppStatus
    if tagged and window:
        status = "tagged"
    elif not window:
        status = "blocked"
    else:
        status = "waiting"
    wing = "call" if side == "call" else "put"
    return {
        "side": side,
        "status": status,
        "trigger": f"NIFTY {relation} {_level_text(level)}",
        "conditions": [
            f"the map still allows a {wing} buy",
            "between 09:20 and 14:00 IST",
            f"not already in a {wing} buy on this map",
        ],
    }


def _body(snap: CoaSnapshot, tape: SessionTape, window: bool) -> str:
    parts: list[str] = []
    spot_txt = _level_text(snap.spot)
    if tape.low is not None and tape.high is not None and tape.open_spot is not None:
        session = (
            f"In this session NIFTY has printed from {_level_text(tape.low)} "
            f"to {_level_text(tape.high)}, last at {spot_txt}"
        )
        extras: list[str] = []
        if tape.tagged_eos:
            extras.append("it already tagged the call-buy zone")
        if tape.tagged_eor:
            extras.append("it already tagged the put-buy zone")
        if extras:
            session += ", and " + " and ".join(extras)
        parts.append(session + ".")
    else:
        parts.append(f"NIFTY is at {spot_txt}.")

    mood = _MOOD.get(snap.scenario or "", "the map is not ready")
    support = None if snap.support is None else snap.support.strike
    resistance = None if snap.resistance is None else snap.resistance.strike
    if support is not None and resistance is not None:
        parts.append(
            f"Right now {mood}, with a floor at {_level_text(support)} "
            f"and a ceiling at {_level_text(resistance)}."
        )
    else:
        parts.append(f"Right now {mood}.")

    if snap.scenario is None or snap.scenario in _ASIDE:
        parts.append("No buy is on the map until one side firms up.")
    else:
        bits: list[str] = []
        if snap.allow_ce and snap.eos is not None:
            bits.append(f"a call buy needs a print at or below {_level_text(snap.eos)}")
        if snap.allow_pe and snap.eor is not None:
            bits.append(f"a put buy needs a print at or above {_level_text(snap.eor)}")
        if bits:
            nxt = "What is likely next: " + "; ".join(bits)
            if not window:
                nxt += ". The buy window is 09:20–14:00 IST — next look then"
            parts.append(nxt + ".")
    return " ".join(parts)


def _level_text(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{int(round(value)):,}"


def _num(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _maybe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
