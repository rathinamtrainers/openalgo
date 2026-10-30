"""5-minute Chart of Accuracy replay. Mind only — no Tradetron webhook."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol

from .config import IST
from .ltp_coa import NIFTY_STRIKE_STEP, CoaSnapshot, evaluate
from .ltp_coa_exits import OpenTrade, can_reenter, decide_exit, in_entry_window
from .ltp_coa_intel import IntelState, apply_handbook, atm_oi

logger = logging.getLogger(__name__)

NIFTY_LOT = 65
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)


class HistoryFeed(Protocol):
    def history(
        self,
        symbol: str,
        exchange: str,
        interval: str,
        start_date: date,
        end_date: date,
        source: str = "api",
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class TapeBar:
    ts: datetime
    spot: float
    atm: float
    expiry: str
    rows: list[dict[str, Any]]


@dataclass(frozen=True)
class Fill:
    side: str
    symbol: str
    entry_ts: datetime
    exit_ts: datetime
    entry: float
    exit: float
    scenario: str
    reason: str
    points: float
    pnl: float


@dataclass
class DayBucket:
    bars: int = 0
    signals: int = 0
    empty_chain: int = 0


@dataclass
class ReplayReport:
    bars: int = 0
    signals: int = 0
    fills: list[Fill] = field(default_factory=list)
    skipped_empty_chain: int = 0
    by_day: dict[date, DayBucket] = field(default_factory=dict)

    @property
    def wins(self) -> int:
        return sum(1 for fill in self.fills if fill.pnl > 0)

    @property
    def losses(self) -> int:
        return sum(1 for fill in self.fills if fill.pnl < 0)

    @property
    def net(self) -> float:
        return sum(fill.pnl for fill in self.fills)

    @property
    def profit_factor(self) -> float | None:
        gross_win = sum(fill.pnl for fill in self.fills if fill.pnl > 0)
        gross_loss = -sum(fill.pnl for fill in self.fills if fill.pnl < 0)
        if gross_loss <= 0:
            return None if gross_win <= 0 else float("inf")
        return gross_win / gross_loss


@dataclass
class _OpenPos:
    symbol: str
    strike: float
    trade: OpenTrade


def option_symbol(underlying: str, expiry: str, strike: float, option_type: str) -> str:
    strike_s = str(int(strike)) if strike == int(strike) else str(strike)
    return f"{underlying}{expiry}{strike_s}{option_type.upper()}"


def expiry_token(day: date) -> str:
    return day.strftime("%d%b%y").upper()


def parse_bar_time(row: Mapping[str, Any]) -> datetime:
    raw = row.get("timestamp", row.get("time", row.get("datetime")))
    if raw is None:
        raise ValueError("history bar missing timestamp")
    if hasattr(raw, "to_pydatetime"):
        parsed = raw.to_pydatetime()
        return parsed.astimezone(IST) if parsed.tzinfo else parsed.replace(tzinfo=IST)
    if isinstance(raw, datetime):
        return raw.astimezone(IST) if raw.tzinfo else raw.replace(tzinfo=IST)
    if isinstance(raw, (int, float)):
        seconds = raw / 1000.0 if raw > 1e12 else float(raw)
        return datetime.fromtimestamp(seconds, tz=IST)
    text = str(raw).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed.astimezone(IST) if parsed.tzinfo else parsed.replace(tzinfo=IST)


def bar_float(row: Mapping[str, Any], *names: str) -> float:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return float(row[name])
    return 0.0


def in_session(ts: datetime) -> bool:
    clock = ts.astimezone(IST).time()
    return SESSION_OPEN <= clock <= SESSION_CLOSE


def default_weekly_expiry(day: date, weekday: int = 1) -> date:
    return day + timedelta(days=(weekday - day.weekday()) % 7)


def build_tape(
    feed: HistoryFeed,
    start: date,
    end: date,
    *,
    underlying: str = "NIFTY",
    spot_exchange: str = "NSE_INDEX",
    option_exchange: str = "NFO",
    interval: str = "5m",
    strike_count: int = 10,
    strike_step: float = NIFTY_STRIKE_STEP,
    source: str = "api",
    expiry_for: Callable[[date], date] | None = None,
    history: Callable[..., list[dict[str, Any]]] | None = None,
) -> list[TapeBar]:
    """Rebuild a 5-minute option-chain tape from per-symbol history bars."""
    spot_rows = feed.history(underlying, spot_exchange, interval, start, end, source)
    fetch = history or feed.history
    spot_bars = []
    for row in spot_rows:
        ts = parse_bar_time(row)
        close = bar_float(row, "close", "c", "ltp")
        if close > 0 and in_session(ts) and start <= ts.date() <= end:
            spot_bars.append((ts, close))
    spot_bars.sort(key=lambda item: item[0])
    if not spot_bars:
        return []

    pick_expiry = expiry_for or (lambda day: default_weekly_expiry(day))
    needed: dict[str, tuple[str, float, str]] = {}
    plan: list[tuple[datetime, float, float, str, list[float]]] = []
    for ts, spot in spot_bars:
        expiry_day = pick_expiry(ts.date())
        token = expiry_token(expiry_day)
        atm = round(spot / strike_step) * strike_step
        strikes = [atm + i * strike_step for i in range(-strike_count, strike_count + 1)]
        plan.append((ts, spot, atm, token, strikes))
        for strike in strikes:
            for wing in ("CE", "PE"):
                symbol = option_symbol(underlying, token, strike, wing)
                needed[symbol] = (token, strike, wing)

    indexed: dict[str, dict[int, dict[str, Any]]] = {}
    for symbol in needed:
        try:
            rows = fetch(symbol, option_exchange, interval, start, end, source)
        except Exception as exc:
            logger.warning("history skip %s: %s", symbol, exc)
            rows = []
        by_ts: dict[int, dict[str, Any]] = {}
        for row in rows:
            try:
                ts = parse_bar_time(row)
            except ValueError:
                continue
            by_ts[_ts_key(ts)] = row
        indexed[symbol] = by_ts

    tape: list[TapeBar] = []
    cum_vol: dict[str, float] = {}
    last_oi: dict[str, float] = {}
    current_day: date | None = None
    for ts, spot, atm, token, strikes in plan:
        day = ts.date()
        if day != current_day:
            cum_vol.clear()
            last_oi.clear()
            current_day = day
        key = _ts_key(ts)
        rows: list[dict[str, Any]] = []
        for strike in strikes:
            ce_sym = option_symbol(underlying, token, strike, "CE")
            pe_sym = option_symbol(underlying, token, strike, "PE")
            rows.append(
                {
                    "strike": strike,
                    "ce": _side_snapshot(indexed, cum_vol, last_oi, ce_sym, key),
                    "pe": _side_snapshot(indexed, cum_vol, last_oi, pe_sym, key),
                }
            )
        tape.append(TapeBar(ts=ts, spot=spot, atm=atm, expiry=token, rows=rows))
    return tape


def replay(
    tape: list[TapeBar],
    *,
    lot_size: int = NIFTY_LOT,
    extension_pts: float = 20.0,
    underlying: str = "NIFTY",
) -> ReplayReport:
    """Walk 5-minute bars, fire CoA, and paper-trade ATM weekly CE/PE."""
    report = ReplayReport()
    open_pos: _OpenPos | None = None
    last_exit: datetime | None = None
    intel = IntelState()

    for bar in tape:
        report.bars += 1
        clock = bar.ts.astimezone(IST).time()
        day = bar.ts.date()
        bucket = report.by_day.setdefault(day, DayBucket())
        bucket.bars += 1
        snap = evaluate(bar.rows, bar.spot, atm=bar.atm, extension_pts=extension_pts)
        call_oi, put_oi = atm_oi(bar.rows, bar.atm)
        snap, intel = apply_handbook(
            snap, intel, bar.ts, call_oi=call_oi, put_oi=put_oi
        )
        if snap.signal != "FLAT":
            report.signals += 1
            bucket.signals += 1
        if not any(_side_ltp(row, "ce") or _side_ltp(row, "pe") for row in bar.rows):
            report.skipped_empty_chain += 1
            bucket.empty_chain += 1

        if open_pos is not None:
            ltp = _option_ltp(bar.rows, open_pos.strike, open_pos.trade.side)
            open_pos.trade.note_ltp(ltp)
            exit_reason = decide_exit(clock, bar.spot, ltp, open_pos.trade, snap)
            if exit_reason and ltp > 0:
                report.fills.append(_close(open_pos, bar.ts, ltp, exit_reason, lot_size))
                last_exit = None if exit_reason == "15:15 flatten" else bar.ts
                open_pos = None

        if open_pos is not None:
            continue
        if not in_entry_window(clock):
            continue
        if not can_reenter(bar.ts, last_exit):
            continue
        if snap.signal == "BUY_CE":
            fill = _enter(bar, snap, "CE", underlying)
            if fill is not None:
                open_pos = fill
        elif snap.signal == "BUY_PE":
            fill = _enter(bar, snap, "PE", underlying)
            if fill is not None:
                open_pos = fill

    if open_pos is not None and tape:
        last = tape[-1]
        ltp = _option_ltp(last.rows, open_pos.strike, open_pos.trade.side) or open_pos.trade.entry
        report.fills.append(_close(open_pos, last.ts, ltp, "end of tape", lot_size))
    return report


def format_report(report: ReplayReport) -> str:
    pf = report.profit_factor
    pf_text = "n/a" if pf is None else ("inf" if pf == float("inf") else f"{pf:.2f}")
    lines = [
        f"bars={report.bars}  signals={report.signals}  trades={len(report.fills)}  "
        f"wins={report.wins}  losses={report.losses}  net=INR {report.net:,.0f}  PF={pf_text}"
        + (f"  empty_chain={report.skipped_empty_chain}" if report.skipped_empty_chain else ""),
    ]
    if len(report.by_day) > 1:
        for day, bucket in sorted(report.by_day.items()):
            day_fills = [fill for fill in report.fills if fill.entry_ts.date() == day]
            day_net = sum(fill.pnl for fill in day_fills)
            lines.append(
                f"  day {day.isoformat()}  bars={bucket.bars}  signals={bucket.signals}  "
                f"trades={len(day_fills)}  empty={bucket.empty_chain}  pnl=INR {day_net:,.0f}"
            )
    for fill in report.fills:
        lines.append(
            f"  {fill.entry_ts.strftime('%Y-%m-%d %H:%M')} {fill.side} {fill.symbol}  "
            f"{fill.entry:.2f}->{fill.exit:.2f}  {fill.reason}  "
            f"CoA {fill.scenario}  INR {fill.pnl:,.0f}"
        )
    return "\n".join(lines)


def _ts_key(ts: datetime) -> int:
    local = ts.astimezone(IST).replace(second=0, microsecond=0)
    minute = (local.minute // 5) * 5
    return int(local.replace(minute=minute).timestamp())


def _side_snapshot(
    indexed: dict[str, dict[int, dict[str, Any]]],
    cum_vol: dict[str, float],
    last_oi: dict[str, float],
    symbol: str,
    key: int,
) -> dict[str, Any]:
    row = indexed.get(symbol, {}).get(key, {})
    bar_vol = bar_float(row, "volume", "v")
    cum_vol[symbol] = cum_vol.get(symbol, 0.0) + bar_vol
    oi = bar_float(row, "oi", "open_interest")
    held_oi = oi if oi > 0 else last_oi.get(symbol, 0.0)
    last_oi[symbol] = held_oi
    ltp = bar_float(row, "close", "c", "ltp")
    return {"symbol": symbol, "volume": cum_vol[symbol], "oi": held_oi, "ltp": ltp}


def _side_ltp(row: dict[str, Any], side: str) -> float:
    data = row.get(side)
    if not isinstance(data, dict):
        return 0.0
    return float(data.get("ltp") or 0)


def _option_ltp(rows: list[dict[str, Any]], strike: float, side: str) -> float:
    key = "ce" if side == "CE" else "pe"
    for row in rows:
        if float(row.get("strike") or 0) == strike:
            return _side_ltp(row, key)
    return 0.0


def _enter(bar: TapeBar, snap: CoaSnapshot, side: str, underlying: str) -> _OpenPos | None:
    ltp = _option_ltp(bar.rows, bar.atm, side)
    if ltp <= 0:
        return None
    symbol = option_symbol(underlying, bar.expiry, bar.atm, side)
    return _OpenPos(
        symbol=symbol,
        strike=bar.atm,
        trade=OpenTrade(
            side=side,
            entry=ltp,
            entry_ts=bar.ts,
            peak=ltp,
            eos=snap.eos,
            eor=snap.eor,
            support=None if snap.support is None else snap.support.strike,
            resistance=None if snap.resistance is None else snap.resistance.strike,
            scenario=snap.scenario or "?",
            strike=bar.atm,
        ),
    )


def _close(pos: _OpenPos, ts: datetime, ltp: float, reason: str, lot_size: int) -> Fill:
    points = ltp - pos.trade.entry
    return Fill(
        side=pos.trade.side,
        symbol=pos.symbol,
        entry_ts=pos.trade.entry_ts,
        exit_ts=ts,
        entry=pos.trade.entry,
        exit=ltp,
        scenario=pos.trade.scenario or "?",
        reason=reason,
        points=points,
        pnl=points * lot_size,
    )
