"""OFA — Order Flow Analysis mind.

Independent reader. Not CoA, not QuadShield, not Tradetron Hands.

Consumes a depth snapshot plus the last print. Classifies hit-bid vs lift-ask,
tracks session CVD, book walls, stack/pull, and a conservative LONG/SHORT/FLAT
flag set (``of_long`` / ``of_short`` / ``of_exit``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

PrintSide = Literal["HIT_BID", "LIFT_ASK", "UNKNOWN"]
BookSide = Literal["BID", "ASK"]
Signal = Literal["LONG", "SHORT", "FLAT"]
OiSense = Literal["LONG_BUILD", "SHORT_BUILD", "SHORT_COVER", "LONG_UNWIND", "FLAT"]

NIFTY_TICK = 0.05
IMBALANCE_RATIO = 3.0
WALL_MULT = 3.0
THIN_TOTAL = 80.0
WIDE_SPREAD_TICKS = 4.0
PULL_FRACTION = 0.5


@dataclass(frozen=True)
class BookLevel:
    price: float
    quantity: float


@dataclass(frozen=True)
class Book:
    symbol: str
    exchange: str
    ltp: float
    ltq: float
    volume: float
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    total_buy: float
    total_sell: float
    oi: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None


@dataclass(frozen=True)
class Wall:
    side: BookSide
    price: float
    quantity: float
    vs_median: float


@dataclass(frozen=True)
class BookMove:
    side: BookSide
    price: float
    prev_qty: float
    qty: float

    @property
    def delta(self) -> float:
        return self.qty - self.prev_qty


@dataclass
class SessionState:
    day: str = ""
    cvd: float = 0.0
    buy_vol: float = 0.0
    sell_vol: float = 0.0
    last_volume: float = 0.0
    last_ltp: float = 0.0
    last_ltq: float = 0.0
    open_ltp: float = 0.0
    open_oi: float | None = None
    last_oi: float | None = None
    last_signal: Signal = "FLAT"
    prev_bids: list[list[float]] = field(default_factory=list)
    prev_asks: list[list[float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "cvd": self.cvd,
            "buy_vol": self.buy_vol,
            "sell_vol": self.sell_vol,
            "last_volume": self.last_volume,
            "last_ltp": self.last_ltp,
            "last_ltq": self.last_ltq,
            "open_ltp": self.open_ltp,
            "open_oi": self.open_oi,
            "last_oi": self.last_oi,
            "last_signal": self.last_signal,
            "prev_bids": self.prev_bids,
            "prev_asks": self.prev_asks,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> SessionState:
        if not raw:
            return cls()
        return cls(
            day=str(raw.get("day") or ""),
            cvd=float(raw.get("cvd") or 0),
            buy_vol=float(raw.get("buy_vol") or 0),
            sell_vol=float(raw.get("sell_vol") or 0),
            last_volume=float(raw.get("last_volume") or 0),
            last_ltp=float(raw.get("last_ltp") or 0),
            last_ltq=float(raw.get("last_ltq") or 0),
            open_ltp=float(raw.get("open_ltp") or 0),
            open_oi=_opt_float(raw.get("open_oi")),
            last_oi=_opt_float(raw.get("last_oi")),
            last_signal=_as_signal(raw.get("last_signal")),
            prev_bids=_pairs(raw.get("prev_bids")),
            prev_asks=_pairs(raw.get("prev_asks")),
        )


@dataclass(frozen=True)
class OfaSnapshot:
    ts: datetime
    symbol: str
    exchange: str
    ltp: float
    best_bid: float | None
    best_ask: float | None
    spread_ticks: float | None
    last_side: PrintSide
    last_qty: float
    session_cvd: float
    buy_vol: float
    sell_vol: float
    bid_wall: Wall | None
    ask_wall: Wall | None
    stacks: tuple[BookMove, ...]
    pulls: tuple[BookMove, ...]
    book_thin: bool
    spoof_suspect: bool
    oi_sense: OiSense | None
    signal: Signal
    of_long: int
    of_short: int
    of_exit: int
    reason: str


def classify_print(price: float, bid: float | None, ask: float | None) -> PrintSide:
    if price <= 0 or bid is None or ask is None or bid <= 0 or ask <= 0:
        return "UNKNOWN"
    if price >= ask:
        return "LIFT_ASK"
    if price <= bid:
        return "HIT_BID"
    mid = (bid + ask) / 2.0
    return "LIFT_ASK" if price >= mid else "HIT_BID"


def best_bid(book: Book) -> float | None:
    return book.bids[0].price if book.bids else None


def best_ask(book: Book) -> float | None:
    return book.asks[0].price if book.asks else None


def spread_ticks(book: Book, tick: float = NIFTY_TICK) -> float | None:
    bid, ask = best_bid(book), best_ask(book)
    if bid is None or ask is None or tick <= 0:
        return None
    return (ask - bid) / tick


def largest_wall(levels: tuple[BookLevel, ...], side: BookSide) -> Wall | None:
    qtys = [level.quantity for level in levels if level.quantity > 0]
    if not qtys:
        return None
    ordered = sorted(qtys)
    median = ordered[len(ordered) // 2]
    top = max(levels, key=lambda level: level.quantity)
    vs = top.quantity / median if median > 0 else 0.0
    return Wall(side=side, price=top.price, quantity=top.quantity, vs_median=vs)


def book_moves(
    prev: list[list[float]],
    current: tuple[BookLevel, ...],
    side: BookSide,
) -> tuple[list[BookMove], list[BookMove]]:
    before = {round(price, 4): qty for price, qty in prev}
    after = {round(level.price, 4): level.quantity for level in current}
    stacks: list[BookMove] = []
    pulls: list[BookMove] = []
    for price in set(before) | set(after):
        old = before.get(price, 0.0)
        new = after.get(price, 0.0)
        if new > old:
            stacks.append(BookMove(side, price, old, new))
        elif new < old:
            pulls.append(BookMove(side, price, old, new))
    stacks.sort(key=lambda move: move.delta, reverse=True)
    pulls.sort(key=lambda move: move.delta)
    return stacks, pulls


def is_thin(book: Book, thin_total: float = THIN_TOTAL) -> bool:
    if len(book.bids) < 3 or len(book.asks) < 3:
        return True
    resting = book.total_buy + book.total_sell
    if resting <= 0:
        resting = sum(level.quantity for level in book.bids) + sum(
            level.quantity for level in book.asks
        )
    return resting < thin_total


def oi_sense(price_change: float, oi_change: float | None) -> OiSense | None:
    if oi_change is None:
        return None
    if price_change > 0 and oi_change > 0:
        return "LONG_BUILD"
    if price_change < 0 and oi_change > 0:
        return "SHORT_BUILD"
    if price_change > 0 and oi_change < 0:
        return "SHORT_COVER"
    if price_change < 0 and oi_change < 0:
        return "LONG_UNWIND"
    return "FLAT"


def parse_depth(data: dict[str, Any], *, symbol: str, exchange: str) -> Book:
    bids = _levels(data.get("bids"))
    asks = _levels(data.get("asks"))
    return Book(
        symbol=symbol,
        exchange=exchange,
        ltp=_num(data.get("ltp")),
        ltq=_num(data.get("ltq")),
        volume=_num(data.get("volume")),
        bids=tuple(bids),
        asks=tuple(asks),
        total_buy=_num(data.get("totalbuyqty")),
        total_sell=_num(data.get("totalsellqty")),
        oi=_opt_float(data.get("oi")),
        open=_opt_float(data.get("open")),
        high=_opt_float(data.get("high")),
        low=_opt_float(data.get("low")),
    )


def monthly_fut_symbol(underlying: str, day: date, weekday: int = 1) -> str:
    """Last ``weekday`` of the month covering ``day`` (NIFTY monthly = Tuesday)."""
    expiry = _last_weekday(day, weekday)
    if day > expiry:
        month = 1 if day.month == 12 else day.month + 1
        year = day.year + (1 if day.month == 12 else 0)
        expiry = _last_weekday(date(year, month, 1), weekday)
    return f"{underlying}{expiry.strftime('%d%b%y').upper()}FUT"


def evaluate(
    book: Book,
    state: SessionState,
    *,
    now: datetime,
    tick: float = NIFTY_TICK,
    wall_mult: float = WALL_MULT,
    thin_total: float = THIN_TOTAL,
    wide_spread_ticks: float = WIDE_SPREAD_TICKS,
) -> tuple[OfaSnapshot, SessionState]:
    day = now.date().isoformat()
    if state.day != day:
        state = SessionState(
            day=day,
            open_ltp=book.ltp,
            open_oi=book.oi,
            last_volume=book.volume,
        )

    bid, ask = best_bid(book), best_ask(book)
    last_side = classify_print(book.ltp, bid, ask)
    ticks = spread_ticks(book, tick)
    bid_wall = largest_wall(book.bids, "BID")
    ask_wall = largest_wall(book.asks, "ASK")
    bid_stacks, bid_pulls = book_moves(state.prev_bids, book.bids, "BID")
    ask_stacks, ask_pulls = book_moves(state.prev_asks, book.asks, "ASK")
    stacks = tuple(sorted(bid_stacks + ask_stacks, key=lambda move: move.delta, reverse=True)[:6])
    pulls = tuple(sorted(bid_pulls + ask_pulls, key=lambda move: move.delta)[:6])
    thin = is_thin(book, thin_total=thin_total)
    spoof = _spoof_suspect(ask_wall, bid_wall, pulls, wall_mult=wall_mult)

    printed = book.volume > 0 and book.volume != state.last_volume and book.ltq > 0
    buy_vol = state.buy_vol
    sell_vol = state.sell_vol
    cvd = state.cvd
    if printed:
        if last_side == "LIFT_ASK":
            buy_vol += book.ltq
            cvd += book.ltq
        elif last_side == "HIT_BID":
            sell_vol += book.ltq
            cvd -= book.ltq

    price_chg = book.ltp - state.open_ltp if state.open_ltp else 0.0
    oi_chg = None
    if book.oi is not None and state.open_oi is not None:
        oi_chg = book.oi - state.open_oi
    sense = oi_sense(price_chg, oi_chg)

    signal, reason = decide_signal(
        last_side=last_side,
        cvd=cvd,
        thin=thin,
        spread_ticks=ticks,
        spoof=spoof,
        bid_wall=bid_wall,
        ask_wall=ask_wall,
        pulls=pulls,
        wide_spread_ticks=wide_spread_ticks,
        wall_mult=wall_mult,
    )
    of_exit = 1 if state.last_signal in {"LONG", "SHORT"} and signal != state.last_signal else 0
    snap = OfaSnapshot(
        ts=now,
        symbol=book.symbol,
        exchange=book.exchange,
        ltp=book.ltp,
        best_bid=bid,
        best_ask=ask,
        spread_ticks=ticks,
        last_side=last_side,
        last_qty=book.ltq if printed else 0.0,
        session_cvd=cvd,
        buy_vol=buy_vol,
        sell_vol=sell_vol,
        bid_wall=bid_wall,
        ask_wall=ask_wall,
        stacks=stacks,
        pulls=pulls,
        book_thin=thin,
        spoof_suspect=spoof,
        oi_sense=sense,
        signal=signal,
        of_long=1 if signal == "LONG" else 0,
        of_short=1 if signal == "SHORT" else 0,
        of_exit=of_exit,
        reason=reason,
    )
    nxt = SessionState(
        day=day,
        cvd=cvd,
        buy_vol=buy_vol,
        sell_vol=sell_vol,
        last_volume=book.volume or state.last_volume,
        last_ltp=book.ltp,
        last_ltq=book.ltq,
        open_ltp=state.open_ltp or book.ltp,
        open_oi=state.open_oi if state.open_oi is not None else book.oi,
        last_oi=book.oi,
        last_signal=signal,
        prev_bids=[[level.price, level.quantity] for level in book.bids],
        prev_asks=[[level.price, level.quantity] for level in book.asks],
    )
    return snap, nxt


def decide_signal(
    *,
    last_side: PrintSide,
    cvd: float,
    thin: bool,
    spread_ticks: float | None,
    spoof: bool,
    bid_wall: Wall | None,
    ask_wall: Wall | None,
    pulls: tuple[BookMove, ...] | list[BookMove],
    wide_spread_ticks: float = WIDE_SPREAD_TICKS,
    wall_mult: float = WALL_MULT,
) -> tuple[Signal, str]:
    if thin:
        return "FLAT", "thin book"
    if spread_ticks is not None and spread_ticks > wide_spread_ticks:
        return "FLAT", f"wide spread {spread_ticks:.1f} ticks"
    if spoof:
        return "FLAT", "spoof/layer pull"
    if last_side == "UNKNOWN":
        return "FLAT", "print unclassified"
    bid_holding = bid_wall is not None and not _wall_pulled(bid_wall, pulls)
    ask_holding = ask_wall is not None and not _wall_pulled(ask_wall, pulls)
    if last_side == "LIFT_ASK" and cvd >= 0 and bid_holding:
        if ask_wall and ask_wall.vs_median >= wall_mult:
            return "FLAT", "ask wall in the way"
        return "LONG", "lift-ask + CVD>=0 + bid wall holds"
    if last_side == "HIT_BID" and cvd <= 0 and ask_holding:
        if bid_wall and bid_wall.vs_median >= wall_mult:
            return "FLAT", "bid wall in the way"
        return "SHORT", "hit-bid + CVD<=0 + ask wall holds"
    return "FLAT", f"{last_side} no wall confirmation"


def format_snapshot(snap: OfaSnapshot) -> str:
    bid_w = _wall_text(snap.bid_wall)
    ask_w = _wall_text(snap.ask_wall)
    spread = "n/a" if snap.spread_ticks is None else f"{snap.spread_ticks:.1f}"
    return "\n".join(
        [
            f"OFA {snap.symbol} {snap.exchange}  {snap.ts.strftime('%Y-%m-%d %H:%M:%S')}  "
            f"ltp={snap.ltp:.2f}  {snap.last_side} qty={snap.last_qty:g}",
            f"  bid={snap.best_bid}  ask={snap.best_ask}  spread={spread} ticks  "
            f"CVD={snap.session_cvd:+.0f}  buy={snap.buy_vol:g}  sell={snap.sell_vol:g}",
            f"  bid_wall {bid_w}  ask_wall {ask_w}  thin={snap.book_thin}  "
            f"spoof={snap.spoof_suspect}  oi={snap.oi_sense or '-'}",
            f"  signal={snap.signal}  of_long={snap.of_long}  of_short={snap.of_short}  "
            f"of_exit={snap.of_exit}  {snap.reason}",
        ]
    )


def _wall_text(wall: Wall | None) -> str:
    if wall is None:
        return "-"
    return f"{wall.quantity:g}@{wall.price:g} ({wall.vs_median:.1f}x)"


def _spoof_suspect(
    ask_wall: Wall | None,
    bid_wall: Wall | None,
    pulls: tuple[BookMove, ...] | list[BookMove],
    *,
    wall_mult: float,
) -> bool:
    for wall in (ask_wall, bid_wall):
        if wall is None or wall.vs_median < wall_mult:
            continue
        for move in pulls:
            if move.side == wall.side and abs(move.price - wall.price) < 1e-6:
                if move.prev_qty > 0 and move.qty <= move.prev_qty * (1.0 - PULL_FRACTION):
                    return True
    for move in pulls:
        if move.prev_qty > 0 and move.qty <= move.prev_qty * 0.2 and move.prev_qty >= 80:
            return True
    return False


def _wall_pulled(wall: Wall, pulls: tuple[BookMove, ...] | list[BookMove]) -> bool:
    for move in pulls:
        if move.side == wall.side and abs(move.price - wall.price) < 1e-6:
            return move.prev_qty > 0 and move.qty < move.prev_qty * (1.0 - PULL_FRACTION)
    return False


def _levels(raw: Any) -> list[BookLevel]:
    if not isinstance(raw, list):
        return []
    out: list[BookLevel] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        price = _num(row.get("price"))
        qty = _num(row.get("quantity", row.get("qty")))
        if price > 0 and qty >= 0:
            out.append(BookLevel(price=price, quantity=qty))
    return out


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _opt_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pairs(raw: Any) -> list[list[float]]:
    if not isinstance(raw, list):
        return []
    out: list[list[float]] = []
    for row in raw:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            try:
                out.append([float(row[0]), float(row[1])])
            except (TypeError, ValueError):
                continue
    return out


def _as_signal(value: Any) -> Signal:
    text = str(value or "FLAT").upper()
    if text in {"LONG", "SHORT", "FLAT"}:
        return text  # type: ignore[return-value]
    return "FLAT"


def _last_weekday(day: date, weekday: int) -> date:
    if day.month == 12:
        nxt = date(day.year + 1, 1, 1)
    else:
        nxt = date(day.year, day.month + 1, 1)
    last = nxt - timedelta(days=1)
    while last.weekday() != weekday:
        last -= timedelta(days=1)
    return last
