from datetime import datetime, time, timedelta

from strike_desk.config import IST
from strike_desk.ltp_coa import evaluate
from strike_desk.ltp_coa_exits import OpenTrade, can_reenter, decide_exit


def _chain():
    return [
        {
            "strike": 23900,
            "ce": {"volume": 0, "oi": 0, "ltp": 0},
            "pe": {"volume": 400, "oi": 400, "ltp": 120},
        },
        {
            "strike": 24000,
            "ce": {"volume": 400, "oi": 400, "ltp": 100},
            "pe": {"volume": 1000, "oi": 1000, "ltp": 90},
        },
        {
            "strike": 24100,
            "ce": {"volume": 1000, "oi": 1000, "ltp": 70},
            "pe": {"volume": 200, "oi": 200, "ltp": 50},
        },
        {
            "strike": 24200,
            "ce": {"volume": 400, "oi": 400, "ltp": 40},
            "pe": {"volume": 0, "oi": 0, "ltp": 0},
        },
    ]


def _trade(**kwargs) -> OpenTrade:
    snap = evaluate(_chain(), spot=23980, atm=24050, extension_pts=20)
    base = dict(
        side="CE",
        entry=100.0,
        entry_ts=datetime(2026, 10, 16, 9, 30, tzinfo=IST),
        peak=100.0,
        eos=snap.eos,
        eor=snap.eor,
        support=snap.support.strike if snap.support else None,
        resistance=snap.resistance.strike if snap.resistance else None,
        scenario=snap.scenario,
    )
    base.update(kwargs)
    return OpenTrade(**base)


def _live(spot: float = 23980):
    return evaluate(_chain(), spot=spot, atm=24050, extension_pts=20)


def test_tsl_waits_for_reclaim_even_at_40_percent():
    trade = _trade()
    trade.note_ltp(145)
    assert trade.reclaimed is False
    assert decide_exit(time(10, 0), 23980, 145, trade, _live(23980)) is None


def test_tsl_arms_at_40_and_locks_20_after_reclaim():
    trade = _trade()
    trade.note_ltp(140)
    assert decide_exit(time(10, 0), 24050, 140, trade, _live(24050)) is None
    assert trade.reclaimed is True
    assert decide_exit(time(10, 5), 24050, 118, trade, _live(24050)) == (
        "premium TSL lock 20% chase 40%"
    )


def test_tsl_chases_peak_giveback():
    trade = _trade()
    trade.note_ltp(160)
    decide_exit(time(10, 0), 24050, 160, trade, _live(24050))
    # peak 160, giveback 20 -> stop 140; 141 still in
    assert decide_exit(time(10, 5), 24050, 141, trade, _live(24050)) is None
    assert decide_exit(time(10, 10), 24050, 139, trade, _live(24050)) == (
        "premium TSL lock 20% chase 40%"
    )


def test_spot_target_at_resistance():
    trade = _trade()
    reason = decide_exit(time(10, 0), 24100, 130, trade, _live(24100))
    assert reason == "spot target opposite S/R"


def test_hard_sl_below_eos():
    trade = _trade()
    # eos ~ 23980, buffer 15 -> 23965
    reason = decide_exit(time(10, 0), 23960, 90, trade, _live(23960))
    assert reason == "spot SL extension broken"


def test_reentry_after_five_minutes():
    ts = datetime(2026, 10, 16, 10, 0, tzinfo=IST)
    assert can_reenter(ts, None) is True
    assert can_reenter(ts, ts) is False
    assert can_reenter(ts + timedelta(minutes=5), ts) is True
