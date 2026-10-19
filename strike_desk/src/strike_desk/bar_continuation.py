"""Last-bar continuation frequency plus ADX / RSI / EMA gates.

Independent reader for NIFTY. Not an Expo or LuxAlgo port, not CoA, and it
never talks to a broker or Tradetron.

On each completed bar S the book records whether the *next* bar took out S.high
and/or S.low, split by whether S was green or red. Bias is the historically
more frequent excursion after that colour. Entries also require ADX trend,
DI agreement, RSI in a mid-band, and EMA(20) alignment. Fills are the following
bar's open (no same-bar lookahead).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any, Literal
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .config import IST

Bias = Literal["BULL", "BEAR", "FLAT"]
Side = Literal["LONG", "SHORT"]

NIFTY_LOT = 65
SESSION_OPEN = time(9, 15)
ENTRY_OPEN = time(9, 30)
FLATTEN = time(15, 15)
SESSION_CLOSE = time(15, 30)

ADX_PERIOD = 14
RSI_PERIOD = 14
EMA_PERIOD = 20
MIN_COLOR_SAMPLES = 50
ADX_MIN = 20.0
RSI_LONG = (50.0, 70.0)
RSI_SHORT = (30.0, 50.0)
SLIPPAGE_PTS = 0.5
COMMISSION_INR = 40.0
TARGET_R = 1.5


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class ColorBucket:
    n: int = 0
    hh: int = 0
    ll: int = 0

    def p_hh(self) -> float | None:
        return None if self.n == 0 else self.hh / self.n

    def p_ll(self) -> float | None:
        return None if self.n == 0 else self.ll / self.n


@dataclass
class FreqBook:
    green: ColorBucket = field(default_factory=ColorBucket)
    red: ColorBucket = field(default_factory=ColorBucket)


@dataclass(frozen=True)
class Snapshot:
    ts: datetime
    bias: Bias
    setup_green: bool
    p_hh: float | None
    p_ll: float | None
    adx: float | None
    di_plus: float | None
    di_minus: float | None
    rsi: float | None
    ema: float | None
    close: float
    signal: Side | None
    reason: str


@dataclass(frozen=True)
class Fill:
    side: Side
    entry_ts: datetime
    exit_ts: datetime
    entry: float
    exit: float
    points: float
    pnl_inr: float
    reason: str
    bars_held: int


@dataclass
class BacktestReport:
    symbol: str
    interval: str
    bars: int = 0
    signals: int = 0
    fills: list[Fill] = field(default_factory=list)
    skipped_warmup: int = 0
    skipped_session: int = 0

    @property
    def wins(self) -> int:
        return sum(1 for fill in self.fills if fill.pnl_inr > 0)

    @property
    def losses(self) -> int:
        return sum(1 for fill in self.fills if fill.pnl_inr < 0)

    @property
    def net_pts(self) -> float:
        return sum(fill.points for fill in self.fills)

    @property
    def net_inr(self) -> float:
        return sum(fill.pnl_inr for fill in self.fills)

    @property
    def profit_factor(self) -> float | None:
        gross_win = sum(fill.pnl_inr for fill in self.fills if fill.pnl_inr > 0)
        gross_loss = -sum(fill.pnl_inr for fill in self.fills if fill.pnl_inr < 0)
        if gross_loss <= 0:
            return None if gross_win <= 0 else float("inf")
        return gross_win / gross_loss


def is_green(bar: Bar) -> bool:
    return bar.close > bar.open


def observe(book: FreqBook, setup: Bar, follow: Bar) -> None:
    """Record whether ``follow`` tagged the setup high and/or low."""
    if setup.close == setup.open:
        return
    bucket = book.green if is_green(setup) else book.red
    bucket.n += 1
    if follow.high >= setup.high:
        bucket.hh += 1
    if follow.low <= setup.low:
        bucket.ll += 1


def bias_from(book: FreqBook, setup_green: bool, min_n: int = MIN_COLOR_SAMPLES) -> Bias:
    bucket = book.green if setup_green else book.red
    if bucket.n < min_n:
        return "FLAT"
    p_hh = bucket.hh / bucket.n
    p_ll = bucket.ll / bucket.n
    if p_hh > p_ll:
        return "BULL"
    if p_ll > p_hh:
        return "BEAR"
    return "FLAT"


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period or period <= 0:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    alpha = 2.0 / (period + 1)
    for i in range(period, len(values)):
        prev = out[i - 1]
        assert prev is not None
        out[i] = values[i] * alpha + prev * (1.0 - alpha)
    return out


def wilder_rsi(closes: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gain = 0.0
    loss = 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        gain += max(delta, 0.0)
        loss += max(-delta, 0.0)
    avg_gain = gain / period
    avg_loss = loss / period
    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
        out[i] = _rsi(avg_gain, avg_loss)
    return out


def _rsi(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def adx_di(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = ADX_PERIOD,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    n = len(closes)
    di_plus: list[float | None] = [None] * n
    di_minus: list[float | None] = [None] * n
    adx: list[float | None] = [None] * n
    if n <= period + 1:
        return di_plus, di_minus, adx

    tr_sum = 0.0
    plus_sum = 0.0
    minus_sum = 0.0
    dx_seed: list[float] = []
    smooth_tr = 0.0
    smooth_plus = 0.0
    smooth_minus = 0.0

    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm = up if up > down and up > 0 else 0.0
        minus_dm = down if down > up and down > 0 else 0.0
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        if i < period:
            tr_sum += tr
            plus_sum += plus_dm
            minus_sum += minus_dm
            continue
        if i == period:
            tr_sum += tr
            plus_sum += plus_dm
            minus_sum += minus_dm
            smooth_tr, smooth_plus, smooth_minus = tr_sum, plus_sum, minus_sum
        else:
            smooth_tr = smooth_tr - (smooth_tr / period) + tr
            smooth_plus = smooth_plus - (smooth_plus / period) + plus_dm
            smooth_minus = smooth_minus - (smooth_minus / period) + minus_dm
        plus, minus, dx = _di_dx(smooth_tr, smooth_plus, smooth_minus)
        di_plus[i] = plus
        di_minus[i] = minus
        if dx is None:
            continue
        if len(dx_seed) < period:
            dx_seed.append(dx)
            if len(dx_seed) == period:
                adx[i] = sum(dx_seed) / period
        else:
            prev = adx[i - 1]
            adx[i] = dx if prev is None else (prev * (period - 1) + dx) / period
    return di_plus, di_minus, adx


def _di_dx(
    smooth_tr: float, smooth_plus: float, smooth_minus: float
) -> tuple[float | None, float | None, float | None]:
    if smooth_tr <= 0:
        return None, None, None
    plus = 100.0 * smooth_plus / smooth_tr
    minus = 100.0 * smooth_minus / smooth_tr
    denom = plus + minus
    dx = None if denom <= 0 else 100.0 * abs(plus - minus) / denom
    return plus, minus, dx


def decide_signal(
    *,
    bias: Bias,
    adx: float | None,
    di_plus: float | None,
    di_minus: float | None,
    rsi: float | None,
    ema_value: float | None,
    close: float,
    adx_min: float = ADX_MIN,
) -> tuple[Side | None, str]:
    if bias == "FLAT":
        return None, "bias flat"
    if adx is None or di_plus is None or di_minus is None or rsi is None or ema_value is None:
        return None, "indicators warming"
    if adx < adx_min:
        return None, f"ADX {adx:.1f} < {adx_min:g}"
    if bias == "BULL":
        if di_plus <= di_minus:
            return None, "+DI not above -DI"
        lo, hi = RSI_LONG
        if not (lo <= rsi <= hi):
            return None, f"RSI {rsi:.1f} outside {lo:g}-{hi:g}"
        if close <= ema_value:
            return None, "close not above EMA20"
        return "LONG", "BULL + ADX + RSI + EMA"
    if di_minus <= di_plus:
        return None, "-DI not above +DI"
    lo, hi = RSI_SHORT
    if not (lo <= rsi <= hi):
        return None, f"RSI {rsi:.1f} outside {lo:g}-{hi:g}"
    if close >= ema_value:
        return None, "close not below EMA20"
    return "SHORT", "BEAR + ADX + RSI + EMA"


def snapshots(bars: list[Bar], min_n: int = MIN_COLOR_SAMPLES) -> list[Snapshot]:
    """One snapshot per bar, using only that bar and earlier history."""
    closes = [bar.close for bar in bars]
    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]
    rsi_arr = wilder_rsi(closes)
    di_plus, di_minus, adx_arr = adx_di(highs, lows, closes)
    ema_arr = ema(closes, EMA_PERIOD)
    book = FreqBook()
    out: list[Snapshot] = []
    for i, bar in enumerate(bars):
        if i >= 2:
            observe(book, bars[i - 2], bars[i - 1])
        setup_green = is_green(bar)
        bias = bias_from(book, setup_green, min_n=min_n)
        bucket = book.green if setup_green else book.red
        side, reason = decide_signal(
            bias=bias,
            adx=adx_arr[i],
            di_plus=di_plus[i],
            di_minus=di_minus[i],
            rsi=rsi_arr[i],
            ema_value=ema_arr[i],
            close=bar.close,
        )
        out.append(
            Snapshot(
                ts=bar.ts,
                bias=bias,
                setup_green=setup_green,
                p_hh=bucket.p_hh(),
                p_ll=bucket.p_ll(),
                adx=adx_arr[i],
                di_plus=di_plus[i],
                di_minus=di_minus[i],
                rsi=rsi_arr[i],
                ema=ema_arr[i],
                close=bar.close,
                signal=side,
                reason=reason,
            )
        )
    return out


def in_entry_window(ts: datetime) -> bool:
    clock = ts.astimezone(IST).time()
    return ENTRY_OPEN <= clock < FLATTEN


def is_flatten_bar(ts: datetime) -> bool:
    clock = ts.astimezone(IST).time()
    return clock >= FLATTEN


def _in_session(ts: datetime) -> bool:
    clock = ts.astimezone(IST).time()
    return SESSION_OPEN <= clock <= SESSION_CLOSE


def backtest(
    bars: list[Bar],
    *,
    symbol: str = "NIFTY",
    interval: str = "15m",
    min_n: int = MIN_COLOR_SAMPLES,
    lot_size: int = NIFTY_LOT,
    target_r: float = TARGET_R,
    flatten_eod: bool = True,
) -> BacktestReport:
    report = BacktestReport(symbol=symbol, interval=interval, bars=len(bars))
    if len(bars) < 3:
        return report
    snaps = snapshots(bars, min_n=min_n)
    open_side: Side | None = None
    entry = 0.0
    stop = 0.0
    target = 0.0
    entry_ts: datetime | None = None
    entry_index = 0

    def close_trade(exit_ts: datetime, raw_exit: float, reason: str, held: int) -> None:
        nonlocal open_side, entry_ts
        assert open_side is not None and entry_ts is not None
        fill_exit = raw_exit - SLIPPAGE_PTS if open_side == "LONG" else raw_exit + SLIPPAGE_PTS
        points = (fill_exit - entry) if open_side == "LONG" else (entry - fill_exit)
        pnl = points * lot_size - COMMISSION_INR
        report.fills.append(
            Fill(
                side=open_side,
                entry_ts=entry_ts,
                exit_ts=exit_ts,
                entry=entry,
                exit=fill_exit,
                points=points,
                pnl_inr=pnl,
                reason=reason,
                bars_held=held,
            )
        )
        open_side = None
        entry_ts = None

    for t in range(1, len(bars)):
        bar = bars[t]
        prev = snaps[t - 1]
        daily = flatten_eod and _in_session(bar.ts)

        if open_side is not None:
            hit_stop = bar.low <= stop if open_side == "LONG" else bar.high >= stop
            hit_target = bar.high >= target if open_side == "LONG" else bar.low <= target
            if hit_stop:
                close_trade(bar.ts, stop, "stop", t - entry_index)
            elif hit_target:
                close_trade(bar.ts, target, "target", t - entry_index)
            elif daily and is_flatten_bar(bar.ts):
                close_trade(bar.ts, bar.close, "15:15 flatten", t - entry_index)

        if open_side is not None:
            continue
        if daily and not in_entry_window(bar.ts):
            report.skipped_session += 1
            continue
        if prev.signal is None:
            if prev.reason.startswith("bias") or "warming" in prev.reason:
                report.skipped_warmup += 1
            continue
        report.signals += 1
        raw_entry = bar.open
        fill_entry = (
            raw_entry + SLIPPAGE_PTS if prev.signal == "LONG" else raw_entry - SLIPPAGE_PTS
        )
        risk = abs(fill_entry - (bars[t - 1].low if prev.signal == "LONG" else bars[t - 1].high))
        if risk <= 0:
            continue
        open_side = prev.signal
        entry = fill_entry
        entry_ts = bar.ts
        entry_index = t
        if open_side == "LONG":
            stop = bars[t - 1].low
            target = entry + target_r * risk
        else:
            stop = bars[t - 1].high
            target = entry - target_r * risk

    if open_side is not None:
        last = bars[-1]
        close_trade(last.ts, last.close, "end of tape", len(bars) - 1 - entry_index)
    return report


def session_bars(bars: list[Bar], interval: str) -> list[Bar]:
    """Drop overnight prints on intraday NIFTY tapes."""
    if interval in {"D", "1d", "day"}:
        return bars
    return [bar for bar in bars if _in_session(bar.ts)]


def parse_history_rows(rows: list[dict[str, Any]]) -> list[Bar]:
    bars: list[Bar] = []
    for row in rows:
        try:
            raw_ts = row.get("timestamp") or row.get("time") or row.get("datetime")
            ts = _parse_ts(raw_ts)
            ohlc = (
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if ts is None or any(not math.isfinite(value) or value <= 0 for value in ohlc):
            continue
        open_, high, low, close = ohlc
        if high < max(open_, close) or low > min(open_, close):
            continue
        bars.append(Bar(ts=ts, open=open_, high=high, low=low, close=close))
    bars.sort(key=lambda bar: bar.ts)
    return bars


def fetch_yahoo_nifty(interval: str = "15m", range_hint: str | None = None) -> list[Bar]:
    """Public Yahoo chart for ^NSEI. Fallback when OpenAlgo history is empty."""
    yahoo_interval, span = _yahoo_range(interval, range_hint)
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI"
        f"?interval={yahoo_interval}&range={span}&events=history"
    )
    request = Request(url, headers={"User-Agent": "strike-desk-bar-continuation/0.1"})
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"yahoo nifty fetch failed: {exc}") from exc
    results = payload.get("chart", {}).get("result") or []
    if not results:
        error = payload.get("chart", {}).get("error")
        raise RuntimeError(f"yahoo nifty empty: {error}")
    result = results[0]
    stamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    tz_name = result.get("meta", {}).get("exchangeTimezoneName") or "Asia/Kolkata"
    zone = ZoneInfo(tz_name)
    rows: list[dict[str, Any]] = []
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    for i, stamp in enumerate(stamps):
        if i >= len(opens) or i >= len(highs) or i >= len(lows) or i >= len(closes):
            break
        if None in (opens[i], highs[i], lows[i], closes[i]):
            continue
        rows.append(
            {
                "timestamp": datetime.fromtimestamp(int(stamp), tz=zone),
                "open": opens[i],
                "high": highs[i],
                "low": lows[i],
                "close": closes[i],
            }
        )
    return parse_history_rows(rows)


def _yahoo_range(interval: str, range_hint: str | None) -> tuple[str, str]:
    if range_hint:
        return _yahoo_interval(interval), range_hint
    if interval in {"1m", "5m"}:
        return _yahoo_interval(interval), "60d"
    if interval in {"15m", "30m", "60m", "1h"}:
        return _yahoo_interval(interval), "60d"
    return "1d", "5y"


def _yahoo_interval(interval: str) -> str:
    mapping = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "60m": "60m",
        "1h": "60m",
        "D": "1d",
        "1d": "1d",
        "day": "1d",
    }
    return mapping.get(interval, "15m")


def _parse_ts(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        ts = raw
    elif isinstance(raw, (int, float)):
        stamp = float(raw)
        if stamp > 1e12:
            stamp /= 1000.0
        ts = datetime.fromtimestamp(stamp, tz=IST)
    elif isinstance(raw, str) and raw:
        text = raw.replace("Z", "+00:00")
        try:
            ts = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=IST)
    return ts.astimezone(IST)


def format_report(report: BacktestReport) -> str:
    pf = report.profit_factor
    pf_text = "n/a" if pf is None else ("inf" if pf == float("inf") else f"{pf:.2f}")
    longs = sum(1 for fill in report.fills if fill.side == "LONG")
    shorts = sum(1 for fill in report.fills if fill.side == "SHORT")
    avg_hold = (
        sum(fill.bars_held for fill in report.fills) / len(report.fills) if report.fills else 0.0
    )
    lines = [
        f"{report.symbol} {report.interval}  bars={report.bars}  signals={report.signals}  "
        f"trades={len(report.fills)}  wins={report.wins}  losses={report.losses}  "
        f"net={report.net_pts:.1f} pts  INR {report.net_inr:,.0f}/lot  PF={pf_text}",
        f"  long={longs}  short={shorts}  avg_hold={avg_hold:.1f} bars  "
        f"slip={SLIPPAGE_PTS:g}pt/side  commission=INR {COMMISSION_INR:g} RT  "
        f"target={TARGET_R:g}R  stop=setup extreme",
        "  gates: last-bar continuation bias, ADX>=20, DI aligned, "
        "RSI 50-70 long / 30-50 short, close vs EMA20",
    ]
    for fill in report.fills[:12]:
        lines.append(
            f"  {fill.entry_ts.strftime('%Y-%m-%d %H:%M')} {fill.side:<5} "
            f"{fill.entry:.2f}->{fill.exit:.2f}  {fill.points:+.1f}pt  "
            f"INR {fill.pnl_inr:+,.0f}  {fill.reason}"
        )
    if len(report.fills) > 12:
        lines.append(f"  … {len(report.fills) - 12} more fills")
    return "\n".join(lines)
