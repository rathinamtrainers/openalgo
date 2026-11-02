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
from services.strategy_book_performance import book_performance
from services.strategy_books_service import (
    COA_HEARTBEAT_STALE_S,
    coa_heartbeat_age_s,
    coa_live_state,
    is_analyzer_order_strategy,
    is_coa_order_strategy,
    is_ofa_order_strategy,
)
from utils.logging import get_logger

logger = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")
MAX_LIMIT = 500
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
    "risk-cleared": ("Risk checks passed. The desk sent a buy to OpenAlgo Analyze (sandbox fill)."),
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
    "ofa-long": (
        "OFA read aggressive buyers on Nifty fut depth. "
        "Hands posted of_long to Tradetron; Analyze bought ATM CE."
    ),
    "ofa-short": (
        "OFA read aggressive sellers on Nifty fut depth. "
        "Hands posted of_short to Tradetron; Analyze bought ATM PE."
    ),
    "ofa-flat": "OFA is flat. No CE/PE book open in Analyze or on Tradetron Hands.",
    "ofa-exit": (
        "OFA signal flipped to flat. Analyze closed the option book; "
        "Hands posted of_exit to Tradetron."
    ),
    "ofa-hold": "OFA signal unchanged this step — no new Analyze fill or Hands post.",
    "coa-buy-ce": (
        "CoA latched EOS and bought ATM CE. Analyze filled the sandbox (tag COA); "
        "Hands posted ltp_buy_ce. Live Offline — this book does not go Live Auto."
    ),
    "coa-buy-pe": (
        "CoA latched EOR and bought ATM PE. Analyze filled the sandbox (tag COA); "
        "Hands posted ltp_buy_pe. Live Offline — this book does not go Live Auto."
    ),
    "coa-hold": (
        "CoA is watching the chain every 5 minutes. No EOS/EOR latch, "
        "so Analyze stays idle and Hands ltp flags stay at 0."
    ),
    "coa-exit": (
        "CoA flattened the ATM option (stop, target, or 15:15). "
        "Analyze sold the sandbox fill; Hands posted ltp_exit. Waiting for the re-entry cooldown."
    ),
    "coa-down": (
        "The CoA mind (strike-desk-coa.service) has no fresh heartbeat. "
        "Analyze cannot receive COA fills and Hands cannot receive ltp_buy_* until the unit is up."
    ),
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


def _is_key_decision(row: dict[str, Any]) -> bool:
    """Rows that belong on Key decisions for every book: enter/hold/flip plus enter fills."""
    kind = str(row.get("kind") or "")
    if kind in {"decision", "status", "action"}:
        return True
    return kind == "fill" and str(row.get("outcome") or "") == "enter"


def _key_decision_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[Any] = set()
    rows: list[dict[str, Any]] = []
    for row in events:
        event_id = row.get("event_id")
        if not _is_key_decision(row) or event_id in seen:
            continue
        seen.add(event_id)
        rows.append(row)
    rows.sort(key=lambda row: str(row.get("created_at_utc") or ""), reverse=True)
    return rows


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
            AnalyzerLog.query.filter(
                AnalyzerLog.api_type.in_(("placeorder", "closeposition", "placesmartorder"))
            )
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
        if not is_analyzer_order_strategy(strategy):
            continue
        api_type = str(data.get("api_type") or "")
        symbol = str(request.get("symbol") or "NIFTY")
        action = str(request.get("action") or "")
        response = data.get("response_data") if isinstance(data.get("response_data"), dict) else {}
        status = str(response.get("status") or "unknown")
        title = (
            "Closed sandbox position"
            if api_type == "closeposition"
            else f"Sandbox {action or 'order'} {symbol}"
        )
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
    candidates.append(Path("/var/tmp/strike-desk-strikeshield.json"))
    candidates.append(Path("/var/lib/strike-desk/strikeshield.json"))
    for path in candidates:
        try:
            if path.is_file() and os.access(path, os.R_OK):
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            continue
    return {}


_STATUS_OUTCOME = {
    "overlay-parked": "decline",
    "coa-buy-ce": "enter",
    "coa-buy-pe": "enter",
}


def _observe_status_event(
    book: dict[str, Any], *, reason: str, title: str, explanation: str
) -> None:
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
            "outcome": _STATUS_OUTCOME.get(reason, "hold"),
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


def _ofa_state_paths() -> list[Path]:
    raw = os.getenv("STRIKE_DESK_STATE_DIR")
    candidates: list[Path] = []
    if raw:
        base = Path(raw)
        candidates.extend([base / "ofa_journal.jsonl", base / "ofa_exec.json", base / "ofa.json"])
    candidates.extend(
        [
            Path("/var/tmp/strike-desk-ofa-journal.jsonl"),
            Path("/var/lib/strike-desk/ofa_journal.jsonl"),
            Path("/var/lib/strike-desk/ofa_exec.json"),
            Path("/var/lib/strike-desk/ofa.json"),
        ]
    )
    return candidates


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        if path.is_file() and os.access(path, os.R_OK):
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def _ofa_journal_events(limit: int) -> list[dict[str, Any]]:
    journal_path = None
    for path in _ofa_state_paths():
        if path.suffix != ".jsonl" or "ofa" not in path.name:
            continue
        try:
            if path.is_file() and os.access(path, os.R_OK):
                journal_path = path
                break
        except OSError:
            continue
    if journal_path is None:
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = journal_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason_code") or row.get("signal") or "ofa-hold")
        events.append(
            _event(
                event_id=str(row.get("event_id") or f"ofa:{row.get('ts')}:{reason}"),
                book_id="ofa-nifty",
                created_at_utc=row.get("ts") or row.get("created_at_utc"),
                kind=str(row.get("kind") or "decision"),
                title=str(row.get("title") or reason),
                explanation=_explain(
                    reason, str(row.get("explanation") or row.get("detail") or reason)
                ),
                outcome=str(row.get("outcome") or "hold"),
                reason_code=reason,
                detail=str(row.get("detail") or ""),
                trading_day=row.get("trading_day"),
            )
        )
        if len(events) >= limit:
            break
    return events


def _ofa_fill_events(limit: int) -> list[dict[str, Any]]:
    try:
        from database.analyzer_db import AnalyzerLog
    except Exception:
        return []
    try:
        rows = (
            AnalyzerLog.query.filter(
                AnalyzerLog.api_type.in_(("placeorder", "closeposition", "placesmartorder"))
            )
            .order_by(AnalyzerLog.created_at.desc())
            .limit(max(limit * 4, 200))
            .all()
        )
    except Exception:
        logger.exception("OFA analyzer fills unreadable")
        return []
    events: list[dict[str, Any]] = []
    for row in rows:
        data = row.to_dict()
        request = data.get("request_data") if isinstance(data.get("request_data"), dict) else {}
        strategy = str(request.get("strategy") or "")
        if not is_ofa_order_strategy(strategy):
            continue
        api_type = str(data.get("api_type") or "")
        symbol = str(request.get("symbol") or "NIFTY")
        action = str(request.get("action") or "")
        response = data.get("response_data") if isinstance(data.get("response_data"), dict) else {}
        status = str(response.get("status") or "unknown")
        title = (
            "Closed OFA sandbox position"
            if api_type == "closeposition"
            else f"OFA sandbox {action or 'order'} {symbol}"
        )
        events.append(
            _event(
                event_id=f"ofa-fill:{row.id}",
                book_id="ofa-nifty",
                created_at_utc=data.get("created_at"),
                kind="fill",
                title=title,
                explanation=FILL_EXPLAIN.get(api_type, "Sandbox fill for the OFA book."),
                outcome="enter" if api_type != "closeposition" else "hold",
                reason_code=api_type,
                detail=f"{status} · {symbol} · {action}".strip(" ·"),
            )
        )
        if len(events) >= limit:
            break
    return events


def _ofa_events(book: dict[str, Any], snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    signal = str(snapshot.get("ofa_signal") or "FLAT").upper()
    if signal == "LONG":
        reason, title = "ofa-long", "Holding CE (LONG)"
    elif signal == "SHORT":
        reason, title = "ofa-short", "Holding PE (SHORT)"
    else:
        reason, title = "ofa-flat", "Book flat"
    explanation = _explain(reason, book.get("notes") or title)
    _observe_status_event(book, reason=reason, title=title, explanation=explanation)
    events = _ofa_journal_events(200) + _ofa_fill_events(200)
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


def _coa_fill_events(limit: int) -> list[dict[str, Any]]:
    try:
        from database.analyzer_db import AnalyzerLog
    except Exception:
        return []
    try:
        rows = (
            AnalyzerLog.query.filter(
                AnalyzerLog.api_type.in_(("placeorder", "closeposition", "placesmartorder"))
            )
            .order_by(AnalyzerLog.created_at.desc())
            .limit(max(limit * 4, 200))
            .all()
        )
    except Exception:
        logger.exception("CoA analyzer fills unreadable")
        return []
    events: list[dict[str, Any]] = []
    for row in rows:
        data = row.to_dict()
        request = data.get("request_data") if isinstance(data.get("request_data"), dict) else {}
        strategy = str(request.get("strategy") or "")
        if not is_coa_order_strategy(strategy):
            continue
        api_type = str(data.get("api_type") or "")
        symbol = str(request.get("symbol") or "NIFTY")
        action = str(request.get("action") or "")
        response = data.get("response_data") if isinstance(data.get("response_data"), dict) else {}
        status = str(response.get("status") or "unknown")
        title = (
            "Closed CoA sandbox position"
            if api_type == "closeposition"
            else f"CoA sandbox {action or 'order'} {symbol}"
        )
        events.append(
            _event(
                event_id=f"coa-fill:{row.id}",
                book_id="coa-nifty",
                created_at_utc=data.get("created_at"),
                kind="fill",
                title=title,
                explanation=FILL_EXPLAIN.get(api_type, "Sandbox fill for the CoA book."),
                outcome="enter" if api_type != "closeposition" else "hold",
                reason_code=api_type,
                detail=f"{status} · {symbol} · {action}".strip(" ·"),
            )
        )
        if len(events) >= limit:
            break
    return events


def _coa_events(book: dict[str, Any]) -> list[dict[str, Any]]:
    age_s = coa_heartbeat_age_s()
    state = coa_live_state()
    open_trade = state.get("open") if isinstance(state.get("open"), dict) else None
    side = str((open_trade or {}).get("side") or "").upper()
    if age_s is None or age_s > COA_HEARTBEAT_STALE_S:
        reason, title = "coa-down", "CoA mind down"
    elif side == "CE":
        reason, title = "coa-buy-ce", "Holding ATM CE"
    elif side == "PE":
        reason, title = "coa-buy-pe", "Holding ATM PE"
    elif state.get("last_exit"):
        reason, title = "coa-exit", "Last trade exited — waiting to re-enter"
    else:
        reason, title = "coa-hold", "Waiting — no CoA latch"
    explanation = _explain(reason, book.get("notes") or title)
    _observe_status_event(book, reason=reason, title=title, explanation=explanation)
    events = _coa_fill_events(200)
    extra = []
    if open_trade:
        if open_trade.get("strike") is not None:
            extra.append(f"strike {open_trade.get('strike')}")
        if open_trade.get("scenario"):
            extra.append(str(open_trade.get("scenario")))
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
    elif book["kind"] == "ofa":
        events = _ofa_events(book, snapshot)
    elif book["kind"] == "strikeshield":
        events = _shield_events(book, snapshot)
    elif book["kind"] == "coa":
        events = _coa_events(book)
    else:
        events = _overlay_events(book)

    if day:
        events = [row for row in events if row.get("trading_day") == day]
    events.sort(key=lambda row: str(row.get("created_at_utc") or ""), reverse=True)

    fills = [row for row in events if row.get("kind") == "fill"]
    status_rows = [row for row in events if row.get("kind") in {"status", "action"}]
    key_decisions = _key_decision_rows(events)

    shown = events if not kind else [row for row in events if row.get("kind") == kind]
    shown = shown[:capped]
    latest = events[0] if events else None
    performance = book_performance(book, events, day=day)

    counts: dict[str, int] = {}
    days: list[str] = []
    seen_days: set[str] = set()
    for row in shown:
        counts[str(row.get("kind") or "event")] = counts.get(str(row.get("kind") or "event"), 0) + 1
    for row in events:
        trading_day = str(row.get("trading_day") or "")
        if trading_day and trading_day not in seen_days:
            seen_days.add(trading_day)
            days.append(trading_day)

    return {
        "book_id": book["id"],
        "name": book["name"],
        "kind": book["kind"],
        "kind_label": book["kind"],
        "underlying": book["underlying"],
        "execution_target": book.get("execution_target"),
        "tt_sid": book.get("tt_sid"),
        "tt_template_id": book.get("tt_template_id"),
        "audit_href": f"/strategy/books/{book['id']}",
        "notes": book.get("notes"),
        "readable": bool(events) or book["kind"] != "analyzer",
        "days": days,
        "counts": counts,
        "latest": latest,
        "sections": {
            "key_decisions": key_decisions[:50],
            "event_log": shown[:100],
            "audit_trail": status_rows[:50] + fills[:50],
        },
        "performance": performance,
        "events": shown,
    }


def _status_plain(book: dict[str, Any], latest: dict[str, Any] | None) -> str:
    if book.get("parked"):
        return book.get("notes") or "This book is parked. It is not allowed to trade."
    if book.get("run_status") == "mind-down":
        return book.get("detail") or "The mind for this book is down."
    if latest and latest.get("explanation"):
        return str(latest["explanation"])
    return book.get("detail") or "Waiting on the next tick."


def list_bank_board(*, snippet: int = 5) -> list[dict[str, Any]]:
    """Every seeded book with current status plus the latest log snippets."""
    from services.strategy_books_service import list_strategy_books

    try:
        capped = max(1, min(int(snippet), 20))
    except (TypeError, ValueError):
        capped = 5
    board: list[dict[str, Any]] = []
    for book in list_strategy_books():
        timeline = list_timeline(book["id"], limit=40)
        sections = timeline.get("sections") or {}
        latest = timeline.get("latest")
        board.append(
            {
                **book,
                "status_plain": _status_plain(book, latest if isinstance(latest, dict) else None),
                "latest": latest,
                "sections": {
                    "key_decisions": (sections.get("key_decisions") or [])[:capped],
                    "event_log": (sections.get("event_log") or [])[:capped],
                    "audit_trail": (sections.get("audit_trail") or [])[:capped],
                },
            }
        )
    return board
