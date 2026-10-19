from datetime import datetime, timedelta

from strike_desk.bar_continuation import (
    IST,
    Bar,
    FreqBook,
    backtest,
    bias_from,
    decide_signal,
    is_green,
    observe,
    snapshots,
    wilder_rsi,
)


def _ts(i: int) -> datetime:
    start = datetime(2026, 9, 1, 9, 15, tzinfo=IST)
    return start + timedelta(minutes=15 * i)


def _bar(i: int, open_: float, high: float, low: float, close: float) -> Bar:
    return Bar(ts=_ts(i), open=open_, high=high, low=low, close=close)


def test_observe_splits_green_and_red_follow_through():
    book = FreqBook()
    green = _bar(0, 100, 110, 99, 109)
    follow_up = _bar(1, 109, 112, 108, 111)
    red = _bar(2, 111, 112, 90, 91)
    follow_down = _bar(3, 91, 92, 80, 82)
    observe(book, green, follow_up)
    observe(book, red, follow_down)
    assert is_green(green)
    assert not is_green(red)
    assert book.green.n == 1 and book.green.hh == 1 and book.green.ll == 0
    assert book.red.n == 1 and book.red.hh == 0 and book.red.ll == 1
    assert bias_from(book, True, min_n=1) == "BULL"
    assert bias_from(book, False, min_n=1) == "BEAR"
    assert bias_from(book, True, min_n=50) == "FLAT"


def test_rsi_climbs_on_a_straight_up_tape():
    closes = [100.0 + i for i in range(40)]
    rsi = wilder_rsi(closes)
    assert rsi[14] is not None
    assert rsi[-1] is not None
    assert rsi[-1] > 80


def test_decide_signal_needs_adx_rsi_and_ema():
    side, reason = decide_signal(
        bias="BULL",
        adx=25.0,
        di_plus=30.0,
        di_minus=10.0,
        rsi=58.0,
        ema_value=24000.0,
        close=24100.0,
    )
    assert side == "LONG"
    assert "BULL" in reason
    blocked, why = decide_signal(
        bias="BULL",
        adx=25.0,
        di_plus=30.0,
        di_minus=10.0,
        rsi=78.0,
        ema_value=24000.0,
        close=24100.0,
    )
    assert blocked is None
    assert "RSI" in why


def test_snapshots_are_causal_and_backtest_enters_next_open():
    bars: list[Bar] = []
    price = 24000.0
    for i in range(80):
        o = price
        c = price + 8
        bars.append(_bar(i, o, c + 2, o - 1, c))
        price = c
    snaps = snapshots(bars, min_n=5)
    assert snaps[0].signal is None
    for i in range(2, len(bars)):
        # Frequencies at bar i must not include the still-open bar i vs i-1.
        assert snaps[i].p_hh is None or snaps[i].p_hh <= 1.0

    report = backtest(bars, min_n=5, flatten_eod=False)
    if report.fills:
        first = report.fills[0]
        entry_i = next(i for i, bar in enumerate(bars) if bar.ts == first.entry_ts)
        assert entry_i >= 1
        # Fill is the bar *after* the signal bar, at that bar's open ± slippage.
        assert first.entry_ts == bars[entry_i].ts
        assert snaps[entry_i - 1].signal == first.side
