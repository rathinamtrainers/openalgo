"""Read-only My Strategies list: registry identity + mind snapshot status."""

from __future__ import annotations

from typing import Any

from database.strategy_books_db import list_books
from services.desk_decisions_service import last_decision
from services.stack_status_service import _read_desk_snapshot
from utils.logging import get_logger

logger = get_logger(__name__)

KIND_LABELS = {
    "analyzer": "Analyzer",
    "strikeshield": "StrikeShield",
    "overlay": "Overlay",
    "webhook": "Webhook",
    "python": "Python",
    "flow": "Flow",
}

TARGET_LABELS = {
    "tradetron-hands": "Tradetron Hands",
    "openalgo": "OpenAlgo Analyze",
    "none": "None",
}


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

    out["run_status"] = "active"
    out["detail"] = book.get("notes") or "waiting on overlay"
    return out


def list_strategy_books() -> list[dict[str, Any]]:
    """Identity from the registry, run status from the desk snapshot. Never raises."""
    snapshot = _read_desk_snapshot()
    return [_annotate(book, snapshot) for book in list_books()]
