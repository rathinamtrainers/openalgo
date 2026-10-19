"""Per-book event timeline with a plain-language explanation on every row."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from database.strategy_books_db import append_event, get_book, latest_event, list_events
from services.desk_decisions_service import _as_ist, list_decisions
from services.stack_status_service import _read_desk_snapshot
from utils.logging import get_logger

logger = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")
MAX_LIMIT = 500
ANALYZER_STRATEGY = "strike-desk-NIFTY"

REASON_EXPLAIN: dict[str, str] = {
    "regime-weak-trend": (
        "The 15-minute playbook does not buy when the tape is a weak trend. "
        "The desk is up and Analyze is on; it chose to stand aside."
    ),
    "regime-not-tradeable": (
        "The regime read is not one this playbook trades, so the tick stood aside."
    ),
    "regime-low-confidence": (
        "The regime looked tradeable but the analyst's confidence was below the floor."
    ),
    "regime-ungrounded": (
        "The regime read was not grounded in live data, so the desk refused to enter."
    ),
    "risk-cleared": (
        "Risk checks passed. The desk sent a buy to OpenAlgo Analyze (sandbox fill)."
    ),
    "position-open": (
        "A NIFTY position is already open, so this tick manages the book instead of adding."
    ),
    "specialist-unavailable": (
        "No specialist was available to propose a contract. A regime read alone is never an entry."
    ),
    "specialist-timeout": "The specialist timed out before a decision could be assembled.",
    "data-quality": "Live book or regime data could not be read, so the desk declined.",
    "tick-timeout": "The tick ran past its budget before a decision was ready.",
    "internal-error": "The tick failed closed after an internal error.",
    "wings-open": "QuadShield opened the wing set. Fills stay on Tradetron Hands.",
    "shorts-open": "ADX and VIX allowed the short set after wings were already on.",
    "wings-shorts": (
        "QuadShield is already in the book (wings and shorts). "
        "The mind is supervising this positional book, not buying a new ATM PE."
    ),
    "shield-flat": "QuadShield is flat. No wings or shorts are latched.",
    "overlay-waiting": (
        "The EMA120 overlay is armed on Tradetron. It does not buy until the mind accepts a signal."
    ),
    "overlay-parked": "This BANKNIFTY overlay is parked. Do not redeploy the Tradetron row.",
}

FILL_EXPLAIN = {
    "placeorder": "OpenAlgo Analyze accepted a sandbox order from this book.",
    "closeposition": "OpenAlgo Analyze flattened the sandbox position for this book.",
    "placesmartorder": "OpenAlgo Analyze accepted a smart order from this book.",
}


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _trading_day(value: Any) -> str:
    stamp = _as_ist(value)
    return stamp[:10] if len(stamp) >= 10 else _now_utc().astimezone(IST).date().isoformat()


def _explain(reason_code: str | None, fallback: str) -> str:
    if reason_code and reason_code in REASON_EXPLAIN:
        return REASON_EXPLAIN[reason_code]
    return fallback


def _event(
    *,
    event_id: str,
    book_id: str,
    created_at_utc: Any,
    kind: str,
    title: str,
    explanation: str,
    outcome: str | None = None,
    reason_code: str | None = None,
    detail: str | None = None,
    trading_day: str | None = None,
) -> dict[str, Any]:
    created = str(created_at_utc or "")
    return {
        "event_id": event_id,
        "book_id": book_id,
        "created_at_utc": created,
        "created_at_ist": _as_ist(created_at_utc),
        "trading_day": trading_day or _trading_day(created_at_utc),
        "kind": kind,
        "title": title,
        "outcome": outcome,
        "reason_code": reason_code,
        "explanation": explanation,
        "detail": detail,
    }


def _analyzer_decision_events(limit: int) -> list[dict[str, Any]]:
    payload = list_decisions(limit=limit)
    events: list[dict[str, Any]] = []
    for row in payload.get("decisions") or []:
        outcome = str(row.get("outcome") or "decline")
        reason = str(row.get("reason_code") or "")
        title = {
            "enter": "Entered",
            "hold": "Held the open book",
            "decline": "Stood aside",
        }.get(outcome, outcome)
        positions = (row.get("book") or {}).get("positions") or []
        detail_parts = [row.get("reason_text") or ""]
        if row.get("regime_label"):
            detail_parts.append(f"regime {row['regime_label']}")
        if positions:
            detail_parts.append(", ".join(str(item) for item in positions))
        events.append(
            _event(
                event_id=f"tick:{row.get('tick_id')}",
                book_id="analyzer-nifty",
                created_at_utc=row.get("created_at_utc"),
                kind="decision",
                title=f"{title} · {reason}" if reason else title,
                explanation=_explain(reason, row.get("reason_text") or title),
                outcome=outcome,
                reason_code=reason,
                detail=" · ".join(part for part in detail_parts if part),
                trading_day=row.get("trading_day"),
            )
        )
    return events


def _analyzer_fill_events(limit: int) -> list[dict[str, Any]]:
    try:
        from database.analyzer_db import AnalyzerLog
    except Exception:
        return []
    try:
        rows = (
            AnalyzerLog.query.filter(AnalyzerLog.api_type.in_(("placeorder", "closeposition", "placesmartorder")))
            .order_by(AnalyzerLog.created_at.desc())
            .limit(max(limit * 4, 200))
            .all()
        )
    except Exception:
        logger.exception("analyzer fills unreadable")
        return []
    events: list[dict[str, Any]] = []
    for row in rows:
        data = row.to_dict()
        request = data.get("request_data") if isinstance(data.get("request_data"), dict) else {}
        strategy = str(request.get("strategy") or "")
        if strategy != ANALYZER_STRATEGY:
            continue
        api_type = str(data.get("api_type") or "")
        symbol = str(request.get("symbol") or "NIFTY")
        action = str(request.get("action") or "")
        response = data.get("response_data") if isinstance(data.get("response_data"), dict) else {}
        status = str(response.get("status") or "unknown")
        title = "Closed sandbox position" if api_type == "closeposition" else f"Sandbox {action or 'order'} {symbol}"
        events.append(
            _event(
                event_id=f"fill:{row.id}",
                book_id="analyzer-nifty",
                created_at_utc=data.get("created_at"),
                kind="fill",
                title=title,
                explanation=FILL_EXPLAIN.get(api_type, "Sandbox fill for this Analyzer book."),
                outcome="enter" if api_type != "closeposition" else "hold",
                reason_code=api_type,
                detail=f"{status} · {symbol} · {action}".strip(" ·"),
            )
        )
        if len(events) >= limit:
            break
    return events


def _shield_phrase(snapshot: dict[str, Any]) -> tuple[str, str]:
    set1 = bool(snapshot.get("set1"))
    set2 = bool(snapshot.get("set2open"))
    if set1 and set2:
        return "wings-shorts", "Holding wings + shorts"
    if set1:
        return "wings-open", "Holding wings"
    if set2:
        return "shorts-open", "Holding shorts"
    return "shield-flat", "Book flat"


def _shield_state_file() -> dict[str, Any]:
    raw = os.getenv("STRIKE_DESK_STATE_DIR")
    candidates = []
    if raw:
        candidates.append(Path(raw) / "strikeshield.json")
    candidates.append(Path("/var/lib/strike-desk/strikeshield.json"))
    for path in candidates:
        try:
            if path.is_file() and os.access(path, os.R_OK):
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def _observe_status_event(book: dict[str, Any], *, reason: str, title: str, explanation: str) -> None:
    now = _now_utc()
    day = now.astimezone(IST).date().isoformat()
    last = latest_event(book["id"])
    if last and last.get("reason_code") == reason and last.get("trading_day") == day:
        return
    append_event(
        {
            "event_id": f"{book['id']}:{reason}:{day}",
            "book_id": book["id"],
            "created_at_utc": now.isoformat(),
            "trading_day": day,
            "kind": "status",
            "title": title,
            "outcome": "hold" if reason != "overlay-parked" else "decline",
            "reason_code": reason,
            "explanation": explanation,
            "detail": book.get("notes"),
        }
    )


def _shield_events(book: dict[str, Any], snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    reason, title = _shield_phrase(snapshot)
    state = _shield_state_file()
    extra = []
    if state.get("eup") is not None:
        extra.append(f"eup {state['eup']}")
    if state.get("rollovers"):
        extra.append(f"rollovers {state['rollovers']}")
    explanation = _explain(
        reason,
        "StrikeShield is supervising the QuadShield book. Fills stay on Tradetron Hands.",
    )
    _observe_status_event(book, reason=reason, title=title, explanation=explanation)
    events = []
    for row in list_events(book["id"], limit=200):
        events.append(
            _event(
                event_id=row["event_id"],
                book_id=book["id"],
                created_at_utc=row["created_at_utc"],
                kind=row["kind"],
                title=row["title"],
                explanation=row["explanation"],
                outcome=row.get("outcome"),
                reason_code=row.get("reason_code"),
                detail=" · ".join(part for part in (row.get("detail"), *extra) if part),
                trading_day=row.get("trading_day"),
            )
        )
    return events


def _overlay_events(book: dict[str, Any]) -> list[dict[str, Any]]:
    if book.get("parked"):
        reason, title = "overlay-parked", "Parked"
    else:
        reason, title = "overlay-waiting", "Waiting on the mind"
    explanation = _explain(reason, book.get("notes") or title)
    _observe_status_event(book, reason=reason, title=title, explanation=explanation)
    events = []
    for row in list_events(book["id"], limit=200):
        events.append(
            _event(
                event_id=row["event_id"],
                book_id=book["id"],
                created_at_utc=row["created_at_utc"],
                kind=row["kind"],
                title=row["title"],
                explanation=row["explanation"],
                outcome=row.get("outcome"),
                reason_code=row.get("reason_code"),
                detail=row.get("detail"),
                trading_day=row.get("trading_day"),
            )
        )
    return events


def list_timeline(
    book_id: str,
    *,
    day: str | None = None,
    kind: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Return one book's timeline. Never raises."""
    try:
        capped = max(1, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        capped = 200
    day = day.strip() if isinstance(day, str) and day.strip() else None
    kind = kind.strip().lower() if isinstance(kind, str) and kind.strip() else None

    book = get_book(book_id)
    if book is None:
        return {"status": "error", "message": "unknown book", "book_id": book_id, "events": []}

    snapshot = _read_desk_snapshot()
    if book["kind"] == "analyzer":
        events = _analyzer_decision_events(capped) + _analyzer_fill_events(capped)
    elif book["kind"] == "strikeshield":
        events = _shield_events(book, snapshot)
    else:
        events = _overlay_events(book)

    if day:
        events = [row for row in events if row.get("trading_day") == day]
    if kind:
        events = [row for row in events if row.get("kind") == kind]
    events.sort(key=lambda row: str(row.get("created_at_utc") or ""), reverse=True)
    events = events[:capped]

    counts: dict[str, int] = {}
    days: list[str] = []
    seen_days: set[str] = set()
    for row in events:
        counts[str(row.get("kind") or "event")] = counts.get(str(row.get("kind") or "event"), 0) + 1
        trading_day = str(row.get("trading_day") or "")
        if trading_day and trading_day not in seen_days:
            seen_days.add(trading_day)
            days.append(trading_day)

    latest = events[0] if events else None
    return {
        "book_id": book["id"],
        "name": book["name"],
        "kind": book["kind"],
        "kind_label": book["kind"],
        "underlying": book["underlying"],
        "notes": book.get("notes"),
        "readable": bool(events) or book["kind"] != "analyzer",
        "days": days,
        "counts": counts,
        "latest": latest,
        "events": events,
    }
