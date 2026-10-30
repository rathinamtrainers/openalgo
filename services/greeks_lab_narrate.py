"""One-line Lab readout. Template first, optional LLM polish.

Never invent numbers. Never an order. If the key is missing, the call fails,
or the rewrite adds a figure, keep the template.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)

MODEL = "claude-haiku-4-5"
CACHE_TTL_SEC = 120.0
_FORBIDDEN = (
    "black-76",
    "black76",
    "theta",
    "greeks",
    "quadshield",
    "adx",
    "atr",
    "sigma",
    "straddle",
    "placeorder",
    "webhook",
    "hands",
)

_SYSTEM = (
    "Rewrite the facts as ONE short sentence a person who is not a trader "
    "can act on, then end with 'Not an order.' "
    "Keep every number exactly as given, including the ₹ sign. "
    "Do not add numbers, tickers, or times. "
    "Do not tell them to click buy or sell. "
    "You may say which map is better: wait, buy a side, or let premium shrink. "
    "Never use: theta, Greeks, IV, ATM, QuadShield, ADX, straddle, sigma, Black-76."
)

Invoke = Callable[[str, str], str]
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def attach_readout(payload: dict[str, Any], *, invoke: Invoke | None = None) -> dict[str, Any]:
    """Add ``readout``. Never raises."""
    try:
        template = _template_line(payload)
        if not template:
            return payload
        polished = _polish(template, invoke=invoke)
        payload["readout"] = polished
    except Exception:
        logger.exception("Greeks lab readout failed")
        payload["readout"] = {
            "source": "template",
            "line": "The next-hour model is a map, not a forecast. Not an order.",
            "sit_still": None,
        }
    return payload


def _path_ends(path: list[dict[str, Any]] | None) -> tuple[float | None, float | None]:
    if not path:
        return None, None
    start = path[0].get("price")
    end = path[-1].get("price")
    try:
        return float(start), float(end)
    except (TypeError, ValueError):
        return None, None


def _rupee(value: float) -> str:
    return f"₹{abs(value):.1f}"


def _chip_state(insights: dict[str, Any] | None, name: str) -> str:
    block = (insights or {}).get(name) or {}
    return str(block.get("state") or "")


def _action(insights: dict[str, Any] | None) -> str:
    decay = _chip_state(insights, "decay")
    session = _chip_state(insights, "session")
    shield = _chip_state(insights, "quadshield")
    if shield == "pause" or decay == "off" or session == "buying_day":
        return "The better map is to wait, or to buy a side if you already trade that way — not to sell premium."
    if shield == "pause_shorts":
        return "The better map is to pause new shorts and wait."
    if decay == "on" and shield == "run" and session == "selling_day":
        return "The better map is to let premium shrink, not to chase a side."
    return "The better map is to wait."


def _leg_clause(name: str, start: float | None, end: float | None) -> str | None:
    if start is None or end is None:
        return None
    lost = start - end
    if lost >= 0.05:
        return f"the {name} goes {start:.1f} to {end:.1f} (loses {_rupee(lost)})"
    if lost <= -0.05:
        return f"the {name} goes {start:.1f} to {end:.1f} (gains {_rupee(lost)})"
    return f"the {name} stays near {start:.1f}"


def _template_line(payload: dict[str, Any]) -> str:
    underlying = str(payload.get("underlying") or "this index")
    horizon = int(payload.get("horizon_minutes") or 60)
    projections = payload.get("projections") or {}
    ce_start, ce_end = _path_ends((projections.get("ce") or {}).get("theta_path"))
    pe_start, pe_end = _path_ends((projections.get("pe") or {}).get("theta_path"))
    clauses = [
        part
        for part in (
            _leg_clause("call", ce_start, ce_end),
            _leg_clause("put", pe_start, pe_end),
        )
        if part
    ]
    if clauses:
        joined = " and ".join(clauses)
        sit = f"If {underlying} does not move for {horizon} min, {joined}."
    else:
        sit = f"If {underlying} does not move for {horizon} min, premium barely changes."
    tiny = False
    for start, end in ((ce_start, ce_end), (pe_start, pe_end)):
        if start and end and start > 0 and abs(start - end) / start < 0.02:
            tiny = True
            break
    if tiny:
        sit += " That is almost no shrink."
    return f"{sit} {_action(payload.get('insights') if isinstance(payload.get('insights'), dict) else None)} Not an order."


def _polish(template: str, *, invoke: Invoke | None = None) -> dict[str, Any]:
    base = {"source": "template", "line": template, "sit_still": template.split(" The better map")[0]}
    injected = invoke is not None
    worker = invoke if injected else _default_invoke()
    if worker is None:
        return base
    cache_key = template
    now = time.monotonic()
    if not injected:
        hit = _CACHE.get(cache_key)
        if hit and now - hit[0] < CACHE_TTL_SEC:
            return hit[1]
    try:
        rewritten = worker(_SYSTEM, "Facts, already safe to show:\n" + template).strip()
    except Exception:
        logger.exception("Greeks lab readout polish failed")
        return base
    rewritten = rewritten.replace("\n", " ").strip().strip('"')
    if not _grounded(rewritten, template):
        return base
    if "not an order" not in rewritten.lower():
        rewritten = rewritten.rstrip(".") + ". Not an order."
    out = {"source": "llm", "line": rewritten, "sit_still": base["sit_still"]}
    if not injected:
        _CACHE[cache_key] = (now, out)
    return out


def _grounded(rewritten: str, template: str) -> bool:
    if not rewritten or len(rewritten) < 40 or len(rewritten) > 420:
        return False
    lower = rewritten.lower()
    if any(token in lower for token in _FORBIDDEN):
        return False
    allowed = set(_numbers(template))
    got = set(_numbers(rewritten))
    extra = [num for num in got if num not in allowed]
    if extra:
        return False
    if allowed and not (got & allowed):
        return False
    return True


def _numbers(text: str) -> list[str]:
    return [token.rstrip(",") for token in re.findall(r"\d[\d,]*\.?\d*", text)]


def _api_key() -> str | None:
    for name in ("STRIKE_DESK_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    for path in (
        Path("/opt/strike-desk/current/.env"),
        Path("/opt/strike-desk/current/strike_desk/.env"),
    ):
        key = _key_from_env_file(path)
        if key:
            return key
    return None


def _key_from_env_file(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() != "STRIKE_DESK_ANTHROPIC_API_KEY":
                continue
            return value.strip().strip("'").strip('"') or None
    except Exception:
        logger.exception("could not read lab readout key file")
    return None


def _default_invoke() -> Invoke | None:
    key = _api_key()
    if not key:
        return None
    try:
        from utils.httpx_client import get_httpx_client
    except Exception:
        return None

    def _call(system: str, user: str) -> str:
        client = get_httpx_client()
        response = client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 160,
                "temperature": 0,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=8.0,
        )
        if response.status_code >= 400:
            logger.info("lab readout polish skipped http=%s", response.status_code)
            raise RuntimeError("polish http error")
        body = response.json()
        chunks = body.get("content") or []
        return "".join(str(part.get("text") or "") for part in chunks if isinstance(part, dict))

    return _call
