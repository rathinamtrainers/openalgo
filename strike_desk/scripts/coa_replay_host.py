"""Run CoA 5m replay using this machine's OpenAlgo broker session.

Uses the logged-in broker token from openalgo.db so we do not depend on
STRIKE_DESK_OPENALGO_API_KEY matching the HTTP API. Never prints tokens.

Historify stores 1m (and daily). 5m CoA bars are aggregated from that 1m tape.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

_here = Path(__file__).resolve()
try:
    OA_ROOT = _here.parents[2]
except IndexError:
    OA_ROOT = Path("/opt/openalgo/current")
if not (OA_ROOT / "app.py").exists():
    OA_ROOT = Path(os.environ.get("OPENALGO_ROOT", "/opt/openalgo/current"))
DESK_SRC = Path(os.environ.get("STRIKE_DESK_SRC", OA_ROOT / "strike_desk" / "src"))
sys.path.insert(0, str(DESK_SRC))
sys.path.insert(0, str(OA_ROOT))

from dotenv import load_dotenv

load_dotenv(OA_ROOT / ".env")

from database.auth_db import Auth, get_auth_token, get_feed_token, get_first_available_api_key  # noqa: E402
from database.historify_db import get_data_range, get_ohlcv, upsert_market_data  # noqa: E402
from database.symbol import SymToken  # noqa: E402
from services.historify_service import download_data, get_option_chain_symbols  # noqa: E402
from services.history_service import get_history  # noqa: E402
from strike_desk.config import IST  # noqa: E402
from strike_desk.ltp_coa_replay import (  # noqa: E402
    NIFTY_STRIKE_STEP,
    build_tape,
    default_weekly_expiry,
    format_report,
    replay,
)

_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def _parse_expiry_label(raw: str) -> date | None:
    text = str(raw or "").strip().upper().replace(" ", "")
    text = text.replace("/", "-")
    parts = text.split("-")
    if len(parts) != 3:
        return None
    try:
        day_n = int(parts[0])
        month = _MONTHS.get(parts[1][:3])
        year_n = int(parts[2])
    except ValueError:
        return None
    if month is None:
        return None
    if year_n < 100:
        year_n += 2000
    try:
        return date(year_n, month, day_n)
    except ValueError:
        return None


def nifty_expiry_labels() -> list[tuple[date, str]]:
    rows = (
        SymToken.query.filter(SymToken.exchange == "NFO")
        .filter(SymToken.symbol.like("NIFTY%CE"))
        .with_entities(SymToken.expiry)
        .distinct()
        .all()
    )
    found: dict[date, str] = {}
    for (raw,) in rows:
        parsed = _parse_expiry_label(str(raw))
        if parsed is not None:
            found.setdefault(parsed, str(raw))
    return sorted(found.items(), key=lambda item: item[0])


def nifty_expiries_from_master() -> list[date]:
    return [day for day, _label in nifty_expiry_labels()]


def expiry_for_factory(listed: list[date]):
    def expiry_for(day: date) -> date:
        upcoming = [exp for exp in listed if exp >= day]
        if not upcoming:
            return default_weekly_expiry(day, weekday=1)
        chosen = upcoming[0]
        # Delisted previous week: the next listed contract can sit >7 days out.
        if (chosen - day).days > 6:
            return default_weekly_expiry(day, weekday=1)
        return chosen

    return expiry_for


class InternalHistoryFeed:
    def __init__(self, auth_token: str, feed_token: str | None, broker: str) -> None:
        self._auth = auth_token
        self._feed = feed_token
        self._broker = broker

    def history(self, symbol, exchange, interval, start_date, end_date, source="api"):
        start = start_date if isinstance(start_date, str) else start_date.isoformat()
        end = end_date if isinstance(end_date, str) else end_date.isoformat()
        ok, payload, _code = get_history(
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            start_date=start,
            end_date=end,
            auth_token=self._auth,
            feed_token=self._feed,
            broker=self._broker,
            source=source,
        )
        if not ok:
            return []
        data = payload.get("data")
        if isinstance(data, dict):
            data = data.get("candles") or data.get("data") or []
        return [row for row in (data or []) if isinstance(row, dict)]


def _active_session() -> tuple[str, str | None, str]:
    row = Auth.query.filter_by(is_revoked=False).first()
    if row is None or not row.name or not row.broker:
        raise SystemExit("no active broker session in openalgo.db")
    token = get_auth_token(row.name)
    if not token:
        raise SystemExit("broker session has no auth token")
    print(f"broker session: {row.broker}")
    return token, get_feed_token(row.name), row.broker


def _epoch_range(start: date, end: date) -> tuple[int, int]:
    start_ts = int(datetime.combine(start, time.min, tzinfo=IST).timestamp())
    end_ts = int(datetime.combine(end, time.max, tzinfo=IST).timestamp())
    return start_ts, end_ts


def _already_seeded(symbol: str, exchange: str, start: date, end: date) -> bool:
    info = get_data_range(symbol, exchange, "1m")
    if not info or not info.get("first_timestamp") or not info.get("last_timestamp"):
        return False
    first = datetime.fromtimestamp(int(info["first_timestamp"]), tz=IST).date()
    last = datetime.fromtimestamp(int(info["last_timestamp"]), tz=IST).date()
    today = datetime.now(tz=IST).date()
    if end >= today:
        return False
    return first <= start and last >= end


def _store_history_rows(rows: list[dict], symbol: str, exchange: str) -> int:
    import pandas as pd

    if not rows:
        return 0
    frame = pd.DataFrame(rows)
    if "time" in frame.columns and "timestamp" not in frame.columns:
        frame["timestamp"] = frame["time"]
    return upsert_market_data(frame, symbol, exchange, "1m")


def seed_symbol(
    symbol: str,
    exchange: str,
    start: date,
    end: date,
    *,
    api_key: str | None,
    feed: InternalHistoryFeed,
    force: bool,
) -> tuple[str, int, str]:
    if not force and _already_seeded(symbol, exchange, start, end):
        return "skip", 0, "already in Historify"
    start_s, end_s = start.isoformat(), end.isoformat()
    if api_key:
        ok, payload, _code = download_data(symbol, exchange, "1m", start_s, end_s, api_key)
        if ok:
            return "ok", int(payload.get("records") or 0), ""
        message = str(payload.get("message") or "download_data failed")
        return "error", 0, message
    rows = feed.history(symbol, exchange, "1m", start, end, "api")
    stored = _store_history_rows(rows, symbol, exchange)
    if stored:
        return "ok", stored, ""
    return "empty", 0, "no 1m bars"


def _spot_band(start: date, end: date, pad_strikes: int) -> tuple[float, float]:
    start_ts, end_ts = _epoch_range(start, end)
    frame = get_ohlcv("NIFTY", "NSE_INDEX", "1m", start_ts, end_ts)
    if frame is None or getattr(frame, "empty", True):
        return 22000.0, 24000.0
    step = NIFTY_STRIKE_STEP
    pad = pad_strikes * step
    low = round(float(frame["close"].min()) / step) * step
    high = round(float(frame["close"].max()) / step) * step
    return low - pad, high + pad


def seed_historify(
    *,
    start: date,
    end: date,
    strikes: int,
    feed: InternalHistoryFeed,
    force: bool,
) -> int:
    api_key = get_first_available_api_key()
    print(
        "Historify seed 1m "
        f"{start.isoformat()} -> {end.isoformat()} "
        f"via={'download_data' if api_key else 'broker+upsert'}"
    )
    labels = nifty_expiry_labels()
    near = [(day, label) for day, label in labels if day <= end + timedelta(days=21)]
    if not near and labels:
        near = labels[:1]
    print(
        "listed NIFTY expiries:",
        ", ".join(f"{day.isoformat()} ({label})" for day, label in labels[:8]) or "(none)",
    )

    wanted: list[tuple[str, str]] = [("NIFTY", "NSE_INDEX")]
    status = seed_symbol("NIFTY", "NSE_INDEX", start, end, api_key=api_key, feed=feed, force=force)
    print(f"  NIFTY NSE_INDEX {status[0]} records={status[1]} {status[2]}".rstrip())
    if status[0] == "error":
        print(f"spot 1m download failed: {status[2]}", file=sys.stderr)
        return 1

    lo, hi = _spot_band(start, end, strikes)
    print(f"strike band {lo:.0f}-{hi:.0f} (spot range plus ATM+/-{strikes})")
    for day, label in near:
        ok, payload, _code = get_option_chain_symbols(
            underlying="NIFTY",
            exchange="NFO",
            expiry=label,
            strike_min=lo,
            strike_max=hi,
        )
        chain = payload.get("data") if ok else []
        print(
            f"  expiry {day.isoformat()} {label}: "
            f"{len(chain) if isinstance(chain, list) else 0} contracts"
        )
        if not isinstance(chain, list):
            continue
        for row in chain:
            symbol = str(row.get("symbol") or "")
            exchange = str(row.get("exchange") or "NFO")
            if symbol:
                wanted.append((symbol, exchange))

    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for item in wanted:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)

    counts = {"ok": 0, "skip": 0, "empty": 0, "error": 0}
    records = 0
    for index, (symbol, exchange) in enumerate(unique, start=1):
        kind, count, message = seed_symbol(
            symbol, exchange, start, end, api_key=api_key, feed=feed, force=force
        )
        counts[kind] = counts.get(kind, 0) + 1
        records += count
        extra = f" {message}" if message else ""
        print(f"  [{index}/{len(unique)}] {symbol} {kind} records={count}{extra}")
    print(
        f"Historify seed done symbols={len(unique)} ok={counts['ok']} skip={counts['skip']} "
        f"empty={counts['empty']} error={counts['error']} records={records}"
    )
    if counts["ok"] == 0 and counts["skip"] == 0:
        print("no 1m bars landed in Historify", file=sys.stderr)
        return 2
    return 0


def _run_replay(
    *,
    start: date,
    end: date,
    strikes: int,
    source: str,
    feed: InternalHistoryFeed,
    listed: list[date],
    replay_expiry: date | None,
    label: str,
) -> int:
    if replay_expiry is not None:
        expiry_for = lambda _day, chosen=replay_expiry: chosen
        print(f"CoA 5m {label} forced expiry {replay_expiry.isoformat()} source={source}")
    else:
        expiry_for = expiry_for_factory(listed)
        print(f"CoA 5m {label} front-weekly source={source}")
    print(f"window {start.isoformat()} -> {end.isoformat()} strikes=ATM+/-{strikes}")
    tape = build_tape(
        feed,
        start,
        end,
        strike_count=strikes,
        source=source,
        expiry_for=expiry_for,
    )
    report = replay(tape)
    print(format_report(report))
    if not tape:
        print("no 5m bars in the window", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    today = datetime.now(tz=IST).date()
    parser = argparse.ArgumentParser(description="CoA 5m replay via host OpenAlgo session")
    parser.add_argument("--start", default=(today - timedelta(days=14)).isoformat())
    parser.add_argument("--end", default=today.isoformat())
    parser.add_argument("--strikes", type=int, default=10)
    parser.add_argument("--source", default="api", choices=("api", "db"))
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Download 1m NIFTY + listed option band into Historify, then replay from db",
    )
    parser.add_argument("--seed-only", action="store_true", help="Seed Historify and exit")
    parser.add_argument(
        "--force-seed",
        action="store_true",
        help="Re-download even when catalog already covers the window",
    )
    parser.add_argument(
        "--replay-expiry",
        default="",
        help="Force one listed expiry YYYY-MM-DD (monthly chain replay)",
    )
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    replay_expiry = date.fromisoformat(args.replay_expiry) if args.replay_expiry else None

    token, feed_token, broker = _active_session()
    feed_client = InternalHistoryFeed(token, feed_token, broker)
    listed = nifty_expiries_from_master()
    print(
        "listed NIFTY expiries:",
        ", ".join(exp.isoformat() for exp in listed[:8]) or "(none)",
    )

    if args.seed or args.seed_only:
        seeded = seed_historify(
            start=start,
            end=end,
            strikes=args.strikes,
            feed=feed_client,
            force=args.force_seed,
        )
        if seeded != 0:
            return seeded
        if args.seed_only:
            return 0
        args.source = "db"

    weekly = _run_replay(
        start=start,
        end=end,
        strikes=args.strikes,
        source=args.source,
        feed=feed_client,
        listed=listed,
        replay_expiry=replay_expiry,
        label="NIFTY",
    )
    if replay_expiry is not None or weekly != 0:
        return weekly

    # Second labeled pass on the next listed expiry (usually monthly). Delisted
    # weeklies are gone from master; this is not front-weekly CoA.
    if args.source == "db" and len(listed) >= 2 and start < listed[0]:
        print()
        print(
            f"second pass: listed chain {listed[1].isoformat()} "
            "(not front weekly — delisted weeklies are gone from master)"
        )
        return _run_replay(
            start=start,
            end=end,
            strikes=args.strikes,
            source=args.source,
            feed=feed_client,
            listed=listed,
            replay_expiry=listed[1],
            label="NIFTY listed-chain",
        )
    return weekly


if __name__ == "__main__":
    raise SystemExit(main())
