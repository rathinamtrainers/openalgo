"""Public CoA briefs plus MCX quote tiles for the Lab page. Never Hands tokens."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)

BRIEF_DIR = Path(os.environ.get("GREEKS_LAB_BRIEF_DIR", "/var/lib/strike-desk"))
DESK_FALLBACK = os.environ.get("GREEKS_LAB_BRIEF_URL", "https://desk.zerotouchalgo.com")

BOARDS = (
    {
        "slug": "nifty",
        "file": "public-brief.json",
        "state": "coa_live.json",
        "title": "NIFTY",
        "exchange": "NFO",
        "underlying": "NIFTY",
        "source": "coa",
    },
    {
        "slug": "banknifty",
        "file": "public-brief-banknifty.json",
        "state": "coa_live_bn.json",
        "title": "BANKNIFTY",
        "exchange": "NFO",
        "underlying": "BANKNIFTY",
        "source": "coa",
    },
    {
        "slug": "sensex",
        "file": "public-brief-sensex.json",
        "state": "coa_live_sx.json",
        "title": "SENSEX",
        "exchange": "BFO",
        "underlying": "SENSEX",
        "source": "coa",
    },
    {
        "slug": "crudeoil",
        "file": "public-brief-crudeoil.json",
        "state": "coa_live_cl.json",
        "title": "CRUDEOIL",
        "exchange": "MCX",
        "underlying": "CRUDEOIL",
        "source": "coa",
    },
    {
        "slug": "natgas",
        "file": "public-brief-natgas.json",
        "state": "coa_live_ng.json",
        "title": "NATGAS",
        "exchange": "MCX",
        "underlying": "NATURALGAS",
        "source": "coa",
    },
    {
        "slug": "btc-options",
        "file": "public-brief-btc-options.json",
        "state": "coa_live_btc.json",
        "title": "BTC",
        "exchange": None,
        "underlying": None,
        "source": "coa",
    },
)

PUBLIC_KEYS = (
    "market",
    "index",
    "scenario_label",
    "as_of_ist",
    "computed_at",
    "spot",
    "last",
    "open",
    "high",
    "low",
    "change_vs_open",
    "do_now",
    "line",
    "stance",
    "support",
    "resistance",
    "eos",
    "eor",
    "mood",
    "disclaimer",
)

def _missing_row(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": meta["slug"],
        "title": meta["title"],
        "greeks_exchange": meta["exchange"],
        "greeks_underlying": meta["underlying"],
        "source": meta.get("source") or "coa",
        "scenario_label": None,
        "last": None,
        "missing": True,
    }


def _public_row(raw: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    out = {key: raw.get(key) for key in PUBLIC_KEYS}
    out["slug"] = meta["slug"]
    out["title"] = meta["title"]
    out["greeks_exchange"] = meta["exchange"]
    out["greeks_underlying"] = meta["underlying"]
    out["source"] = meta.get("source") or "coa"
    if not out.get("last") and out.get("spot") is not None:
        out["last"] = out["spot"]
    return out


def _read_file(name: str) -> dict[str, Any] | None:
    path = BRIEF_DIR / name
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("CoA brief unreadable JSON: %s", name)
        return None
    return data if isinstance(data, dict) else None


def _read_http(name: str) -> dict[str, Any] | None:
    try:
        from utils.httpx_client import get_httpx_client

        client = get_httpx_client()
        response = client.get(
            f"{DESK_FALLBACK}/{name}", timeout=5.0, headers={"Cache-Control": "no-store"}
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        logger.exception("CoA brief HTTP fallback failed: %s", name)
        return None
    return data if isinstance(data, dict) else None


def get_coa_boards(api_key: str | None = None) -> dict[str, Any]:
    del api_key
    boards: list[dict[str, Any]] = []
    for meta in BOARDS:
        filename = meta.get("file")
        raw = _read_file(filename) if filename else None
        if raw is None and filename:
            raw = _read_http(filename)
        if not raw:
            boards.append(_missing_row(meta))
            continue
        boards.append(_public_row(raw, meta))
    return {"status": "success", "boards": boards}


TRADE_KEYS = (
    "id",
    "board",
    "title",
    "side",
    "scenario",
    "in_at",
    "in_ist",
    "out_at",
    "out_ist",
    "day",
    "in_label",
    "out_label",
    "rule",
    "hold_s",
    "hold_label",
    "ist_span",
    "entry_ltp",
    "exit_ltp",
    "strike",
    "strike_label",
    "expiry",
    "symbol",
    "lot_size",
    "lots",
    "qty",
    "currency",
    "margin_used",
    "peak_ltp",
    "peak_pct",
    "mark_ltp",
    "atm_pnl",
    "mfe",
    "eos",
    "eor",
    "support",
    "resistance",
    "open",
    "exit_scenario",
)

TRADE_LOG = "coa-trades.jsonl"


def get_coa_trades(limit: int = 500) -> dict[str, Any]:
    """Hands-confirmed CoA fills. ATM-leg mark, not two-leg strategy P&L."""
    by_id: dict[str, dict[str, Any]] = {}
    path = BRIEF_DIR / TRADE_LOG
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("id") or "")
        if not key:
            continue
        by_id[key] = {field: raw.get(field) for field in TRADE_KEYS}
    rows = list(by_id.values())
    now = datetime.now(tz=timezone(timedelta(hours=5, minutes=30)))
    states = _open_states()
    for row in rows:
        _fill_margin(row)
        if not row.get("open") or not row.get("in_at"):
            continue
        try:
            started = datetime.fromisoformat(str(row["in_at"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=now.tzinfo)
        seconds = max(0, int((now - started).total_seconds()))
        row["hold_s"] = seconds
        row["hold_label"] = "<1 min" if seconds < 45 else f"~{max(1, round(seconds / 60))} min"
        in_ist = row.get("in_ist") or started.strftime("%H:%M")
        row["ist_span"] = f"{in_ist}–open"
        row["out_label"] = "open"
        _overlay_open_mark(row, states.get(str(row.get("board") or "")))
    rows.sort(key=lambda row: str(row.get("out_at") or row.get("in_at") or ""), reverse=True)
    cap = max(1, min(int(limit), 2000))
    return {"status": "success", "trades": rows[:cap], "count": min(len(rows), cap)}


def _fill_margin(row: dict[str, Any]) -> None:
    if row.get("margin_used") is not None:
        return
    try:
        entry = float(row.get("entry_ltp") or 0)
        qty = int(row.get("qty") or 0)
    except (TypeError, ValueError):
        return
    if entry > 0 and qty > 0:
        row["margin_used"] = round(entry * qty, 2)


def _open_states() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for meta in BOARDS:
        name = meta.get("state")
        slug = meta.get("slug")
        if not name or not slug:
            continue
        try:
            raw = json.loads((BRIEF_DIR / str(name)).read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict):
            out[str(slug)] = raw
    return out


def _overlay_open_mark(row: dict[str, Any], state: dict[str, Any] | None) -> None:
    if not state:
        return
    opened = state.get("open")
    if not isinstance(opened, dict):
        return
    entry = float(row.get("entry_ltp") or 0)
    try:
        qty = int(row.get("qty") or 0)
    except (TypeError, ValueError):
        qty = 0
    peak = opened.get("peak")
    last = opened.get("last_ltp")
    if peak is not None:
        try:
            peak_ltp = round(float(peak), 2)
        except (TypeError, ValueError):
            peak_ltp = None
        if peak_ltp is not None:
            row["peak_ltp"] = peak_ltp
            if entry > 0:
                row["peak_pct"] = round((peak_ltp / entry - 1.0) * 100.0, 1)
            if qty > 0:
                row["mfe"] = round((peak_ltp - entry) * qty, 2)
    if last is not None:
        try:
            mark = round(float(last), 2)
        except (TypeError, ValueError):
            mark = None
        if mark is not None:
            row["mark_ltp"] = mark
            if qty > 0:
                row["atm_pnl"] = round((mark - entry) * qty, 2)
