"""Public Chart of Accuracy brief for zerotouchalgo.com/ltp.

Facts and a plain-English paragraph from Strike Desk's CoA reading.
Never Hands SIDs, tokens, broker, Analyze, or house exits.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from .config import IST
from .ltp_coa import NIFTY_STRIKE_STEP, CoaSnapshot
from .ltp_coa_exits import in_entry_window

logger = logging.getLogger(__name__)

Stance = Literal["watching", "in a trade", "standing aside"]
OppStatus = Literal["waiting", "tagged", "blocked"]
OppSide = Literal["call", "put"]

def _disclaimer(extension_pts: float = 20.0) -> str:
    pts = int(extension_pts) if float(extension_pts).is_integer() else extension_pts
    return (
        "Map, not advice. Levels are Strike Desk's Chart of Accuracy reading "
        f"(call-buy zone = floor minus {pts} points, put-buy zone = ceiling plus {pts} points), "
        "not the LTP Calculator app. ZeroTouchAlgo is not a SEBI-registered "
        "Investment Adviser or Research Analyst."
    )


DISCLAIMER = _disclaimer(20.0)

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
    index_name: str = "NIFTY",
    strike_step: float = NIFTY_STRIKE_STEP,
    market: str = "nifty",
    extension_pts: float = 20.0,
    hands_posted: str | None = None,
) -> dict[str, Any]:
    """Public JSON for /ltp. No scenario codes, no private Hands fields."""
    clock = now.astimezone(IST).time()
    window = in_entry_window(clock) if in_window is None else in_window
    stance = _stance(snap, window)
    opportunities = _opportunities(
        snap, window, index_name, strike_step, hands_posted=hands_posted
    )
    support = None if snap.support is None else snap.support.strike
    resistance = None if snap.resistance is None else snap.resistance.strike
    do_now = _cta(snap, window, index_name)
    line = _line(snap, window, do_now)
    chip = _chip(snap, window, hands_posted=hands_posted)
    last = _num(snap.spot)
    computed_at = now.astimezone(IST).isoformat(timespec="seconds")
    payload = {
        "as_of_ist": now.astimezone(IST).strftime("%d %b %Y, %H:%M IST"),
        "computed_at": computed_at,
        "session_date": tape.session_date,
        "market": market,
        "index": index_name,
        "stance": stance,
        "spot": last,
        "last": last,
        "support": _num(support),
        "resistance": _num(resistance),
        "eos": _num(snap.eos),
        "eor": _num(snap.eor),
        "mood": _MOOD.get(snap.scenario or "", "the map is not ready"),
        "action": do_now,
        "do_now": do_now,
        "cta": do_now,
        "now": line,
        "line": line,
        "line_source": "template",
        "scenario_label": chip,
        "body": line + " " + _body(snap, tape, window, index_name),
        "opportunities": opportunities,
        "opportunity_source": "coa",
        "disclaimer": _disclaimer(extension_pts),
        "destination": "https://zerotouchalgo.com/ltp",
        "watch_for": _watch_for(snap),
        "waiting_for": _waiting_for(snap),
        "building": _building(snap),
    }
    if tape.open_spot is not None:
        payload["open"] = _num(tape.open_spot)
    if tape.high is not None:
        payload["high"] = _num(tape.high)
    if tape.low is not None:
        payload["low"] = _num(tape.low)
    if last is not None and tape.open_spot:
        payload["change_vs_open"] = round(last - tape.open_spot, 2)
    strike = _featured_strike(snap, strike_step)
    if strike:
        payload["strike"] = strike
    return payload


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


def push_public_brief(payload: dict[str, Any], settings: Any | None) -> None:
    """POST the live map to zerotouchalgo.com/ltp. Never logs the token."""
    if settings is None:
        return
    url = str(getattr(settings, "coa_publish_url", "") or "").strip()
    secret = getattr(settings, "coa_publish_token", None)
    token = ""
    if secret is not None:
        raw = secret.get_secret_value() if hasattr(secret, "get_secret_value") else str(secret)
        token = str(raw).strip()
    if not url or not token:
        return
    try:
        import httpx
    except ImportError:
        return
    try:
        with httpx.Client(timeout=8.0) as client:
            response = client.post(
                url,
                headers={
                    "authorization": f"Bearer {token}",
                    "content-type": "application/json",
                },
                json=payload,
            )
        if response.status_code >= 400:
            logger.warning("public brief push skipped http=%s", response.status_code)
    except Exception:
        logger.exception("public brief push failed")


def publish_public_brief(
    path: Path, payload: dict[str, Any], settings: Any | None = None
) -> None:
    """Write locally, then push so /ltp shows this map, not a leftover one."""
    write_public_brief(path, payload)
    try:
        push_public_brief(payload, settings)
    except Exception:
        logger.exception("public brief push raised")


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


def _opportunities(
    snap: CoaSnapshot,
    window: bool,
    index_name: str,
    strike_step: float,
    *,
    hands_posted: str | None = None,
) -> list[dict[str, Any]]:
    if snap.scenario is None or snap.scenario in _ASIDE:
        return []
    posted = (hands_posted or "").upper()
    rows: list[dict[str, Any]] = []
    if snap.allow_ce and snap.eos is not None:
        rows.append(
            _opportunity(
                "call",
                snap,
                window,
                tagged=posted == "CE",
                index_name=index_name,
                strike_step=strike_step,
            )
        )
    if snap.allow_pe and snap.eor is not None:
        rows.append(
            _opportunity(
                "put",
                snap,
                window,
                tagged=posted == "PE",
                index_name=index_name,
                strike_step=strike_step,
            )
        )
    return rows


def _opportunity(
    side: OppSide,
    snap: CoaSnapshot,
    window: bool,
    *,
    tagged: bool,
    index_name: str,
    strike_step: float,
) -> dict[str, Any]:
    level = snap.eos if side == "call" else snap.eor
    relation = "at or below" if side == "call" else "at or above"
    status: OppStatus
    if tagged and window:
        status = "tagged"
    else:
        # After hours stays "waiting" so zerotouchalgo.com/ltp does not
        # paint the hardcoded Invalidation badge (it maps status=blocked).
        status = "waiting"
    wing = "call" if side == "call" else "put"
    labels = {
        "tagged": "Buy armed — this is the go",
        "waiting": "Not reached — wait" if window else "Window closed — wait",
        "blocked": "Window closed — wait",
    }
    trigger = f"{index_name} {relation} {_level_text(level)}"
    conditions = (
        f"the map still allows a {wing} buy. between 09:20 and 14:00 IST. "
        f"not already in a {wing} buy on this map."
    )
    strike = _option_strike(level, strike_step)
    entry = f"{relation} {_level_text(level)}"
    return {
        "side": side,
        "status": status,
        "label": labels[status],
        "trigger": trigger,
        "conditions": conditions,
        "if_event": trigger,
        "action": f"Buy {wing}",
        "entry": entry,
        **({"strike": strike} if strike else {}),
    }


def _cta(snap: CoaSnapshot, window: bool, index_name: str) -> str:
    """One sentence: what to do right now. No jargon."""
    spot = _level_text(snap.spot)
    if snap.scenario is None or snap.scenario in _ASIDE:
        return (
            f"Do nothing. The map is mixed at {spot}, so neither a call "
            "nor a put buy is allowed."
        )
    if snap.signal == "BUY_CE":
        if not window:
            return (
                f"Do nothing. Cash is closed. The last map had a call buy armed at {spot}."
            )
        return f"A call buy is armed at {spot}. That is the only action — do not buy a put."
    if snap.signal == "BUY_PE":
        if not window:
            return (
                f"Do nothing. Cash is closed. The last map had a put buy armed at {spot}."
            )
        return f"A put buy is armed at {spot}. That is the only action — do not buy a call."
    waits: list[str] = []
    if snap.allow_ce and snap.eos is not None:
        waits.append(
            f"a call buy needs {index_name} at or below {_level_text(snap.eos)}"
        )
    if snap.allow_pe and snap.eor is not None:
        waits.append(
            f"a put buy needs {index_name} at or above {_level_text(snap.eor)}"
        )
    if not window:
        if waits:
            return "Do nothing. Cash is closed. Next session, " + " or ".join(waits) + "."
        return "Do nothing. Cash is closed, and no buy is on this map."
    if not waits:
        return f"Do nothing. {index_name} is {spot} and no buy is allowed on this map."
    return (
        f"Do nothing yet. {index_name} is {spot}. Wait until "
        + " or ".join(waits)
        + "."
    )


def _chip(snap: CoaSnapshot, window: bool, *, hands_posted: str | None = None) -> str:
    """Website chip. CALL/PUT TAGGED only after a successful Hands webhook."""
    del snap, window
    posted = (hands_posted or "").upper()
    if posted == "CE":
        return "CALL TAGGED"
    if posted == "PE":
        return "PUT TAGGED"
    return "WATCHING"


def _line(snap: CoaSnapshot, window: bool, do_now: str) -> str:
    """Running sentence the /ltp page can show as the live caption."""
    extra = ""
    if not window:
        extra = " This is not an order."
    elif snap.signal == "FLAT":
        extra = " Nothing is an order until one of those prices prints."
    return do_now + extra


def _body(snap: CoaSnapshot, tape: SessionTape, window: bool, index_name: str) -> str:
    parts: list[str] = []
    spot_txt = _level_text(snap.spot)
    if tape.low is not None and tape.high is not None and tape.open_spot is not None:
        session = (
            f"In this session {index_name} has printed from {_level_text(tape.low)} "
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
        parts.append(f"{index_name} is at {spot_txt}.")

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


def _option_strike(level: float | None, strike_step: float = NIFTY_STRIKE_STEP) -> str | None:
    if level is None:
        return None
    step = strike_step or NIFTY_STRIKE_STEP
    return str(int(round(float(level) / step) * step))


def _featured_strike(
    snap: CoaSnapshot, strike_step: float = NIFTY_STRIKE_STEP
) -> str | None:
    if snap.signal == "BUY_CE":
        return _option_strike(snap.eos, strike_step)
    if snap.signal == "BUY_PE":
        return _option_strike(snap.eor, strike_step)
    if snap.allow_pe and not snap.allow_ce:
        return _option_strike(snap.eor, strike_step)
    if snap.allow_ce and not snap.allow_pe:
        return _option_strike(snap.eos, strike_step)
    return _option_strike(snap.atm, strike_step)


def _watch_for(snap: CoaSnapshot) -> str:
    bits: list[str] = []
    if snap.allow_pe and snap.eor is not None:
        bits.append(f"{_level_text(snap.eor)} put buy")
    if snap.allow_ce and snap.eos is not None:
        bits.append(f"{_level_text(snap.eos)} call buy")
    return " / ".join(bits) if bits else "no buy on this map"


def _waiting_for(snap: CoaSnapshot) -> str:
    bits: list[str] = []
    if snap.allow_pe and snap.eor is not None:
        bits.append(f"put buy at or above {_level_text(snap.eor)}")
    if snap.allow_ce and snap.eos is not None:
        bits.append(f"call buy at or below {_level_text(snap.eos)}")
    if not bits:
        return "no buy on this map — waiting is not an order"
    return "; ".join(bits) + " — waiting is not an order"


def _building(snap: CoaSnapshot) -> dict[str, str]:
    support = None if snap.support is None else snap.support.strike
    resistance = None if snap.resistance is None else snap.resistance.strike
    out: dict[str, str] = {}
    if snap.allow_ce and support is not None and snap.eos is not None:
        out["call"] = f"{_level_text(support)}–{_level_text(snap.atm or support)}, waiting"
    else:
        out["call"] = "no call buy on this map"
    if snap.allow_pe and resistance is not None and snap.eor is not None:
        out["put"] = f"{_level_text(snap.atm or resistance)}–{_level_text(resistance)}, waiting"
    else:
        out["put"] = "no put buy on this map"
    return out
