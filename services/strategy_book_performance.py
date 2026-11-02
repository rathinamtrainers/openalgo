"""Per-strategy performance from OpenAlgo fills.

Any strategy in this section — Strike Desk books, webhook strategies, Python
hosts, and rows created later — is scored the same way: FIFO-match sandbox
trades whose ``strategy`` tag equals or is prefixed by that strategy's id or
name. Known desk prefixes (``strike-desk:``, ``OFA``) stay as aliases so
existing Analyzer/OFA fills keep their book.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from utils.logging import get_logger

logger = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")
TRADE_CAP = 2000
LIST_TRADE_CAP = 200
SQUARE_OFF_TAG = "AUTO_SQUARE_OFF"
TRADING_DAYS_YEAR = 252
RISK_FREE_ANNUAL = 0.065
ACCOUNT_CAPITAL_DEFAULT = 10_000_000.0
# Compounding a 1–2 day burst into a “year” produces junk (e.g. 5 trillion %).
MIN_CALENDAR_DAYS_FOR_YEARLY_PACE = 21
MAX_ABS_YEARLY_PACE_PCT = 500.0

# Aliases for live desk tags that are not the book id/name. Future strategies
# only need to set placeorder ``strategy`` to their id or name.
BOOK_TAG_PREFIXES: dict[str, tuple[str, ...]] = {
    "analyzer-nifty": ("strike-desk:", "strike-desk-NIFTY", "strike-desk-"),
    "ofa-nifty": ("OFA", "OFA:", "OFA-"),
    "coa-nifty": ("COA", "COA:", "COA-"),
}


def identity_prefixes(row: dict[str, Any]) -> tuple[str, ...]:
    """Tags that count as this strategy's fills: id, name, then optional aliases."""
    prefixes: list[str] = []
    for key in ("id", "book_id", "name"):
        value = str(row.get(key) or "").strip()
        if value and value not in prefixes:
            prefixes.append(value)
    extra = row.get("order_prefixes") or BOOK_TAG_PREFIXES.get(
        str(row.get("id") or row.get("book_id") or ""), ()
    )
    for raw in extra:
        value = str(raw or "").strip()
        if value and value not in prefixes:
            prefixes.append(value)
    return tuple(prefixes)


def tag_matches_identity(row: dict[str, Any], strategy: str | None) -> bool:
    """True when a placeorder/sandbox strategy tag belongs to this strategy."""
    text = str(strategy or "").strip()
    if not text or text == SQUARE_OFF_TAG:
        return False
    for needle in identity_prefixes(row):
        if text == needle:
            return True
        if needle.endswith((":", "-")):
            if text.startswith(needle):
                return True
        elif text.startswith(f"{needle}:") or text.startswith(f"{needle}-"):
            return True
    return False


def trade_belongs_to_book(book: dict[str, Any], strategy: str | None) -> bool:
    """True when a sandbox/placeorder strategy tag is this book's fill."""
    return tag_matches_identity(book, strategy)


def _source(row: dict[str, Any]) -> tuple[str, str]:
    if str(row.get("id") or "") == "sandbox-all":
        return (
            "openalgo-analyze",
            "PnL is from every ZeroTouchAlgo Analyze fill in this sandbox.",
        )
    return (
        "openalgo-analyze",
        "PnL is from ZeroTouchAlgo Analyze fills tagged to this strategy "
        "(Analyze sandbox and placeorder strategy names).",
    )


def _as_moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=IST)
    return moment


def _as_ist(value: Any) -> str:
    moment = _as_moment(value)
    if moment is None:
        return str(value or "")
    return moment.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")


def _trading_day(value: Any) -> str:
    moment = _as_moment(value)
    if moment is None:
        return ""
    return moment.astimezone(IST).date().isoformat()


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _position_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("symbol") or ""),
        str(row.get("exchange") or ""),
        str(row.get("product") or ""),
    )


def _load_sandbox_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return plain trade and position dicts. Never raises."""
    try:
        from database.sandbox_db import SandboxPositions, SandboxTrades
    except Exception:
        return [], []
    try:
        trade_rows = (
            SandboxTrades.query.order_by(SandboxTrades.trade_timestamp.desc())
            .limit(TRADE_CAP)
            .all()
        )
        position_rows = SandboxPositions.query.all()
    except Exception:
        logger.exception("sandbox rows unreadable for strategy performance")
        return [], []
    trades: list[dict[str, Any]] = []
    for row in trade_rows:
        stamp = row.trade_timestamp
        trades.append(
            {
                "trade_id": str(row.tradeid or row.id),
                "created_at_utc": (
                    _as_moment(stamp).astimezone(UTC).isoformat() if _as_moment(stamp) else ""
                ),
                "created_at_ist": _as_ist(stamp),
                "trading_day": _trading_day(stamp),
                "symbol": str(row.symbol or ""),
                "exchange": str(row.exchange or ""),
                "product": str(row.product or ""),
                "action": str(row.action or "").upper(),
                "quantity": _int(row.quantity),
                "price": _num(row.price),
                "strategy": str(row.strategy or ""),
            }
        )
    positions: list[dict[str, Any]] = []
    for row in position_rows:
        positions.append(
            {
                "symbol": str(row.symbol or ""),
                "exchange": str(row.exchange or ""),
                "product": str(row.product or ""),
                "quantity": _int(row.quantity),
                "average_price": _num(row.average_price),
                "ltp": _num(row.ltp),
                "unrealized_pnl": _num(row.pnl) if _int(row.quantity) != 0 else 0.0,
                "accumulated_realized_pnl": _num(row.accumulated_realized_pnl),
            }
        )
    return trades, positions


def _in_window(
    trading_day: str | None,
    *,
    day: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> bool:
    text = str(trading_day or "")
    if day:
        return text == day
    if start_date and (not text or text < start_date):
        return False
    if end_date and (not text or text > end_date):
        return False
    return True


def _window_label(
    day: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    if day:
        return day
    if start_date or end_date:
        return f"{start_date or '…'}…{end_date or '…'}"
    return "all"


def _select_book_trades(
    book: dict[str, Any],
    trades: list[dict[str, Any]],
    *,
    day: str | None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    owned: list[dict[str, Any]] = []
    symbols: set[tuple[str, str, str]] = set()
    first_ts: dict[tuple[str, str, str], str] = {}
    window = {"day": day, "start_date": start_date, "end_date": end_date}
    for row in trades:
        if not trade_belongs_to_book(book, row.get("strategy")):
            continue
        if not _in_window(row.get("trading_day"), **window):
            continue
        owned.append(row)
        key = _position_key(row)
        symbols.add(key)
        stamp = str(row.get("created_at_utc") or "")
        if key not in first_ts or stamp < first_ts[key]:
            first_ts[key] = stamp
    extras: list[dict[str, Any]] = []
    for row in trades:
        if str(row.get("strategy") or "") != SQUARE_OFF_TAG:
            continue
        key = _position_key(row)
        if key not in symbols:
            continue
        if not _in_window(row.get("trading_day"), **window):
            continue
        stamp = str(row.get("created_at_utc") or "")
        if stamp and first_ts.get(key) and stamp < first_ts[key]:
            continue
        extras.append(row)
    combined = owned + extras
    combined.sort(key=lambda row: str(row.get("created_at_utc") or ""))
    return combined


def _fifo(
    trades_chrono: list[dict[str, Any]],
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    lots: dict[tuple[str, str, str], deque[dict[str, Any]]] = defaultdict(deque)
    round_trips: list[dict[str, Any]] = []
    realized = 0.0
    for trade in trades_chrono:
        action = str(trade.get("action") or "")
        qty = _int(trade.get("quantity"))
        price = _num(trade.get("price"))
        if action not in {"BUY", "SELL"} or qty <= 0:
            continue
        side = "long" if action == "BUY" else "short"
        key = _position_key(trade)
        remaining = qty
        while remaining > 0 and lots[key] and lots[key][0]["side"] != side:
            lot = lots[key][0]
            closed = min(remaining, lot["qty"])
            if lot["side"] == "long":
                pnl = (price - lot["price"]) * closed
            else:
                pnl = (lot["price"] - price) * closed
            realized += pnl
            capital = round(lot["price"] * closed, 2)
            round_trips.append(
                {
                    "symbol": trade["symbol"],
                    "exchange": trade["exchange"],
                    "product": trade["product"],
                    "quantity": closed,
                    "side": lot["side"],
                    "entry_price": round(lot["price"], 2),
                    "exit_price": round(price, 2),
                    "pnl": round(pnl, 2),
                    "roi_pct": round((pnl / capital) * 100, 2) if capital else None,
                    "entry_at_ist": lot["created_at_ist"],
                    "exit_at_ist": trade.get("created_at_ist"),
                    "trading_day": trade.get("trading_day"),
                    "capital": capital,
                }
            )
            lot["qty"] -= closed
            remaining -= closed
            if lot["qty"] <= 0:
                lots[key].popleft()
        if remaining > 0:
            lots[key].append(
                {
                    "qty": remaining,
                    "price": price,
                    "side": side,
                    "created_at_ist": trade.get("created_at_ist"),
                }
            )

    ltp_map = {_position_key(row): _num(row.get("ltp")) for row in positions}
    open_positions: list[dict[str, Any]] = []
    unrealized = 0.0
    open_quantity = 0
    for key, queue in lots.items():
        for lot in queue:
            signed = lot["qty"] if lot["side"] == "long" else -lot["qty"]
            open_quantity += signed
            ltp = ltp_map.get(key) or 0.0
            if ltp > 0:
                if lot["side"] == "long":
                    u_pnl = (ltp - lot["price"]) * lot["qty"]
                else:
                    u_pnl = (lot["price"] - ltp) * lot["qty"]
            else:
                u_pnl = 0.0
            unrealized += u_pnl
            open_positions.append(
                {
                    "symbol": key[0],
                    "exchange": key[1],
                    "product": key[2],
                    "quantity": signed,
                    "average_price": round(lot["price"], 2),
                    "ltp": round(ltp, 2),
                    "unrealized_pnl": round(u_pnl, 2),
                    "side": lot["side"],
                    "opened_at_ist": lot.get("created_at_ist"),
                }
            )

    wins = sum(1 for row in round_trips if row["pnl"] > 0)
    losses = sum(1 for row in round_trips if row["pnl"] < 0)
    decided = wins + losses
    return {
        "realized_pnl": round(realized, 2),
        "unrealized_pnl": round(unrealized, 2),
        "total_pnl": round(realized + unrealized, 2),
        "closed_round_trips": len(round_trips),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round((wins / decided) * 100, 1) if decided else None,
        "open_quantity": open_quantity,
        "round_trips": list(reversed(round_trips)),
        "open_positions": open_positions,
        "risk": _risk_metrics(
            round_trips,
            open_positions,
            total_pnl=realized + unrealized,
        ),
    }


def _parse_day(value: Any) -> date | None:
    text = str(value or "").strip()
    if len(text) >= 10:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def _weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((item - mean) ** 2 for item in values) / (len(values) - 1)
    return math.sqrt(var) if var > 0 else 0.0


def _empty_risk() -> dict[str, Any]:
    return {
        "capital_deployed": 0.0,
        "account_capital": ACCOUNT_CAPITAL_DEFAULT,
        "roi_pct": None,
        "account_roi_pct": 0.0,
        "cagr_pct": None,
        "cagr_potential_pct": None,
        "sharpe": None,
        "sortino": None,
        "calmar": None,
        "max_drawdown_pct": None,
        "profit_factor": None,
        "expectancy": None,
        "avg_win": None,
        "avg_loss": None,
        "calendar_days": 0,
        "trading_days": 0,
        "profit_days": 0,
        "loss_days": 0,
        "avg_profit_day": None,
        "avg_loss_day": None,
        "avg_day": None,
        "sample_days": 0,
        "sample_note": None,
    }


def _activity_dates(
    round_trips: list[dict[str, Any]],
    open_positions: list[dict[str, Any]],
) -> list[date]:
    dates: list[date] = []
    for row in round_trips:
        for key in ("entry_at_ist", "exit_at_ist", "trading_day"):
            parsed = _parse_day(row.get(key))
            if parsed:
                dates.append(parsed)
    for row in open_positions:
        parsed = _parse_day(row.get("opened_at_ist"))
        if parsed:
            dates.append(parsed)
    return dates


def _yearly_pace_pct(total_pnl: float, capital: float, calendar_days: int) -> float | None:
    """Linear yearly pace from this stretch. Never compound a few days into a fantasy."""
    if calendar_days < MIN_CALENDAR_DAYS_FOR_YEARLY_PACE or capital <= 0:
        return None
    pace = (total_pnl / capital) * (365.25 / calendar_days) * 100
    if not math.isfinite(pace) or abs(pace) > MAX_ABS_YEARLY_PACE_PCT:
        return None
    return round(pace, 2)


def _risk_metrics(
    round_trips: list[dict[str, Any]],
    open_positions: list[dict[str, Any]],
    *,
    total_pnl: float,
    account_capital: float = ACCOUNT_CAPITAL_DEFAULT,
) -> dict[str, Any]:
    """RoI / Sharpe / Sortino / CAGR from this strategy's own closed trades."""
    out = _empty_risk()
    out["account_capital"] = round(account_capital, 2)
    out["account_roi_pct"] = (
        round((total_pnl / account_capital) * 100, 4) if account_capital else None
    )
    closed_capital = sum(
        _num(row.get("capital")) or abs(_num(row.get("entry_price")) * _int(row.get("quantity")))
        for row in round_trips
    )
    open_capital = sum(
        abs(_num(row.get("average_price")) * _int(row.get("quantity"))) for row in open_positions
    )
    capital = closed_capital + open_capital
    out["capital_deployed"] = round(capital, 2)
    if capital > 0:
        out["roi_pct"] = round((total_pnl / capital) * 100, 2)

    wins = [row["pnl"] for row in round_trips if row["pnl"] > 0]
    losses = [row["pnl"] for row in round_trips if row["pnl"] < 0]
    if wins:
        out["avg_win"] = round(sum(wins) / len(wins), 2)
    if losses:
        out["avg_loss"] = round(sum(losses) / len(losses), 2)
    if round_trips:
        out["expectancy"] = round(sum(row["pnl"] for row in round_trips) / len(round_trips), 2)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        out["profit_factor"] = round(gross_win / gross_loss, 2)
    elif gross_win > 0:
        out["profit_factor"] = None

    daily: dict[date, float] = defaultdict(float)
    for row in round_trips:
        day = _parse_day(row.get("trading_day") or row.get("exit_at_ist"))
        if day:
            daily[day] += _num(row.get("pnl"))
    if open_positions:
        last_day = max(daily) if daily else date.today()
        daily[last_day] += sum(_num(row.get("unrealized_pnl")) for row in open_positions)

    span_dates = _activity_dates(round_trips, open_positions)
    span_dates.extend(daily.keys())
    if span_dates:
        first, last = min(span_dates), max(span_dates)
        calendar_days = (last - first).days + 1
        weekdays = _weekdays(first, last)
        profit_vals = [value for value in daily.values() if value > 0]
        loss_vals = [value for value in daily.values() if value < 0]
        out["calendar_days"] = calendar_days
        out["trading_days"] = len(weekdays)
        out["profit_days"] = len(profit_vals)
        out["loss_days"] = len(loss_vals)
        if profit_vals:
            out["avg_profit_day"] = round(sum(profit_vals) / len(profit_vals), 2)
        if loss_vals:
            out["avg_loss_day"] = round(sum(loss_vals) / len(loss_vals), 2)
        if weekdays:
            out["avg_day"] = round(sum(daily.values()) / len(weekdays), 2)
        out["sample_days"] = len(weekdays)
        pace = _yearly_pace_pct(total_pnl, capital, calendar_days)
        if pace is None:
            out["sample_note"] = (
                "Short stretch — yearly pace waits until this has run longer. "
                "A few days can look huge or tiny."
            )
        else:
            out["cagr_pct"] = pace
            out["cagr_potential_pct"] = pace

    if not daily or capital <= 0:
        return out

    span = _weekdays(min(daily), max(daily))
    if not span:
        return out
    returns = [(daily.get(day, 0.0) / capital) for day in span]
    if len(span) < 20 and out["sample_note"] is None:
        out["sample_note"] = "Short sample — ratios are noisy until more trading days land."

    start_equity = capital
    equity = start_equity
    peak = start_equity
    max_dd = 0.0
    for day in span:
        equity += daily.get(day, 0.0)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    out["max_drawdown_pct"] = round(max_dd * 100, 2)
    if out["cagr_potential_pct"] is not None and max_dd > 0:
        out["calmar"] = round((out["cagr_potential_pct"] / 100) / max_dd, 2)

    stdev = _stdev(returns)
    mean = sum(returns) / len(returns)
    rf_daily = RISK_FREE_ANNUAL / TRADING_DAYS_YEAR
    if stdev and stdev > 0:
        out["sharpe"] = round(((mean - rf_daily) / stdev) * math.sqrt(TRADING_DAYS_YEAR), 2)
    downside = [min(item - rf_daily, 0.0) for item in returns]
    down_var = sum(item * item for item in downside) / len(downside)
    if down_var > 0:
        out["sortino"] = round(
            ((mean - rf_daily) / math.sqrt(down_var)) * math.sqrt(TRADING_DAYS_YEAR), 2
        )
    return out


def _decision_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"enter": 0, "hold": 0, "decline": 0}
    for row in events:
        kind = str(row.get("kind") or "")
        if kind not in {"decision", "status"}:
            continue
        outcome = str(row.get("outcome") or "")
        if outcome in counts:
            counts[outcome] += 1
    return counts


def _empty_summary() -> dict[str, Any]:
    return {
        "fills": 0,
        "buys": 0,
        "sells": 0,
        "closed_round_trips": 0,
        "wins": 0,
        "losses": 0,
        "win_rate_pct": None,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "total_pnl": 0.0,
        "open_quantity": 0,
        "days_traded": 0,
        "first_trade_at_ist": None,
        "last_trade_at_ist": None,
        "enter_decisions": 0,
        "hold_decisions": 0,
        "decline_decisions": 0,
        **_empty_risk(),
    }


def _payload(
    book: dict[str, Any],
    *,
    trades: list[dict[str, Any]],
    fifo: dict[str, Any],
    decisions: dict[str, int],
    day: str | None,
    include_lists: bool,
) -> dict[str, Any]:
    source, note = _source(book)
    buys = sum(1 for row in trades if row.get("action") == "BUY")
    sells = sum(1 for row in trades if row.get("action") == "SELL")
    days = sorted({str(row.get("trading_day") or "") for row in trades if row.get("trading_day")})
    newest_first = list(reversed(trades))
    summary = {
        **_empty_summary(),
        "fills": len(trades),
        "buys": buys,
        "sells": sells,
        "closed_round_trips": fifo["closed_round_trips"],
        "wins": fifo["wins"],
        "losses": fifo["losses"],
        "win_rate_pct": fifo["win_rate_pct"],
        "realized_pnl": fifo["realized_pnl"],
        "unrealized_pnl": fifo["unrealized_pnl"],
        "total_pnl": fifo["total_pnl"],
        "open_quantity": fifo["open_quantity"],
        "days_traded": len(days),
        "first_trade_at_ist": trades[0]["created_at_ist"] if trades else None,
        "last_trade_at_ist": trades[-1]["created_at_ist"] if trades else None,
        "enter_decisions": decisions["enter"],
        "hold_decisions": decisions["hold"],
        "decline_decisions": decisions["decline"],
        **(fifo.get("risk") or _empty_risk()),
    }
    payload: dict[str, Any] = {
        "source": source,
        "source_note": note,
        "day": day or "all",
        "summary": summary,
    }
    if include_lists:
        payload["trades"] = newest_first[:LIST_TRADE_CAP]
        payload["round_trips"] = fifo["round_trips"][:LIST_TRADE_CAP]
        payload["open_positions"] = fifo["open_positions"]
    return payload


def book_performance(
    book: dict[str, Any],
    events: list[dict[str, Any]] | None = None,
    *,
    day: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    trades: list[dict[str, Any]] | None = None,
    positions: list[dict[str, Any]] | None = None,
    include_lists: bool = True,
) -> dict[str, Any]:
    """Full performance block for one book page. Never raises."""
    label = _window_label(day, start_date, end_date)
    try:
        loaded_trades, loaded_positions = (
            (trades, positions) if trades is not None else _load_sandbox_rows()
        )
        selected = _select_book_trades(
            book,
            loaded_trades or [],
            day=day,
            start_date=start_date,
            end_date=end_date,
        )
        fifo = _fifo(selected, loaded_positions or [])
        return _payload(
            book,
            trades=selected,
            fifo=fifo,
            decisions=_decision_counts(events or []),
            day=label,
            include_lists=include_lists,
        )
    except Exception:
        logger.exception("book performance failed for %s", book.get("id"))
        source, note = _source(book)
        return {
            "source": source,
            "source_note": note,
            "day": label,
            "summary": _empty_summary(),
            "trades": [],
            "round_trips": [],
            "open_positions": [],
        }


ACCOUNT_BOOK = {
    "id": "sandbox-all",
    "name": "Sandbox Analyze",
    "kind": "analyzer",
}


def sandbox_account_performance(
    *,
    day: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    trades: list[dict[str, Any]] | None = None,
    positions: list[dict[str, Any]] | None = None,
    include_lists: bool = True,
) -> dict[str, Any]:
    """Overall Analyze sandbox performance — every fill, not one book."""
    label = _window_label(day, start_date, end_date)
    try:
        loaded_trades, loaded_positions = (
            (trades, positions) if trades is not None else _load_sandbox_rows()
        )
        selected = [
            row
            for row in (loaded_trades or [])
            if _in_window(row.get("trading_day"), day=day, start_date=start_date, end_date=end_date)
        ]
        selected.sort(key=lambda row: str(row.get("created_at_utc") or ""))
        fifo = _fifo(selected, loaded_positions or [])
        return _payload(
            ACCOUNT_BOOK,
            trades=selected,
            fifo=fifo,
            decisions={"enter": 0, "hold": 0, "decline": 0},
            day=label,
            include_lists=include_lists,
        )
    except Exception:
        logger.exception("sandbox account performance failed")
        source, note = _source(ACCOUNT_BOOK)
        return {
            "source": source,
            "source_note": note,
            "day": label,
            "summary": _empty_summary(),
            "trades": [],
            "round_trips": [],
            "open_positions": [],
        }


def _trade_summary_from_trip(book: dict[str, Any], trip: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy_id": str(book.get("id") or ""),
        "strategy_name": str(book.get("name") or book.get("id") or ""),
        "status": "closed",
        "symbol": trip.get("symbol"),
        "exchange": trip.get("exchange"),
        "product": trip.get("product"),
        "side": trip.get("side"),
        "quantity": trip.get("quantity"),
        "entry_price": trip.get("entry_price"),
        "exit_price": trip.get("exit_price"),
        "pnl": trip.get("pnl"),
        "roi_pct": trip.get("roi_pct"),
        "entry_at_ist": trip.get("entry_at_ist"),
        "exit_at_ist": trip.get("exit_at_ist"),
        "trading_day": trip.get("trading_day"),
    }


def _trade_summary_from_open(book: dict[str, Any], position: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy_id": str(book.get("id") or ""),
        "strategy_name": str(book.get("name") or book.get("id") or ""),
        "status": "open",
        "symbol": position.get("symbol"),
        "exchange": position.get("exchange"),
        "product": position.get("product"),
        "side": position.get("side"),
        "quantity": position.get("quantity"),
        "entry_price": position.get("average_price"),
        "exit_price": None,
        "pnl": position.get("unrealized_pnl"),
        "roi_pct": None,
        "entry_at_ist": position.get("opened_at_ist"),
        "exit_at_ist": None,
        "trading_day": None,
    }


def sandbox_strategy_report(
    *,
    day: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Per-strategy sandbox PnL plus one summary row per round trip.

    Registered books come first (registry order). Leftover placeorder tags that
    do not match a book are appended. AUTO_SQUARE_OFF fills stay attached to
    the book that opened the lot — they are not a separate strategy.
    """
    trades, positions = _load_sandbox_rows()
    try:
        from database.strategy_books_db import list_books

        books = list(list_books() or [])
    except Exception:
        logger.exception("strategy books unreadable for sandbox report")
        books = []

    claimed: set[str] = set()
    strategies: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    def _append_book(book: dict[str, Any], selected: list[dict[str, Any]]) -> None:
        fifo = _fifo(selected, positions or [])
        payload = _payload(
            book,
            trades=selected,
            fifo=fifo,
            decisions={"enter": 0, "hold": 0, "decline": 0},
            day=_window_label(day, start_date, end_date),
            include_lists=True,
        )
        book_id = str(book.get("id") or "")
        strategies.append(
            {
                "id": book_id,
                "name": str(book.get("name") or book_id),
                "kind": str(book.get("kind") or "unassigned"),
                "href": f"/strategy/books/{book_id}" if not book_id.startswith("tag:") else None,
                "performance": payload,
            }
        )
        for trip in payload.get("round_trips") or []:
            summaries.append(_trade_summary_from_trip(book, trip))
        for open_row in payload.get("open_positions") or []:
            summaries.append(_trade_summary_from_open(book, open_row))

    for book in books:
        selected = _select_book_trades(
            book, trades, day=day, start_date=start_date, end_date=end_date
        )
        for row in selected:
            trade_id = str(row.get("trade_id") or "")
            if trade_id:
                claimed.add(trade_id)
        _append_book(book, selected)

    leftover: dict[str, list[dict[str, Any]]] = {}
    for row in trades:
        trade_id = str(row.get("trade_id") or "")
        if trade_id and trade_id in claimed:
            continue
        tag = str(row.get("strategy") or "").strip()
        if not tag or tag == SQUARE_OFF_TAG:
            continue
        if not _in_window(row.get("trading_day"), day=day, start_date=start_date, end_date=end_date):
            continue
        leftover.setdefault(tag, []).append(row)

    for tag, rows in leftover.items():
        rows.sort(key=lambda item: str(item.get("created_at_utc") or ""))
        _append_book({"id": f"tag:{tag}", "name": tag, "kind": "unassigned"}, rows)

    summaries.sort(
        key=lambda row: str(row.get("exit_at_ist") or row.get("entry_at_ist") or ""),
        reverse=True,
    )
    report = {
        "overall": sandbox_account_performance(
            day=day,
            start_date=start_date,
            end_date=end_date,
            trades=trades,
            positions=positions,
            include_lists=True,
        ),
        "strategies": strategies,
        "trade_summaries": summaries[:LIST_TRADE_CAP],
    }
    try:
        from services.strategy_performance_narrate import attach_narratives

        attach_narratives(report)
    except Exception:
        logger.exception("attach_narratives failed")
    return report


def attach_performance_summaries(books: list[dict[str, Any]]) -> None:
    """Add a compact ``performance`` dict onto each My Strategies card."""
    try:
        trades, positions = _load_sandbox_rows()
    except Exception:
        logger.exception("sandbox performance summaries unreadable")
        trades, positions = [], []
    for book in books:
        try:
            selected = _select_book_trades(book, trades, day=None)
            fifo = _fifo(selected, positions)
            book["performance"] = _payload(
                book,
                trades=selected,
                fifo=fifo,
                decisions={"enter": 0, "hold": 0, "decline": 0},
                day=None,
                include_lists=False,
            )
        except Exception:
            logger.exception("performance summary failed for %s", book.get("id"))
            source, note = _source(book)
            book["performance"] = {
                "source": source,
                "source_note": note,
                "day": "all",
                "summary": _empty_summary(),
            }
