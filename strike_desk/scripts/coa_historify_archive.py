"""Archive today's cash-index ATM bands into Historify (1m).

Nifty, Banknifty and Sensex each keep their own front listed expiry.
Runs as a oneshot after the cash session. Never prints tokens.
CoA replay reads this tape later; expired contracts cannot be re-downloaded.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

_here = Path(__file__).resolve()
OA_ROOT = Path(os.environ.get("OPENALGO_ROOT") or "/opt/openalgo/current")
if not (OA_ROOT / "app.py").exists():
    try:
        candidate = _here.parents[2]
    except IndexError:
        candidate = OA_ROOT
    if (candidate / "app.py").exists():
        OA_ROOT = candidate
DESK_SRC = Path(os.environ.get("STRIKE_DESK_SRC", str(OA_ROOT / "strike_desk" / "src")))
# Live OpenAlgo first so database.auth_db is not the Strike Desk tree copy.
sys.path.insert(0, str(_here.parent))
sys.path.insert(0, str(DESK_SRC))
sys.path.insert(0, str(OA_ROOT))

from dotenv import load_dotenv

load_dotenv(OA_ROOT / ".env")

from strike_desk.coa_archive import LOOKBACK_DAYS, STRIKE_PAD, lookback_window, weeklies_to_archive  # noqa: E402
from strike_desk.coa_boards import CASH_BOARDS, CoaBoard  # noqa: E402
from strike_desk.config import IST  # noqa: E402

import coa_replay_host as replay  # noqa: E402


def archive_board(
    board: CoaBoard,
    *,
    start: date,
    end: date,
    strikes: int,
    api_key: str | None,
    feed: replay.InternalHistoryFeed,
    force: bool,
) -> tuple[int, int, int]:
    """Return (ok_count, skip_count, record_count). Raises nothing for a dead board."""
    labels = replay.expiry_labels(board.index_symbol, board.option_exchange)
    listed = [day for day, _label in labels]
    print(
        f"{board.slug} listed expiries:",
        ", ".join(f"{day.isoformat()} ({label})" for day, label in labels[:8]) or "(none)",
    )
    keep_days = weeklies_to_archive(end, listed)
    keep = [(day, label) for day, label in labels if day in set(keep_days)]
    if not keep:
        print(f"{board.slug}: no listed front expiry to archive", file=sys.stderr)
        return 0, 0, 0
    print(f"{board.slug} keeping", ", ".join(f"{day.isoformat()} ({label})" for day, label in keep))

    status = replay.seed_symbol(
        board.index_symbol,
        board.index_spot_exchange,
        start,
        end,
        api_key=api_key,
        feed=feed,
        force=force,
    )
    print(
        f"  {board.index_symbol} {board.index_spot_exchange} "
        f"{status[0]} records={status[1]} {status[2]}".rstrip()
    )
    if status[0] == "error":
        print(f"{board.slug} spot 1m download failed: {status[2]}", file=sys.stderr)
        return 0, 0, 0

    band = replay._spot_band(
        start,
        end,
        strikes,
        symbol=board.index_symbol,
        exchange=board.index_spot_exchange,
        step=board.strike_step,
    )
    if band is None:
        print(f"{board.slug}: empty spot tape after download", file=sys.stderr)
        return 0, 0, 0
    lo, hi = band
    print(f"{board.slug} strike band {lo:.0f}-{hi:.0f}")
    wanted: list[tuple[str, str]] = [(board.index_symbol, board.index_spot_exchange)]
    for day, label in keep:
        ok, payload, _code = replay.get_option_chain_symbols(
            underlying=board.index_symbol,
            exchange=board.option_exchange,
            expiry=label,
            strike_min=lo,
            strike_max=hi,
        )
        chain = payload.get("data") if ok else []
        print(
            f"  {board.slug} expiry {day.isoformat()} {label}: "
            f"{len(chain) if isinstance(chain, list) else 0} contracts"
        )
        if not isinstance(chain, list):
            continue
        for row in chain:
            symbol = str(row.get("symbol") or "")
            exchange = str(row.get("exchange") or board.option_exchange)
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
        kind, count, message = replay.seed_symbol(
            symbol, exchange, start, end, api_key=api_key, feed=feed, force=force
        )
        counts[kind] = counts.get(kind, 0) + 1
        records += count
        extra = f" {message}" if message else ""
        print(f"  [{board.slug} {index}/{len(unique)}] {symbol} {kind} records={count}{extra}")
    print(
        f"{board.slug} done symbols={len(unique)} ok={counts['ok']} skip={counts['skip']} "
        f"empty={counts['empty']} error={counts['error']} records={records}"
    )
    return counts["ok"], counts["skip"], records


def archive_session(*, start: date, end: date, strikes: int, force: bool) -> int:
    token, feed_token, broker = replay._active_session()
    feed = replay.InternalHistoryFeed(token, feed_token, broker)
    api_key = replay.get_first_available_api_key()
    print(
        f"archive 1m {start.isoformat()} -> {end.isoformat()} ATM+/-{strikes} "
        f"boards={','.join(board.slug for board in CASH_BOARDS)}"
    )
    ok_total = 0
    skip_total = 0
    record_total = 0
    for board in CASH_BOARDS:
        ok_count, skip_count, records = archive_board(
            board,
            start=start,
            end=end,
            strikes=strikes,
            api_key=api_key,
            feed=feed,
            force=force,
        )
        ok_total += ok_count
        skip_total += skip_count
        record_total += records
    print(
        f"archive done boards={len(CASH_BOARDS)} ok={ok_total} skip={skip_total} "
        f"records={record_total}"
    )
    if ok_total == 0 and skip_total == 0:
        print("no 1m bars landed in Historify", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    today = datetime.now(tz=IST).date()
    default_start, default_end = lookback_window(today, LOOKBACK_DAYS)
    parser = argparse.ArgumentParser(
        description="Archive Nifty/Banknifty/Sensex ATM 1m into Historify"
    )
    parser.add_argument("--start", default=default_start.isoformat())
    parser.add_argument("--end", default=default_end.isoformat())
    parser.add_argument("--strikes", type=int, default=STRIKE_PAD)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even when catalog already covers a past day",
    )
    args = parser.parse_args()
    return archive_session(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        strikes=args.strikes,
        force=args.force,
    )


if __name__ == "__main__":
    raise SystemExit(main())
