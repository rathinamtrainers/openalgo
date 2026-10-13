"""Read the local Delta India BTC option desk for the /btc monitor.

The desk process writes outputs/otm_desk_status.json and data/delta.sqlite.
Set BTC_DESK_ROOT if that tree is not the default path on this machine.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

DEFAULT_ROOT = Path(r"C:\Users\Admin\Downloads\TTCCLAUDELOCAL")
STALE_AFTER_SECONDS = 15


def desk_root() -> Path:
    raw = os.getenv("BTC_DESK_ROOT", "").strip()
    return Path(raw) if raw else DEFAULT_ROOT


def _read_status(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_arrests(db_path: Path, limit: int) -> tuple[list[dict], str | None]:
    if not db_path.exists():
        return [], None
    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(
            """
            SELECT detected_at, kind, expiry, near_symbol, far_symbol,
                   near_ask, far_bid, diff, spot, mode, qty
            FROM anomalies
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
    except sqlite3.Error as exc:
        return [], str(exc)
    arrests = [
        {
            "detected_at": row["detected_at"],
            "kind": row["kind"],
            "expiry": row["expiry"],
            "near": row["near_symbol"],
            "far": row["far_symbol"],
            "near_ask": row["near_ask"],
            "far_bid": row["far_bid"],
            "diff": row["diff"],
            "spot": row["spot"],
            "mode": row["mode"],
            "qty": row["qty"],
        }
        for row in rows
    ]
    return arrests, None


def read_desk(limit: int = 40) -> dict:
    root = desk_root()
    status_path = root / "outputs" / "otm_desk_status.json"
    db_path = root / "data" / "delta.sqlite"
    status = _read_status(status_path)
    age = None
    if status_path.exists():
        age = max(0.0, time.time() - status_path.stat().st_mtime)
    arrests, db_error = _read_arrests(db_path, limit)
    alive = age is not None and age < STALE_AFTER_SECONDS and bool(status)
    return {
        "status": "success",
        "data": {
            "alive": alive,
            "age_seconds": None if age is None else round(age, 1),
            "desk": status,
            "arrests": arrests,
            "db_error": db_error,
            "positions_href": "/positions",
            "orderbook_href": "/orderbook",
        },
    }
