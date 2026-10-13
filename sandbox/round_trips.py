"""Session round-trip blotter from sandbox fills.

Pairs BUY/SELL fills FIFO per (symbol, exchange, product) so the Positions
page can show strike, entry/exit time, entry/exit LTP, rupee P&L and % RoI
for a closed (qty=0) leftover row instead of Zerodha-style zeros.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from datetime import datetime
from typing import Any

_OPT_RE = re.compile(
    r"^(.+?)(\d{1,2}[A-Z]{3}\d{2})(\d+(?:\.\d+)?)(CE|PE)$",
    re.IGNORECASE,
)
_FUT_RE = re.compile(r"^(.+?)(\d{1,2}[A-Z]{3}\d{2})FUT$", re.IGNORECASE)


def parse_option_symbol(symbol: str) -> dict[str, str | None]:
    """Split an OpenAlgo F&O symbol into underlying / expiry / strike / type."""
    if not symbol:
        return {"underlying": "", "expiry": None, "strike": None, "option_type": None}

    opt = _OPT_RE.match(symbol)
    if opt:
        return {
            "underlying": opt.group(1),
            "expiry": opt.group(2).upper(),
            "strike": opt.group(3),
            "option_type": opt.group(4).upper(),
        }

    fut = _FUT_RE.match(symbol)
    if fut:
        return {
            "underlying": fut.group(1),
            "expiry": fut.group(2).upper(),
            "strike": None,
            "option_type": "FUT",
        }

    return {"underlying": symbol, "expiry": None, "strike": None, "option_type": None}


def _leg_key(fill: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(fill.get("symbol") or ""),
        str(fill.get("exchange") or ""),
        str(fill.get("product") or ""),
    )


def _fmt_ts(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value).strip()
    return text or None


def _weighted_close(lot: dict[str, Any], qty: int, price: float, ts: datetime | str) -> dict[str, Any]:
    """Close ``qty`` of an open lot and return a completed trip slice."""
    parsed = parse_option_symbol(lot["symbol"])
    entry_price = float(lot["price"])
    exit_price = float(price)
    side = lot["side"]
    if side == "BUY":
        pnl_per_unit = exit_price - entry_price
    else:
        pnl_per_unit = entry_price - exit_price
    roi = (pnl_per_unit / entry_price * 100.0) if entry_price else 0.0
    return {
        "symbol": lot["symbol"],
        "exchange": lot["exchange"],
        "product": lot["product"],
        "side": side,
        "underlying": parsed["underlying"],
        "expiry": parsed["expiry"],
        "strike": parsed["strike"],
        "option_type": parsed["option_type"],
        "entry_qty": int(qty),
        "entry_price": round(entry_price, 2),
        "entry_time": _fmt_ts(lot["time"]),
        "exit_qty": int(qty),
        "exit_price": round(exit_price, 2),
        "exit_time": _fmt_ts(ts),
        "open": False,
        "trip_pnl_per_unit": round(pnl_per_unit, 4),
        "roi_percent": round(roi, 2),
    }


def pair_round_trips(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """FIFO-pair fills into round trips.

    Each fill dict needs: symbol, exchange, product, action (BUY/SELL),
    quantity (signed or abs), price, timestamp (datetime or str).
    """
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for fill in fills:
        grouped[_leg_key(fill)].append(fill)

    trips: list[dict[str, Any]] = []
    for series in grouped.values():
        series = sorted(
            series,
            key=lambda item: (
                item.get("timestamp") or datetime.min,
                str(item.get("tradeid") or ""),
            ),
        )
        inventory: deque[dict[str, Any]] = deque()
        for fill in series:
            action = str(fill.get("action") or "").upper()
            if action not in {"BUY", "SELL"}:
                continue
            remaining = abs(int(fill.get("quantity") or 0))
            if remaining <= 0:
                continue
            price = float(fill.get("price") or 0)
            ts = fill.get("timestamp")
            while remaining > 0 and inventory and inventory[0]["side"] != action:
                lot = inventory[0]
                matched = min(int(lot["qty"]), remaining)
                trips.append(_weighted_close(lot, matched, price, ts))
                lot["qty"] -= matched
                remaining -= matched
                if lot["qty"] <= 0:
                    inventory.popleft()
            if remaining > 0:
                inventory.append(
                    {
                        "symbol": fill.get("symbol"),
                        "exchange": fill.get("exchange"),
                        "product": fill.get("product"),
                        "side": action,
                        "qty": remaining,
                        "price": price,
                        "time": ts,
                    }
                )

        for lot in inventory:
            parsed = parse_option_symbol(lot["symbol"])
            trips.append(
                {
                    "symbol": lot["symbol"],
                    "exchange": lot["exchange"],
                    "product": lot["product"],
                    "side": lot["side"],
                    "underlying": parsed["underlying"],
                    "expiry": parsed["expiry"],
                    "strike": parsed["strike"],
                    "option_type": parsed["option_type"],
                    "entry_qty": int(lot["qty"]),
                    "entry_price": round(float(lot["price"]), 2),
                    "entry_time": _fmt_ts(lot["time"]),
                    "exit_qty": 0,
                    "exit_price": None,
                    "exit_time": None,
                    "open": True,
                    "trip_pnl_per_unit": None,
                    "roi_percent": None,
                }
            )
    return trips


def last_trip_by_leg(trips: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Keep the current (open) trip, else the last closed trip, per leg."""
    by_leg: dict[tuple[str, str, str], dict[str, Any]] = {}
    for trip in trips:
        key = (trip["symbol"], trip["exchange"], trip["product"])
        existing = by_leg.get(key)
        if existing is None:
            by_leg[key] = trip
            continue
        if trip.get("open") and not existing.get("open"):
            by_leg[key] = trip
            continue
        if existing.get("open") and not trip.get("open"):
            continue
        existing_ts = existing.get("exit_time") or existing.get("entry_time") or ""
        trip_ts = trip.get("exit_time") or trip.get("entry_time") or ""
        if trip_ts >= existing_ts:
            by_leg[key] = trip
    return by_leg


def blotter_fields_for_position(
    trip: dict[str, Any] | None,
    *,
    ltp: float | None = None,
    contract_value: float = 1.0,
    today_realized_pnl: float | None = None,
) -> dict[str, Any]:
    """Map a FIFO trip onto the extra keys the Positions UI reads."""
    empty = {
        "underlying": None,
        "expiry": None,
        "strike": None,
        "option_type": None,
        "entry_time": None,
        "exit_time": None,
        "entry_price": None,
        "exit_price": None,
        "traded_quantity": None,
        "roi_percent": None,
    }
    if not trip:
        return empty

    parsed = parse_option_symbol(trip.get("symbol") or "")
    entry_price = trip.get("entry_price")
    exit_price = trip.get("exit_price")
    qty = int(trip.get("entry_qty") or 0)
    roi = trip.get("roi_percent")
    if roi is None and entry_price:
        mark = exit_price if exit_price is not None else ltp
        if mark is not None:
            if trip.get("side") == "SELL":
                roi = (float(entry_price) - float(mark)) / float(entry_price) * 100.0
            else:
                roi = (float(mark) - float(entry_price)) / float(entry_price) * 100.0
            roi = round(roi, 2)

    # Prefer rupee P&L already booked today; else last-trip mark * qty * cv
    if today_realized_pnl is None and entry_price is not None and qty:
        mark = exit_price if exit_price is not None else ltp
        if mark is not None:
            direction = 1 if trip.get("side") != "SELL" else -1
            today_realized_pnl = (float(mark) - float(entry_price)) * qty * float(contract_value) * direction

    return {
        "underlying": trip.get("underlying") or parsed["underlying"],
        "expiry": trip.get("expiry") or parsed["expiry"],
        "strike": trip.get("strike") or parsed["strike"],
        "option_type": trip.get("option_type") or parsed["option_type"],
        "entry_time": trip.get("entry_time"),
        "exit_time": trip.get("exit_time"),
        "entry_price": trip.get("entry_price"),
        "exit_price": trip.get("exit_price"),
        "traded_quantity": qty or None,
        "roi_percent": roi,
    }


def trip_rupee_pnl(trip: dict[str, Any], contract_value: float = 1.0) -> float | None:
    """Rupee P&L for a closed trip: (exit-entry) × qty × contract_value (flipped for shorts)."""
    if trip.get("open") or trip.get("entry_price") is None or trip.get("exit_price") is None:
        return None
    qty = int(trip.get("entry_qty") or 0)
    if qty <= 0:
        return None
    entry = float(trip["entry_price"])
    exit_px = float(trip["exit_price"])
    direction = -1 if trip.get("side") == "SELL" else 1
    return round((exit_px - entry) * qty * float(contract_value) * direction, 2)


def serialize_trip(trip: dict[str, Any], contract_value: float = 1.0) -> dict[str, Any]:
    """UI payload for a sandbox/analyzer blotter row."""
    fields = blotter_fields_for_position(trip, contract_value=contract_value)
    pnl = trip_rupee_pnl(trip, contract_value)
    qty = float(fields.get("traded_quantity") or 0)
    entry = float(fields.get("entry_price") or 0)
    premium = qty * entry
    roi = fields.get("roi_percent")
    if pnl is not None and premium:
        roi = round(pnl / premium * 100.0, 2)
    return {
        **fields,
        "symbol": trip.get("symbol"),
        "exchange": trip.get("exchange"),
        "product": trip.get("product"),
        "side": trip.get("side"),
        "open": bool(trip.get("open")),
        "trade_pnl": pnl,
        "roi_percent": roi,
    }


def _parse_log_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def attach_trips_to_requests(
    requests: list[dict[str, Any]],
    trips: list[dict[str, Any]],
    window_seconds: int = 300,
) -> list[dict[str, Any]]:
    """Stamp analyzer log rows with the matching session round-trip.

    placeorder rows match on symbol + entry time; closeposition rows match on
    exit time (symbol is often missing on that API).
    """
    serialized = [
        trip if "traded_quantity" in trip or "trade_pnl" in trip else serialize_trip(trip)
        for trip in trips
    ]

    def _closest(candidates: list[dict[str, Any]], ts: datetime, field: str) -> dict[str, Any] | None:
        best = None
        best_delta = window_seconds + 1
        for item in candidates:
            other = _parse_log_ts(item.get(field))
            if other is None:
                continue
            delta = abs((other - ts).total_seconds())
            if delta <= window_seconds and delta < best_delta:
                best = item
                best_delta = delta
        return best

    for req in requests:
        ts = _parse_log_ts(req.get("timestamp"))
        if ts is None:
            continue
        api_type = str(req.get("api_type") or "")
        symbol = req.get("symbol")
        matched = None
        if api_type in {"placeorder", "placesmartorder"}:
            pool = [item for item in serialized if not symbol or item.get("symbol") == symbol]
            matched = _closest(pool, ts, "entry_time")
        elif api_type == "closeposition":
            pool = [item for item in serialized if not symbol or item.get("symbol") == symbol]
            matched = _closest(pool, ts, "exit_time")
        if not matched:
            continue
        req.update(
            {
                "symbol": req.get("symbol") or matched.get("symbol"),
                "exchange": req.get("exchange") or matched.get("exchange"),
                "strike": matched.get("strike"),
                "option_type": matched.get("option_type"),
                "expiry": matched.get("expiry"),
                "entry_time": matched.get("entry_time"),
                "entry_price": matched.get("entry_price"),
                "traded_quantity": matched.get("traded_quantity"),
            }
        )
        # P&L / exit belong on the close only — stamping them on placeorder
        # repeats the same rupees on two API rows for one round-trip.
        if api_type == "closeposition":
            req.update(
                {
                    "exit_time": matched.get("exit_time"),
                    "exit_price": matched.get("exit_price"),
                    "trade_pnl": matched.get("trade_pnl"),
                    "roi_percent": matched.get("roi_percent"),
                }
            )
    return requests


_ENTRY_APIS = {"placeorder", "placesmartorder"}


def drop_entry_rows_when_closed(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the closeposition recap; hide the matching placeorder for the same fill.

    Open entries (no close yet) stay visible.
    """
    closed: set[tuple[str, str]] = set()
    for req in requests:
        if str(req.get("api_type") or "") != "closeposition":
            continue
        symbol = str(req.get("symbol") or "")
        entry_time = str(req.get("entry_time") or "")
        if symbol and entry_time:
            closed.add((symbol, entry_time))
    if not closed:
        return requests
    kept: list[dict[str, Any]] = []
    for req in requests:
        api_type = str(req.get("api_type") or "")
        if api_type in _ENTRY_APIS:
            key = (str(req.get("symbol") or ""), str(req.get("entry_time") or ""))
            if key in closed:
                continue
        kept.append(req)
    return kept


def _trip_sort_key(trip: dict[str, Any]) -> str:
    return str(trip.get("exit_time") or trip.get("entry_time") or "")


def summarize_round_trips(trips: list[dict[str, Any]]) -> dict[str, Any]:
    """Tradetron-style headline stats from sandbox blotter rows.

    RoI is net rupee P&L divided by premium paid (entry × qty), matching the
    per-row % RoI. Sharpe / sortino / recovery / MAE-MFE are withheld — those
    need daily equity and bar-level excursion data we do not have on fills.
    """
    closed = [
        trip
        for trip in trips
        if not trip.get("open") and trip.get("trade_pnl") is not None
    ]
    closed.sort(key=_trip_sort_key)
    open_count = sum(1 for trip in trips if trip.get("open"))

    empty = {
        "trades": 0,
        "open_trades": open_count,
        "net_pnl": 0.0,
        "roi_percent": None,
        "total_premium": 0.0,
        "winning": 0,
        "losing": 0,
        "even": 0,
        "win_rate": None,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "avg_win": None,
        "avg_loss": None,
        "avg_pnl": None,
        "avg_roi_percent": None,
        "profit_factor": None,
        "profit_factor_infinite": False,
        "expectancy": None,
        "best_trade": None,
        "worst_trade": None,
        "max_consecutive_wins": 0,
        "max_consecutive_losses": 0,
        "max_drawdown": 0.0,
        "max_drawdown_pct": 0.0,
        "pe_trades": 0,
        "pe_pnl": 0.0,
        "ce_trades": 0,
        "ce_pnl": 0.0,
        "withheld": ["sharpe", "sortino", "recovery_factor", "mae", "mfe"],
    }
    if not closed:
        return empty

    pnls = [float(trip["trade_pnl"]) for trip in closed]
    premiums: list[float] = []
    for trip in closed:
        qty = float(trip.get("traded_quantity") or 0)
        entry = float(trip.get("entry_price") or 0)
        premiums.append(qty * entry)

    net = round(sum(pnls), 2)
    premium = round(sum(premiums), 2)
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    evens = [pnl for pnl in pnls if pnl == 0]
    gross_profit = round(sum(wins), 2)
    gross_loss = round(abs(sum(losses)), 2)

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        drawdown = peak - equity
        if drawdown > max_dd:
            max_dd = drawdown

    max_cw = max_cl = streak_w = streak_l = 0
    for pnl in pnls:
        if pnl > 0:
            streak_w += 1
            streak_l = 0
        elif pnl < 0:
            streak_l += 1
            streak_w = 0
        else:
            streak_w = streak_l = 0
        max_cw = max(max_cw, streak_w)
        max_cl = max(max_cl, streak_l)

    pe = [trip for trip in closed if str(trip.get("option_type") or "").upper() == "PE"]
    ce = [trip for trip in closed if str(trip.get("option_type") or "").upper() == "CE"]
    avg_pnl = round(net / len(closed), 2)
    pf_infinite = bool(wins) and not losses

    return {
        **empty,
        "trades": len(closed),
        "net_pnl": net,
        "roi_percent": round(net / premium * 100.0, 2) if premium else None,
        "total_premium": premium,
        "winning": len(wins),
        "losing": len(losses),
        "even": len(evens),
        "win_rate": round(len(wins) / len(closed) * 100.0, 2),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "avg_win": round(gross_profit / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "avg_pnl": avg_pnl,
        "avg_roi_percent": round(net / premium * 100.0, 2) if premium else None,
        "profit_factor": (
            None if pf_infinite else (round(gross_profit / gross_loss, 2) if gross_loss else None)
        ),
        "profit_factor_infinite": pf_infinite,
        "expectancy": avg_pnl,
        "best_trade": round(max(pnls), 2),
        "worst_trade": round(min(pnls), 2),
        "max_consecutive_wins": max_cw,
        "max_consecutive_losses": max_cl,
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd / premium * 100.0, 2) if premium else 0.0,
        "pe_trades": len(pe),
        "pe_pnl": round(sum(float(trip["trade_pnl"]) for trip in pe), 2),
        "ce_trades": len(ce),
        "ce_pnl": round(sum(float(trip["trade_pnl"]) for trip in ce), 2),
    }


def attach_running_totals(trips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stamp each closed trip with running Cum P&L / Cum RoI (oldest → newest).

    Newest closed row then equals headline cumulative P&L and RoI.
    """
    closed_idx = [
        i
        for i, trip in enumerate(trips)
        if not trip.get("open") and trip.get("trade_pnl") is not None
    ]
    closed_idx.sort(key=lambda i: _trip_sort_key(trips[i]))
    running: dict[int, tuple[float, float | None]] = {}
    cum_pnl = 0.0
    cum_premium = 0.0
    for i in closed_idx:
        trip = trips[i]
        cum_pnl = round(cum_pnl + float(trip["trade_pnl"]), 2)
        cum_premium += float(trip.get("traded_quantity") or 0) * float(trip.get("entry_price") or 0)
        running[i] = (
            cum_pnl,
            round(cum_pnl / cum_premium * 100.0, 2) if cum_premium else None,
        )
    stamped: list[dict[str, Any]] = []
    for i, trip in enumerate(trips):
        row = dict(trip)
        if i in running:
            row["cum_pnl"], row["cum_roi_percent"] = running[i]
        else:
            row["cum_pnl"] = None
            row["cum_roi_percent"] = None
        stamped.append(row)
    return stamped
