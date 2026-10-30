"""Cash-index CoA boards. Nifty, Banknifty and Sensex each keep their own map.

Tradetron Hands flags are per board. Never copy Nifty ltp_buy_* onto Banknifty
or Sensex — those sets stay idle until that board's own CoA posts.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta

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


@dataclass(frozen=True)
class CoaBoard:
    slug: str
    index_name: str
    index_symbol: str
    index_spot_exchange: str
    option_exchange: str
    strike_step: float
    extension_pts: float
    spot_sl_buffer: float
    reclaim_trail: float
    flag_suffix: str
    expiry_kind: str
    expiry_weekday: int
    state_file: str
    brief_file: str
    always_open: bool = False
    analyze: bool = True
    flatten_at: time | None = time(15, 15)
    entry_open: time = time(9, 20)
    entry_close: time = time(14, 0)
    session: str = "cash"
    # OpenAlgo MCX master often stores lotsize=1. Units per lot when that happens.
    contract_qty: int | None = None

    def order_quantity(self, chain_lot: int, lots: int = 1) -> int:
        """Units for one CoA fill. Never trust MCX lotsize=1 from the master."""
        unit = int(chain_lot or 0)
        if unit <= 1 and self.contract_qty:
            unit = self.contract_qty
        if unit <= 0:
            return 0
        return unit * max(int(lots), 1)


# Spot buffers scale with strike step (Nifty 50 → 20/15/10). Premium TSL stays %.
NIFTY = CoaBoard(
    slug="nifty",
    index_name="NIFTY",
    index_symbol="NIFTY",
    index_spot_exchange="NSE_INDEX",
    option_exchange="NFO",
    strike_step=50.0,
    extension_pts=20.0,
    spot_sl_buffer=15.0,
    reclaim_trail=10.0,
    flag_suffix="",
    expiry_kind="weekly",
    expiry_weekday=1,  # Tuesday
    state_file="coa_live.json",
    brief_file="public-brief.json",
)

BANKNIFTY = CoaBoard(
    slug="banknifty",
    index_name="BANKNIFTY",
    index_symbol="BANKNIFTY",
    index_spot_exchange="NSE_INDEX",
    option_exchange="NFO",
    strike_step=100.0,
    extension_pts=40.0,
    spot_sl_buffer=30.0,
    reclaim_trail=20.0,
    flag_suffix="_bn",
    expiry_kind="monthly",
    expiry_weekday=1,  # last Tuesday; holidays walk back (Hands Current Month)
    state_file="coa_live_bn.json",
    brief_file="public-brief-banknifty.json",
)

SENSEX = CoaBoard(
    slug="sensex",
    index_name="SENSEX",
    index_symbol="SENSEX",
    index_spot_exchange="BSE_INDEX",
    option_exchange="BFO",
    strike_step=100.0,
    extension_pts=40.0,
    spot_sl_buffer=30.0,
    reclaim_trail=20.0,
    flag_suffix="_sx",
    expiry_kind="weekly",
    expiry_weekday=3,  # Thursday
    state_file="coa_live_sx.json",
    brief_file="public-brief-sensex.json",
)

BTC = CoaBoard(
    slug="btc-options",
    index_name="BTC",
    index_symbol="BTC",
    index_spot_exchange="CRYPTO",
    option_exchange="CRYPTO",
    strike_step=200.0,
    extension_pts=200.0,
    spot_sl_buffer=150.0,
    reclaim_trail=100.0,
    flag_suffix="",
    expiry_kind="daily",
    expiry_weekday=0,
    state_file="coa_live_btc.json",
    brief_file="public-brief-btc-options.json",
    always_open=True,
    analyze=False,
    flatten_at=None,
    entry_open=time(0, 0),
    entry_close=time(23, 59),
    session="crypto",
)

CRUDEOIL = CoaBoard(
    slug="crudeoil",
    index_name="CRUDEOIL",
    index_symbol="CRUDEOIL",
    index_spot_exchange="MCX",
    option_exchange="MCX",
    strike_step=50.0,
    extension_pts=20.0,
    spot_sl_buffer=15.0,
    reclaim_trail=10.0,
    flag_suffix="_cl",
    expiry_kind="monthly",
    expiry_weekday=2,
    state_file="coa_live_cl.json",
    brief_file="public-brief-crudeoil.json",
    flatten_at=time(23, 15),
    entry_open=time(9, 20),
    entry_close=time(22, 0),
    session="mcx",
    contract_qty=100,
)

NATGAS = CoaBoard(
    slug="natgas",
    index_name="NATGAS",
    index_symbol="NATURALGAS",
    index_spot_exchange="MCX",
    option_exchange="MCX",
    strike_step=5.0,
    extension_pts=2.0,
    spot_sl_buffer=1.5,
    reclaim_trail=1.0,
    flag_suffix="_ng",
    expiry_kind="monthly",
    expiry_weekday=4,
    state_file="coa_live_ng.json",
    brief_file="public-brief-natgas.json",
    flatten_at=time(23, 15),
    entry_open=time(9, 20),
    entry_close=time(22, 0),
    session="mcx",
    contract_qty=1250,
)

CASH_BOARDS: tuple[CoaBoard, ...] = (NIFTY, BANKNIFTY, SENSEX)
MCX_BOARDS: tuple[CoaBoard, ...] = (CRUDEOIL, NATGAS)
SESSION_BOARDS: tuple[CoaBoard, ...] = (*CASH_BOARDS, *MCX_BOARDS)
LIVE_BOARDS: tuple[CoaBoard, ...] = (*SESSION_BOARDS, BTC)
BOARDS_BY_SLUG: dict[str, CoaBoard] = {board.slug: board for board in LIVE_BOARDS}


def parse_expiry_label(raw: str) -> date | None:
    """Parse OpenAlgo labels such as ``23-NOV-26`` or ``23NOV26``."""
    compact = str(raw).strip().upper().replace(" ", "").replace("-", "")
    if len(compact) < 7 or not compact[:2].isdigit() or not compact[5:7].isdigit():
        return None
    month = _MONTHS.get(compact[2:5])
    if month is None:
        return None
    try:
        return date(2000 + int(compact[5:7]), month, int(compact[:2]))
    except ValueError:
        return None


def pick_listed_expiry(
    day: date,
    labels: Sequence[str],
    *,
    kind: str = "weekly",
) -> date | None:
    """Front listed contract on or after ``day``.

    Weekly (Nifty, Sensex) = nearest listed date. Monthly (Bank Nifty) = last
    listed date in that month, so a weekly listing cannot steal Current Month.
    """
    parsed = [got for raw in labels if (got := parse_expiry_label(str(raw)))]
    upcoming = sorted(item for item in parsed if item >= day)
    if not upcoming:
        return None
    if kind == "monthly":
        last_in_month: dict[tuple[int, int], date] = {}
        for item in upcoming:
            last_in_month[(item.year, item.month)] = item
        monthlies = sorted(last_in_month.values())
        return monthlies[0] if monthlies else None
    return upcoming[0]


def expiry_date_for_board(
    day: date,
    board: CoaBoard,
    is_trading_day: Callable[[date], bool],
) -> date | None:
    """Weekly = next expiry weekday; monthly = last that weekday of the month.

    Daily (BTC) is the calendar day itself — crypto does not close on weekends.
    """
    if board.expiry_kind == "daily":
        return day
    if board.expiry_kind == "monthly":
        candidate = _last_weekday(day, board.expiry_weekday)
        if candidate < day:
            nxt = date(
                day.year + (1 if day.month == 12 else 0),
                1 if day.month == 12 else day.month + 1,
                1,
            )
            candidate = _last_weekday(nxt, board.expiry_weekday)
    else:
        offset = (board.expiry_weekday - day.weekday()) % 7
        candidate = day + timedelta(days=offset)
    for _ in range(10):
        if is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    return None


def _last_weekday(day: date, weekday: int) -> date:
    if day.month == 12:
        nxt = date(day.year + 1, 1, 1)
    else:
        nxt = date(day.year, day.month + 1, 1)
    last = nxt - timedelta(days=1)
    while last.weekday() != weekday:
        last -= timedelta(days=1)
    return last
