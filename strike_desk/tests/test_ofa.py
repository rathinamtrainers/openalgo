from datetime import date, datetime

from strike_desk.config import IST
from strike_desk.ofa import (
    Book,
    BookLevel,
    SessionState,
    classify_print,
    evaluate,
    monthly_fut_symbol,
    oi_sense,
    parse_depth,
)


def _book(
    *,
    ltp: float = 25000.05,
    ltq: float = 75,
    volume: float = 1000,
    bids: list[tuple[float, float]] | None = None,
    asks: list[tuple[float, float]] | None = None,
    total_buy: float = 400,
    total_sell: float = 350,
    oi: float | None = 1_000_000,
) -> Book:
    bids = bids or [(25000.00, 120), (24999.95, 40), (24999.90, 35), (24999.85, 30)]
    asks = asks or [(25000.10, 45), (25000.15, 40), (25000.20, 38), (25000.25, 36)]
    return Book(
        symbol="NIFTY28OCT26FUT",
        exchange="NFO",
        ltp=ltp,
        ltq=ltq,
        volume=volume,
        bids=tuple(BookLevel(p, q) for p, q in bids),
        asks=tuple(BookLevel(p, q) for p, q in asks),
        total_buy=total_buy,
        total_sell=total_sell,
        oi=oi,
    )


def _ts() -> datetime:
    return datetime(2026, 10, 18, 10, 15, tzinfo=IST)


def test_classify_print_lift_and_hit():
    assert classify_print(25000.10, 25000.00, 25000.10) == "LIFT_ASK"
    assert classify_print(25000.00, 25000.00, 25000.10) == "HIT_BID"
    assert classify_print(0, None, None) == "UNKNOWN"


def test_monthly_fut_is_last_tuesday():
    assert monthly_fut_symbol("NIFTY", date(2026, 10, 18)) == "NIFTY27OCT26FUT"
    assert monthly_fut_symbol("NIFTY", date(2026, 10, 28)) == "NIFTY24NOV26FUT"


def test_oi_sense_matrix():
    assert oi_sense(10, 5) == "LONG_BUILD"
    assert oi_sense(-10, 5) == "SHORT_BUILD"
    assert oi_sense(10, -5) == "SHORT_COVER"
    assert oi_sense(-10, -5) == "LONG_UNWIND"


def test_parse_depth_and_lift_ask_goes_long():
    raw = {
        "ltp": 25000.10,
        "ltq": 80,
        "volume": 5000,
        "totalbuyqty": 400,
        "totalsellqty": 200,
        "oi": 1_100_000,
        "bids": [
            {"price": 25000.00, "quantity": 150},
            {"price": 24999.95, "quantity": 40},
            {"price": 24999.90, "quantity": 35},
        ],
        "asks": [
            {"price": 25000.10, "quantity": 40},
            {"price": 25000.15, "quantity": 38},
            {"price": 25000.20, "quantity": 36},
        ],
    }
    book = parse_depth(raw, symbol="NIFTY27OCT26FUT", exchange="NFO")
    state = SessionState(day="2026-10-18", open_ltp=24990, open_oi=1_000_000)
    snap, nxt = evaluate(book, state, now=_ts())
    assert snap.last_side == "LIFT_ASK"
    assert snap.of_long == 1
    assert snap.of_short == 0
    assert nxt.cvd == 80
    assert snap.signal == "LONG"


def test_thin_book_stays_flat():
    book = _book(
        bids=[(25000.0, 2), (24999.95, 2)],
        asks=[(25000.1, 2), (25000.15, 2)],
        total_buy=4,
        total_sell=4,
    )
    snap, _ = evaluate(book, SessionState(day="2026-10-18"), now=_ts())
    assert snap.signal == "FLAT"
    assert snap.book_thin
    assert snap.of_long == 0


def test_pulled_wall_is_spoof_and_exits_prior_long():
    prev = SessionState(
        day="2026-10-18",
        last_signal="LONG",
        last_volume=900,
        prev_asks=[[25000.40, 200], [25000.15, 40], [25000.20, 38]],
        prev_bids=[[25000.00, 40], [24999.95, 40], [24999.90, 35]],
    )
    book = _book(
        ltp=25000.00,
        ltq=60,
        volume=1100,
        asks=[(25000.10, 40), (25000.15, 38), (25000.20, 36), (25000.40, 10)],
        bids=[(25000.00, 40), (24999.95, 38), (24999.90, 35)],
    )
    snap, _ = evaluate(book, prev, now=_ts())
    assert snap.spoof_suspect or snap.signal == "FLAT"
    assert snap.of_exit == 1


def test_session_resets_cvd_on_a_new_day():
    stale = SessionState(day="2026-10-17", cvd=5000, last_volume=100)
    book = _book(volume=100)
    _, nxt = evaluate(book, stale, now=_ts())
    assert nxt.day == "2026-10-18"
    assert nxt.cvd == 0
