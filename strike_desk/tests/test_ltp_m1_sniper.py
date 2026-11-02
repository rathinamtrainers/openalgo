from datetime import datetime, timedelta, timezone

from strike_desk.config import IST
from strike_desk.ltp_m1_live import M1SniperRunner
from strike_desk.ltp_m1_sniper import (
    M1Bar,
    decide_m1_exit,
    evaluate_m1,
    halt_until,
    in_m1_window,
)
from strike_desk.tradetron_hands import M1_BUY_CE, M1_EXIT, NoopCoaHands


def _bar(ts: datetime, close: float, *, low=None, high=None, open_=None) -> M1Bar:
    return M1Bar(
        ts=ts,
        open=open_ if open_ is not None else close,
        high=high if high is not None else close + 5,
        low=low if low is not None else close - 5,
        close=close,
    )


def _series(n: int, start: float, step: float, width: timedelta, origin: datetime) -> list[M1Bar]:
    out = []
    price = start
    for i in range(n):
        ts = origin + i * width
        out.append(_bar(ts, price))
        price += step
    return out


def test_m1_window_is_london_ny_overlap_not_cash_hours():
    friday = datetime(2026, 10, 30, 18, 0, tzinfo=IST)
    assert in_m1_window(friday) is True
    assert in_m1_window(datetime(2026, 10, 30, 10, 0, tzinfo=IST)) is False
    assert in_m1_window(datetime(2026, 10, 30, 20, 5, tzinfo=IST)) is False
    assert in_m1_window(datetime(2026, 10, 29, 20, 5, tzinfo=IST)) is True


def test_m1_exit_uses_wick_and_target():
    assert decide_m1_exit(99, "CE", sl=100, tp=110) == "M1 SL below entry wick"
    assert decide_m1_exit(111, "CE", sl=100, tp=110) == "M1 TP"
    assert decide_m1_exit(105, "CE", sl=100, tp=110) is None
    assert decide_m1_exit(121, "PE", sl=120, tp=100) == "M1 SL above entry wick"


def test_two_strike_and_outside_window_stay_flat():
    now = datetime(2026, 10, 30, 18, 0, tzinfo=IST)
    empty = evaluate_m1([], [], now, consecutive_stops=2)
    assert empty.signal == "FLAT"
    assert "two-strike" in empty.reason
    closed = evaluate_m1([], [], datetime(2026, 10, 30, 10, 0, tzinfo=IST))
    assert closed.reason == "outside London/NY overlap"


def test_long_setup_buys_ce_not_coa_flags(settings):
    origin = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    m5 = _series(60, 1000, 2, timedelta(minutes=5), origin)
    m1 = _series(80, 1100, 1, timedelta(minutes=1), origin)
    last = m1[-1]
    m1[-1] = _bar(last.ts, last.close, low=0, high=last.close + 20, open_=last.close - 1)
    dump_start = len(m1) - 16
    for i in range(dump_start, len(m1) - 1):
        price = 1100 - (i - dump_start) * 8
        m1[i] = _bar(m1[i].ts, price, low=price - 12, high=price + 2)
    now = datetime(2026, 10, 30, 18, 5, tzinfo=IST)
    snap = evaluate_m1(m1, m5, now)
    assert snap.trend == "long"
    if snap.signal == "BUY_CE":
        assert snap.sl is not None and snap.sl < (snap.entry or 0)
        assert snap.tp is not None and snap.tp > (snap.entry or 0)

    class _Hands:
        def __init__(self) -> None:
            self.sent: list[tuple[str, str]] = []

        def send(self, action: str, board: str = "nifty"):
            self.sent.append((action, board))
            return type("R", (), {"http_status": 200})()

    class _Feed:
        def candles(self, symbol, resolution, start, end):
            rows = m1 if resolution == "1m" else m5
            return [
                {
                    "time": int(bar.ts.timestamp()),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": 1,
                }
                for bar in rows
            ]

    hands = _Hands()
    runner = M1SniperRunner(settings, _Feed(), hands)
    sent = runner.step(now)
    assert M1_EXIT not in sent
    if sent:
        assert sent == [M1_BUY_CE]
        assert hands.sent == [(M1_BUY_CE, "btc-options")]
        state = (settings.state_dir / "coa_live_m1_btc.json").read_text(encoding="utf-8")
        assert "CE" in state
        assert (settings.state_dir / "coa_live_btc.json").exists() is False


def test_m1_noop_hands_do_not_open(settings):
    origin = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    m5 = _series(60, 1000, 2, timedelta(minutes=5), origin)
    m1 = _series(80, 1100, 1, timedelta(minutes=1), origin)

    class _Feed:
        def candles(self, _symbol, resolution, _start, _end):
            rows = m1 if resolution == "1m" else m5
            return [{"time": int(bar.ts.timestamp()), "close": bar.close} for bar in rows]

    runner = M1SniperRunner(settings, _Feed(), NoopCoaHands())
    sent = runner.step(datetime(2026, 10, 30, 18, 5, tzinfo=IST))
    assert sent == []


def test_halt_until_is_next_ist_day():
    now = datetime(2026, 10, 30, 20, 0, tzinfo=IST)
    assert halt_until(now) == "2026-10-31"
