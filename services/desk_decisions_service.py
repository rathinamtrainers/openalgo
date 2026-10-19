"""Read-only view of the Strike Desk 15-minute decision journal.

The journal is append-only SQLite written by Strike Desk (`strike_desk.db`).
This process never writes it. A world-readable JSONL export is a fallback when
the journal directory is not readable by the OpenAlgo user.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from utils.logging import get_logger

logger = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_JOURNAL = Path("/var/lib/strike-desk/strike_desk.db")
DEFAULT_EXPORT = Path("/var/tmp/strike-desk-decisions.jsonl")
MAX_LIMIT = 1000


def _journal_candidates() -> list[Path]:
    raw = os.getenv("STRIKE_DESK_JOURNAL")
    state = os.getenv("STRIKE_DESK_STATE_DIR")
    paths: list[Path] = []
    if raw:
        paths.append(Path(raw))
    if state:
        paths.append(Path(state) / "strike_desk.db")
    paths.append(DEFAULT_JOURNAL)
    paths.append(Path("/var/tmp/strike-desk.db"))
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path if path.is_absolute() else path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def _export_path() -> Path:
    raw = os.getenv("STRIKE_DESK_DECISIONS_EXPORT")
    return Path(raw) if raw else DEFAULT_EXPORT


def resolve_journal_path() -> Path | None:
    """First existing, readable journal file, or None."""
    for path in _journal_candidates():
        try:
            if path.is_file() and os.access(path, os.R_OK):
                return path
        except OSError:
            continue
    return None


def _as_ist(value: Any) -> str:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    else:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")


def _book_summary(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        book = raw
    elif isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        book = parsed if isinstance(parsed, dict) else {}
    else:
        return {}
    positions = book.get("open_positions") or []
    symbols = []
    if isinstance(positions, list):
        for row in positions:
            if isinstance(row, dict) and row.get("symbol"):
                symbols.append(str(row["symbol"]))
    return {
        "flat": book.get("flat"),
        "decisions_today": book.get("decisions_today"),
        "positions": symbols,
    }


def _serialize_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    created = data.get("created_at_utc")
    return {
        "tick_id": data.get("tick_id"),
        "created_at_utc": str(created or ""),
        "created_at_ist": _as_ist(created),
        "trading_day": data.get("trading_day"),
        "index_symbol": data.get("index_symbol"),
        "trigger": data.get("trigger"),
        "outcome": data.get("outcome"),
        "reason_code": data.get("reason_code"),
        "reason_text": data.get("reason_text"),
        "regime_label": data.get("regime_label"),
        "regime_confidence": data.get("regime_confidence"),
        "latency_ms": data.get("latency_ms"),
        "trace_complete": bool(data.get("trace_complete", True)),
        "book": _book_summary(data.get("book_state_json")),
    }


def _connect_readonly(path: Path) -> sqlite3.Connection:
    """Open the journal without writing. WAL needs immutable=1 when the dir is 0750."""
    uri = path.resolve().as_uri()
    last_error: sqlite3.Error | None = None
    for suffix in ("?mode=ro", "?mode=ro&immutable=1"):
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(f"{uri}{suffix}", uri=True, timeout=5)
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
            return conn
        except sqlite3.Error as exc:
            last_error = exc
            if conn is not None:
                conn.close()
    raise last_error or sqlite3.OperationalError(f"cannot open {path}")


def _read_sqlite(
    path: Path,
    *,
    day: str | None,
    outcome: str | None,
    limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    clauses = []
    params: list[Any] = []
    if day:
        clauses.append("trading_day = ?")
        params.append(day)
    if outcome:
        clauses.append("outcome = ?")
        params.append(outcome)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    days: list[str] = []
    rows: list[dict[str, Any]] = []
    conn = _connect_readonly(path)
    try:
        conn.row_factory = sqlite3.Row
        days = [
            str(item[0])
            for item in conn.execute(
                "SELECT DISTINCT trading_day FROM decisions ORDER BY trading_day DESC LIMIT 60"
            )
        ]
        query = (
            f"SELECT tick_id, created_at_utc, trading_day, index_symbol, trigger, "
            f"outcome, reason_code, reason_text, regime_label, regime_confidence, "
            f"latency_ms, trace_complete, book_state_json "
            f"FROM decisions {where} ORDER BY created_at_utc DESC LIMIT ?"
        )
        params.append(limit)
        rows = [_serialize_row(item) for item in conn.execute(query, params)]
    finally:
        conn.close()
    return rows, days


def _read_jsonl(
    path: Path,
    *,
    day: str | None,
    outcome: str | None,
    limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.is_file() or not os.access(path, os.R_OK):
        return [], []
    parsed: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    parsed.append(_serialize_row(item))
    except OSError:
        logger.exception("decision export unreadable")
        return [], []
    days = sorted({str(row.get("trading_day")) for row in parsed if row.get("trading_day")}, reverse=True)
    if day:
        parsed = [row for row in parsed if row.get("trading_day") == day]
    if outcome:
        parsed = [row for row in parsed if row.get("outcome") == outcome]
    parsed.sort(key=lambda row: str(row.get("created_at_utc") or ""), reverse=True)
    return parsed[:limit], days[:60]


def last_decision() -> dict[str, Any] | None:
    payload = list_decisions(limit=1)
    rows = payload.get("decisions") or []
    return rows[0] if rows else None


def list_decisions(
    *,
    day: str | None = None,
    outcome: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Return the audit trail. Never raises."""
    try:
        capped = max(1, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        capped = 200
    day = day.strip() if isinstance(day, str) and day.strip() else None
    outcome = outcome.strip().lower() if isinstance(outcome, str) and outcome.strip() else None
    if outcome and outcome not in {"enter", "hold", "decline"}:
        outcome = None

    journal = resolve_journal_path()
    export = _export_path()
    source = "missing"
    source_path = str(journal or export)
    rows: list[dict[str, Any]] = []
    days: list[str] = []

    if journal is not None:
        try:
            rows, days = _read_sqlite(journal, day=day, outcome=outcome, limit=capped)
            source = "journal"
            source_path = str(journal)
        except sqlite3.Error:
            logger.exception("strike desk journal unreadable")
            journal = None

    if not rows:
        export_rows, export_days = _read_jsonl(export, day=day, outcome=outcome, limit=capped)
        if export_rows:
            rows = export_rows
            days = export_days or days
            source = "export"
            source_path = str(export)

    counts: dict[str, int] = {"enter": 0, "hold": 0, "decline": 0}
    for row in rows:
        key = str(row.get("outcome") or "")
        if key in counts:
            counts[key] += 1

    return {
        "book_id": "analyzer-nifty",
        "name": "Strike Desk NIFTY Analyzer",
        "cadence_seconds": 900,
        "source": source,
        "source_path": source_path,
        "readable": source != "missing",
        "days": days,
        "counts": counts,
        "decisions": rows,
    }
