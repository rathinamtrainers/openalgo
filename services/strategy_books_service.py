"""Read-only My Strategies list: registry identity + mind snapshot status."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from database.strategy_books_db import ensure_missing_books, list_books
from services.desk_decisions_service import last_decision
from services.stack_status_service import _read_desk_snapshot
from utils.logging import get_logger

logger = get_logger(__name__)

# CoA steps every 5 minutes; two missed beats means the unit is down.
COA_HEARTBEAT_STALE_S = 600.0


def _ofa_signal_fallback() -> str:
    """Read live OFA signal from desk state when the stack snapshot omits it."""
    raw = os.getenv("STRIKE_DESK_STATE_DIR")
    candidates: list[Path] = []
    if raw:
        base = Path(raw)
        candidates.extend([base / "ofa_signal.txt", base / "ofa_exec.json"])
    candidates.extend(
        [
            Path("/var/lib/strike-desk/ofa_signal.txt"),
            Path("/var/lib/strike-desk/ofa_exec.json"),
        ]
    )
    for path in candidates:
        try:
            if not path.is_file():
                continue
            if path.suffix == ".txt":
                value = path.read_text(encoding="utf-8").strip().upper()
                if value in {"LONG", "SHORT", "FLAT"}:
                    return value
            else:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    value = str(data.get("acted_signal") or "FLAT").upper()
                    if value in {"LONG", "SHORT", "FLAT"}:
                        return value
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
    return "FLAT"


KIND_LABELS = {
    "analyzer": "Analyzer",
    "coa": "CoA",
    "ofa": "OFA",
    "strikeshield": "StrikeShield",
    "overlay": "Overlay",
    "webhook": "Webhook",
    "python": "Python",
    "flow": "Flow",
}

TARGET_LABELS = {
    "tradetron-hands": "Tradetron Hands",
    "openalgo": "OpenAlgo Analyze",
    "openalgo-tradetron": "OpenAlgo Analyze + Tradetron Hands",
    "none": "None",
}

# placeorder `strategy` tags → My Strategies card names
ORDER_STRATEGY_LABELS = {
    "OFA": "Strike Desk OFA NIFTY",
    "COA": "Strike Desk CoA NIFTY",
    "strike-desk-NIFTY": "Strike Desk NIFTY Analyzer",
    "AUTO_SQUARE_OFF": "Auto square-off",
}


def is_analyzer_order_strategy(tag: str | None) -> bool:
    """True when a placeorder strategy tag belongs to the NIFTY Analyzer book."""
    text = str(tag or "").strip()
    if not text:
        return False
    if text == "strike-desk-NIFTY" or text.startswith("strike-desk-NIFTY"):
        return True
    return text.startswith("strike-desk:")


def is_ofa_order_strategy(tag: str | None) -> bool:
    """True when a placeorder strategy tag belongs to the OFA NIFTY book."""
    text = str(tag or "").strip()
    if not text:
        return False
    return text == "OFA" or text.startswith("OFA:") or text.startswith("OFA-")


def is_coa_order_strategy(tag: str | None) -> bool:
    """True when a placeorder strategy tag belongs to the CoA NIFTY book."""
    text = str(tag or "").strip()
    if not text:
        return False
    return text == "COA" or text.startswith("COA:") or text.startswith("COA-")


def label_order_strategies(tags: list[str] | tuple[str, ...] | None) -> str:
    """Turn one or more placeorder strategy tags into a Positions-page label."""
    labels: list[str] = []
    for raw in tags or []:
        tag = str(raw or "").strip()
        if not tag:
            continue
        if tag in ORDER_STRATEGY_LABELS:
            label = ORDER_STRATEGY_LABELS[tag]
        elif tag.startswith("strike-desk:") or tag.startswith("strike-desk-"):
            label = ORDER_STRATEGY_LABELS["strike-desk-NIFTY"]
        elif tag.startswith("OFA:") or tag.startswith("OFA-"):
            label = ORDER_STRATEGY_LABELS["OFA"]
        elif tag.startswith("COA:") or tag.startswith("COA-"):
            label = ORDER_STRATEGY_LABELS["COA"]
        else:
            label = tag
        if label not in labels:
            labels.append(label)
    return " · ".join(labels)


def _desk_state_candidates(*names: str) -> list[Path]:
    raw = os.getenv("STRIKE_DESK_STATE_DIR")
    candidates: list[Path] = []
    if raw:
        base = Path(raw)
        candidates.extend(base / name for name in names)
    lib = Path("/var/lib/strike-desk")
    candidates.extend(lib / name for name in names)
    return candidates


def _file_age_s(path: Path) -> float | None:
    try:
        if path.is_file():
            return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None
    return None


def coa_heartbeat_age_s() -> float | None:
    """Seconds since coa.heartbeat was last written, or None if missing."""
    for path in _desk_state_candidates("coa.heartbeat"):
        age = _file_age_s(path)
        if age is not None:
            return age
    return None


def coa_live_state() -> dict[str, Any]:
    """Latest coa_live.json (open trade / last exit). Empty dict if unread."""
    for path in _desk_state_candidates("coa_live.json"):
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
    return {}


def _coa_open_side(state: dict[str, Any] | None = None) -> str | None:
    payload = state if state is not None else coa_live_state()
    open_trade = payload.get("open")
    if not isinstance(open_trade, dict):
        return None
    side = str(open_trade.get("side") or "").upper()
    return side if side in {"CE", "PE"} else None


def _book_phrase(snapshot: dict[str, Any]) -> str:
    set1 = bool(snapshot.get("set1"))
    set2 = bool(snapshot.get("set2open"))
    if set1 and set2:
        return "wings + shorts"
    if set1:
        return "wings open"
    if set2:
        return "shorts open"
    return "flat"


def _annotate(book: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    out = dict(book)
    out["kind_label"] = KIND_LABELS.get(book["kind"], book["kind"])
    out["execution_label"] = TARGET_LABELS.get(book["execution_target"], book["execution_target"])
    out["audit_href"] = f"/strategy/books/{book['id']}"
    desk_up = str(snapshot.get("strike_desk") or "") == "active"

    if book.get("parked"):
        out["run_status"] = "parked"
        out["detail"] = book.get("notes") or "parked"
        out["mind"] = "up" if desk_up else "down"
        return out

    if book["kind"] == "coa":
        return _annotate_coa(out)

    if not desk_up:
        out["run_status"] = "mind-down"
        out["detail"] = "Strike Desk is down — positional books are unprotected"
        out["mind"] = "down"
        return out

    out["mind"] = "up"
    if book["kind"] == "strikeshield":
        phrase = _book_phrase(snapshot)
        if phrase == "flat":
            out["run_status"] = "active"
            out["detail"] = "mind up — book flat"
        else:
            out["run_status"] = "live-entered"
            out["detail"] = phrase
        return out

    if book["kind"] == "analyzer":
        latest = last_decision()
        if latest:
            stamp = latest.get("created_at_ist") or latest.get("created_at_utc") or ""
            out["run_status"] = "live-entered" if latest.get("outcome") == "enter" else "active"
            out["detail"] = f"{latest.get('outcome')}/{latest.get('reason_code')} · {stamp}"
        else:
            out["run_status"] = "active"
            out["detail"] = book.get("notes") or "15-min Analyzer book — waiting on the next tick"
        return out

    if book["kind"] == "ofa":
        ofa_enabled = bool(snapshot.get("ofa_enabled"))
        ofa_active = str(snapshot.get("ofa_active") or "") == "active"
        age = snapshot.get("ofa_heartbeat_age_s")
        try:
            age_s = float(age) if age is not None else None
        except (TypeError, ValueError):
            age_s = None
        signal = str(snapshot.get("ofa_signal") or "").upper()
        if signal not in {"LONG", "SHORT", "FLAT"}:
            signal = _ofa_signal_fallback()
        if not ofa_enabled:
            out["run_status"] = "parked"
            out["detail"] = "OFA disabled (STRIKE_DESK_OFA_ENABLED)"
            return out
        if not ofa_active or age_s is None or age_s > 90:
            out["run_status"] = "mind-down"
            out["detail"] = "OFA unit has no fresh heartbeat"
            return out
        if signal in {"LONG", "SHORT"}:
            out["run_status"] = "live-entered"
            out["detail"] = f"signal {signal}"
        else:
            out["run_status"] = "active"
            out["detail"] = "mind up — flat"
        return out

    out["run_status"] = "active"
    out["detail"] = book.get("notes") or "waiting on overlay"
    return out


def _annotate_coa(out: dict[str, Any]) -> dict[str, Any]:
    """CoA has its own systemd unit; do not inherit the main Strike Desk heartbeat."""
    age_s = coa_heartbeat_age_s()
    state = coa_live_state()
    side = _coa_open_side(state)
    if age_s is None or age_s > COA_HEARTBEAT_STALE_S:
        out["run_status"] = "mind-down"
        out["detail"] = "CoA unit has no fresh heartbeat"
        out["mind"] = "down"
        return out
    out["mind"] = "up"
    if side == "CE":
        out["run_status"] = "live-entered"
        out["detail"] = "holding ATM CE"
        return out
    if side == "PE":
        out["run_status"] = "live-entered"
        out["detail"] = "holding ATM PE"
        return out
    if state.get("last_exit"):
        out["run_status"] = "active"
        out["detail"] = "mind up — last trade exited, waiting to re-enter"
        return out
    out["run_status"] = "active"
    out["detail"] = "mind up — waiting (no EOS/EOR latch)"
    return out


def list_strategy_books() -> list[dict[str, Any]]:
    """Identity from the registry, run status from the desk snapshot. Never raises."""
    try:
        ensure_missing_books()
    except Exception:
        logger.exception("ensure_missing_books failed during list")
    snapshot = _read_desk_snapshot()
    rows = [_annotate(book, snapshot) for book in list_books()]
    try:
        from services.strategy_book_performance import attach_performance_summaries

        attach_performance_summaries(rows)
    except Exception:
        logger.exception("attach_performance_summaries failed during list")
    return rows
