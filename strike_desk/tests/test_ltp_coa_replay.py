from datetime import date, datetime

from strike_desk.config import IST
from strike_desk.ltp_coa_replay import (
    TapeBar,
    build_tape,
    expiry_token,
    format_report,
    option_symbol,
    replay,
)


def _ts(hhmm: str) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime(2026, 10, 14, hour, minute, tzinfo=IST)


def _side(volume: float, oi: float = 0, ltp: float = 0, symbol: str = "") -> dict:
    return {"volume": volume, "oi": oi, "ltp": ltp, "symbol": symbol}


def _row(strike: float, **sides) -> dict:
    row = {"strike": strike, "ce": _side(0), "pe": _side(0)}
    row.update(sides)
    return row


def _bar(hhmm: str, spot: float, rows: list[dict], atm: float | None = None) -> TapeBar:
    step = 50.0
    return TapeBar(
        ts=_ts(hhmm),
        spot=spot,
        atm=atm if atm is not None else round(spot / step) * step,
        expiry="20OCT26",
        rows=rows,
    )


def _strong_chain(*, ce_ltp: float, pe_ltp: float = 80) -> list[dict]:
    return [
        _row(23900, pe=_side(400, 400, 120)),
        _row(24000, pe=_side(1000, 1000, 90), ce=_side(400, 400, ce_ltp)),
        _row(24100, ce=_side(1000, 1000, pe_ltp), pe=_side(200, 200, 50)),
        _row(24200, ce=_side(400, 400, 40)),
    ]


def test_option_symbol_and_expiry_token():
    assert option_symbol("NIFTY", "20OCT26", 24000, "CE") == "NIFTY20OCT2624000CE"
    assert expiry_token(date(2026, 10, 20)) == "20OCT26"


def test_replay_takes_coa_1_1_ce_and_hits_tsl_lock():
    tape = [
        _bar("09:25", 24050, _strong_chain(ce_ltp=100)),
        _bar("09:30", 23980, _strong_chain(ce_ltp=100)),
        _bar("09:35", 24050, _strong_chain(ce_ltp=140)),
        _bar("09:40", 24050, _strong_chain(ce_ltp=118)),
    ]
    report = replay(tape)
    assert report.signals >= 1
    assert len(report.fills) == 1
    fill = report.fills[0]
    assert fill.side == "CE"
    assert fill.reason == "premium TSL lock 20% chase 40%"
    assert fill.pnl == (118 - 100) * 65


def test_replay_blood_bath_does_not_buy_ce_at_eos():
    rows = [
        _row(23500, pe=_side(800, 800, 150)),
        _row(23600, pe=_side(1000, 1000, 120), ce=_side(800, 800, 90)),
        _row(23700, ce=_side(1000, 1000, 70)),
    ]
    tape = [_bar("09:30", 23580, rows, atm=23650)]
    report = replay(tape)
    assert report.fills == []


def test_replay_flattens_at_1515():
    chain = _strong_chain(ce_ltp=100)
    tape = [
        _bar("09:30", 23980, chain),
        _bar("15:15", 23970, _strong_chain(ce_ltp=95)),
    ]
    report = replay(tape)
    assert len(report.fills) == 1
    assert report.fills[0].reason == "15:15 flatten"


def test_one_position_at_a_time_until_flatten():
    chain = _strong_chain(ce_ltp=100)
    tape = [
        _bar("09:30", 23980, chain),
        _bar("09:40", 23970, _strong_chain(ce_ltp=90)),
        _bar("10:00", 23960, _strong_chain(ce_ltp=100)),
        _bar("15:15", 23950, _strong_chain(ce_ltp=90)),
    ]
    report = replay(tape)
    assert len(report.fills) == 1


class _Feed:
    def __init__(self, books: dict[str, list[dict]]) -> None:
        self.books = books

    def history(self, symbol, exchange, interval, start_date, end_date, source="api"):
        del exchange, interval, start_date, end_date, source
        return list(self.books.get(symbol, []))


def test_build_tape_accumulates_session_volume():
    day = date(2026, 10, 14)
    t1 = int(_ts("09:20").timestamp())
    t2 = int(_ts("09:25").timestamp())
    spot = [
        {"timestamp": t1, "close": 24050},
        {"timestamp": t2, "close": 24050},
    ]
    ce = option_symbol("NIFTY", "20OCT26", 24100, "CE")
    pe = option_symbol("NIFTY", "20OCT26", 24000, "PE")
    books = {
        "NIFTY": spot,
        ce: [
            {"timestamp": t1, "close": 80, "volume": 10, "oi": 100},
            {"timestamp": t2, "close": 81, "volume": 15, "oi": 110},
        ],
        pe: [
            {"timestamp": t1, "close": 70, "volume": 20, "oi": 200},
            {"timestamp": t2, "close": 69, "volume": 5, "oi": 210},
        ],
    }
    tape = build_tape(
        _Feed(books),
        day,
        day,
        strike_count=2,
        expiry_for=lambda _: date(2026, 10, 20),
    )
    assert len(tape) == 2
    row_ce = next(r for r in tape[1].rows if r["strike"] == 24100)
    row_pe = next(r for r in tape[1].rows if r["strike"] == 24000)
    assert row_ce["ce"]["volume"] == 25
    assert row_pe["pe"]["volume"] == 25
    assert row_ce["ce"]["oi"] == 110


def test_format_report_mentions_pf():
    chain = _strong_chain(ce_ltp=100)
    report = replay([_bar("09:30", 23980, chain), _bar("15:15", 23970, _strong_chain(ce_ltp=95))])
    text = format_report(report)
    assert "PF=" in text
    assert "trades=1" in text


def test_format_report_breaks_out_days():
    chain = _strong_chain(ce_ltp=100)
    day_two = TapeBar(
        ts=datetime(2026, 10, 15, 9, 30, tzinfo=IST),
        spot=23980,
        atm=24000,
        expiry="20OCT26",
        rows=chain,
    )
    report = replay(
        [
            _bar("09:30", 23980, chain),
            _bar("15:15", 23970, _strong_chain(ce_ltp=95)),
            day_two,
            TapeBar(
                ts=datetime(2026, 10, 15, 15, 15, tzinfo=IST),
                spot=23950,
                atm=24000,
                expiry="20OCT26",
                rows=_strong_chain(ce_ltp=95),
            ),
        ]
    )
    text = format_report(report)
    assert "day 2026-10-14" in text
    assert "day 2026-10-15" in text
    assert report.wins == 0
    assert len(report.fills) == 2
