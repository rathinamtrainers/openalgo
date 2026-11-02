"""Append-only CoA Hands blotter. Lab reads this; Tradetron is not the source.

A row is written only after the Hands webhook is accepted. ATM-leg P&L is
(exit − entry) × qty for the ATM print the mind tracked — not the two-leg
Hands template and not strategy P&L. Never tokens, SIDs, or broker fields.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .coa_boards import CoaBoard
from .config import IST
from .ltp_coa_exits import OpenTrade

logger = logging.getLogger(__name__)

LOG_NAME = "coa-trades.jsonl"
SNAPSHOT_NAME = "coa-trades.json"
MAX_ROWS = 2000

_TITLES = {
    "NIFTY": "Nifty",
    "BANKNIFTY": "Banknifty",
    "SENSEX": "Sensex",
    "CRUDEOIL": "Crude",
    "NATGAS": "Natgas",
    "BTC": "BTC",
}


def display_title(index_name: str) -> str:
    return _TITLES.get(index_name, index_name)


def trade_id(board: str, entry_ts: datetime) -> str:
    return f"{board}:{entry_ts.astimezone(IST).isoformat()}"


def hold_seconds(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds()))


def hold_label(seconds: int) -> str:
    if seconds < 45:
        return "<1 min"
    return f"~{max(1, round(seconds / 60))} min"


def clock_ist(ts: datetime) -> str:
    return ts.astimezone(IST).strftime("%H:%M")


def day_ist(ts: datetime) -> str:
    return ts.astimezone(IST).strftime("%d %b %Y")


def in_label(side: str, scenario: str | None) -> str:
    tag = (scenario or "?").replace("CoA ", "")
    return f"{side} on CoA {tag}"


def out_label(reason: str, exit_scenario: str | None) -> str:
    text = (reason or "").strip()
    if text.startswith("scenario kill"):
        tag = (exit_scenario or "").replace("CoA ", "") or "?"
        return f"scenario kill {tag}"
    return text or "—"


def currency_for(board: CoaBoard) -> str:
    return "USD" if getattr(board, "session", "cash") == "crypto" else "INR"


def strike_label(side: str, strike: float | None, expiry: str | None = None) -> str:
    tag = (side or "").upper()
    if strike is None or float(strike) == 0:
        text = tag or "—"
    else:
        value = float(strike)
        shown = int(value) if value.is_integer() else value
        text = f"{shown} {tag}".strip()
    if expiry:
        return f"{text} · {expiry}"
    return text


def peak_pct(entry: float, peak: float | None) -> float | None:
    if not entry or peak is None:
        return None
    return round((float(peak) / float(entry) - 1.0) * 100.0, 1)


def mark_money(entry: float, last: float | None, qty: int | None) -> float | None:
    if last is None or qty is None:
        return None
    try:
        units = int(qty)
    except (TypeError, ValueError):
        return None
    if units <= 0:
        return None
    return round((float(last) - float(entry)) * units, 2)


def record_entry(
    state_dir: Path,
    board: CoaBoard,
    trade: OpenTrade,
    *,
    symbol: str | None = None,
    lot_size: int | None = None,
    lots: int | None = None,
    qty: int | None = None,
    expiry: str | None = None,
    currency: str | None = None,
) -> None:
    row = _base_row(
        board,
        trade,
        symbol=symbol,
        lot_size=lot_size,
        lots=lots,
        qty=qty,
        expiry=expiry,
        currency=currency,
    )
    row["open"] = True
    row["out_at"] = None
    row["out_ist"] = None
    row["exit_ltp"] = None
    row["exit_scenario"] = None
    row["rule"] = None
    row["out_label"] = "open"
    row["hold_s"] = None
    row["hold_label"] = "open"
    row["atm_pnl"] = None
    row["mfe"] = None
    row["mark_ltp"] = row["entry_ltp"]
    _append(state_dir, row)


def record_exit(
    state_dir: Path,
    board: CoaBoard,
    trade: OpenTrade,
    *,
    now: datetime,
    exit_ltp: float,
    reason: str,
    exit_scenario: str | None,
    symbol: str | None = None,
    lot_size: int | None = None,
    lots: int | None = None,
    qty: int | None = None,
    expiry: str | None = None,
    currency: str | None = None,
) -> None:
    ended = now.astimezone(IST)
    started = trade.entry_ts.astimezone(IST)
    seconds = hold_seconds(started, ended)
    row = _base_row(
        board,
        trade,
        symbol=symbol,
        lot_size=lot_size,
        lots=lots,
        qty=qty,
        expiry=expiry,
        currency=currency,
    )
    row["open"] = False
    row["out_at"] = ended.isoformat()
    row["out_ist"] = clock_ist(ended)
    row["exit_ltp"] = round(float(exit_ltp), 2)
    row["exit_scenario"] = exit_scenario
    row["rule"] = reason
    row["out_label"] = out_label(reason, exit_scenario)
    row["hold_s"] = seconds
    row["hold_label"] = hold_label(seconds)
    row["ist_span"] = f"{clock_ist(started)}–{clock_ist(ended)}"
    row["mark_ltp"] = round(float(exit_ltp), 2)
    row["atm_pnl"] = mark_money(float(trade.entry or 0), exit_ltp, row.get("qty"))
    row["mfe"] = mark_money(float(trade.entry or 0), row.get("peak_ltp"), row.get("qty"))
    _append(state_dir, row)


def record_mark(
    state_dir: Path,
    board: CoaBoard,
    trade: OpenTrade,
    *,
    ltp: float,
    symbol: str | None = None,
    lot_size: int | None = None,
    lots: int | None = None,
    qty: int | None = None,
    expiry: str | None = None,
    currency: str | None = None,
) -> None:
    """Ratchet the open-row high watermark. Last-id-wins; trade stays open."""
    row = _base_row(
        board,
        trade,
        symbol=symbol,
        lot_size=lot_size,
        lots=lots,
        qty=qty,
        expiry=expiry,
        currency=currency,
    )
    row["open"] = True
    row["out_at"] = None
    row["out_ist"] = None
    row["exit_ltp"] = None
    row["exit_scenario"] = None
    row["rule"] = None
    row["out_label"] = "open"
    row["hold_s"] = None
    row["hold_label"] = "open"
    row["mark_ltp"] = round(float(ltp), 2)
    row["atm_pnl"] = mark_money(float(trade.entry or 0), ltp, row.get("qty"))
    row["mfe"] = mark_money(float(trade.entry or 0), row.get("peak_ltp"), row.get("qty"))
    _append(state_dir, row)


def load_trades(
    state_dir: Path,
    *,
    now: datetime | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Newest first. Last line for an id wins. Open rows get a live hold clock."""
    stamp = (now or datetime.now(tz=IST)).astimezone(IST)
    by_id: dict[str, dict[str, Any]] = {}
    for raw in _iter_jsonl(state_dir / LOG_NAME):
        key = str(raw.get("id") or "")
        if not key:
            continue
        by_id[key] = raw
    rows = list(by_id.values())
    for row in rows:
        if row.get("open") and row.get("in_at"):
            started = _parse_ts(row.get("in_at"))
            if started is not None:
                seconds = hold_seconds(started, stamp)
                row["hold_s"] = seconds
                row["hold_label"] = hold_label(seconds)
                row["out_label"] = "open"
                row["ist_span"] = f"{row.get('in_ist') or clock_ist(started)}–open"
    rows.sort(key=lambda row: str(row.get("out_at") or row.get("in_at") or ""), reverse=True)
    return rows[: max(1, min(int(limit), MAX_ROWS))]


def _base_row(
    board: CoaBoard,
    trade: OpenTrade,
    *,
    symbol: str | None = None,
    lot_size: int | None = None,
    lots: int | None = None,
    qty: int | None = None,
    expiry: str | None = None,
    currency: str | None = None,
) -> dict[str, Any]:
    started = trade.entry_ts.astimezone(IST)
    entry = round(float(trade.entry or 0), 2)
    strike = float(trade.strike) if trade.strike else None
    peak = round(float(trade.peak or 0), 2) or None
    lots_n = int(lots) if lots else 1
    lot_n = int(lot_size) if lot_size else None
    qty_n = int(qty) if qty else None
    if qty_n is None and lot_n:
        order_quantity = getattr(board, "order_quantity", None)
        if callable(order_quantity):
            qty_n = int(order_quantity(lot_n, lots_n))
        else:
            qty_n = lot_n * lots_n
    return {
        "id": trade_id(board.slug, started),
        "board": board.slug,
        "title": display_title(board.index_name),
        "side": trade.side,
        "scenario": trade.scenario,
        "in_at": started.isoformat(),
        "in_ist": clock_ist(started),
        "day": day_ist(started),
        "in_label": in_label(trade.side, trade.scenario),
        "entry_ltp": entry,
        "strike": strike,
        "strike_label": strike_label(trade.side, strike, expiry),
        "expiry": expiry,
        "symbol": (symbol or "").strip() or None,
        "lot_size": lot_n,
        "lots": lots_n,
        "qty": qty_n,
        "currency": currency or currency_for(board),
        "margin_used": mark_money(0.0, entry, qty_n),
        "peak_ltp": peak,
        "peak_pct": peak_pct(entry, peak),
        "eos": trade.eos,
        "eor": trade.eor,
        "support": trade.support,
        "resistance": trade.resistance,
        "ist_span": f"{clock_ist(started)}–open",
    }


def _append(state_dir: Path, row: dict[str, Any]) -> None:
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        path = state_dir / LOG_NAME
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        fd = os.open(path, flags, 0o644)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")
        try:
            os.chmod(path, 0o644)
        except OSError:
            pass
        _write_snapshot(state_dir)
    except OSError:
        logger.warning("coa trade log unwritable — Hands fill still stands")


def _write_snapshot(state_dir: Path) -> None:
    try:
        rows = load_trades(state_dir, limit=MAX_ROWS)
        path = state_dir / SNAPSHOT_NAME
        path.write_text(json.dumps({"trades": rows}, default=str), encoding="utf-8")
        os.chmod(path, 0o644)
    except OSError:
        logger.warning("coa trade snapshot skipped")


def _iter_jsonl(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            yield raw


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)
