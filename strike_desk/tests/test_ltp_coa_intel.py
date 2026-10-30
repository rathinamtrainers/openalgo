from datetime import datetime, timedelta

from strike_desk.config import IST
from strike_desk.ltp_coa import evaluate
from strike_desk.ltp_coa_intel import IntelState, apply_handbook, atm_oi

ATM = 24050.0
STEP = 50.0


def _row(strike: float, *, ce_vol=0, ce_oi=0, pe_vol=0, pe_oi=0) -> dict:
    return {
        "strike": strike,
        "ce": {"volume": ce_vol, "oi": ce_oi},
        "pe": {"volume": pe_vol, "oi": pe_oi},
    }


def _ladder(**peaks: float) -> list[dict]:
    rows = []
    for i in range(-6, 7):
        strike = ATM + i * STEP
        rows.append(_row(strike))
    by_strike = {row["strike"]: row for row in rows}
    for key, value in peaks.items():
        side, metric, strike_s = key.split("_")
        strike = float(strike_s)
        field = "volume" if metric == "vol" else "oi"
        by_strike[strike][side][field] = value
    return [by_strike[k] for k in sorted(by_strike)]


def _strong():
    return _ladder(
        pe_vol_24000=1000,
        pe_vol_23900=400,
        ce_vol_24100=1000,
        ce_vol_24200=400,
    )


def test_vanishing_wtt_blocks_ce_at_eos():
    chain = _strong()
    now = datetime(2026, 10, 16, 10, 0, tzinfo=IST)
    snap = evaluate(chain, spot=23980, atm=ATM, extension_pts=20)
    assert snap.signal == "BUY_CE"
    intel = IntelState(
        session_date="2026-10-16",
        support_state="wtt",
        support_pct=0.85,
        resistance_state="strong",
    )
    refined, _ = apply_handbook(snap, intel, now)
    assert refined.game_bias == "bearish"
    assert refined.allow_ce is False
    assert refined.signal == "FLAT"


def test_soc_in_support_after_one_hour_blocks_ce():
    chain = _ladder(
        pe_vol_24000=1000,
        pe_vol_24100=800,
        ce_vol_24100=1000,
        ce_vol_24200=400,
    )
    now = datetime(2026, 10, 16, 11, 30, tzinfo=IST)
    snap = evaluate(chain, spot=23980, atm=ATM, extension_pts=20)
    intel = IntelState(
        session_date="2026-10-16",
        support_state="wtt",
        resistance_state="strong",
        weak_support_since=(now - timedelta(hours=1, minutes=5)).isoformat(),
    )
    refined, _ = apply_handbook(snap, intel, now)
    assert refined.soc == "support"
    assert refined.allow_ce is False
    assert refined.signal == "FLAT"


def test_coa2_both_oi_falling_blocks_in_window():
    chain = _strong()
    now = datetime(2026, 10, 16, 11, 0, tzinfo=IST)
    snap = evaluate(chain, spot=23980, atm=ATM, extension_pts=20)
    intel = IntelState(session_date="2026-10-16", call_oi=10_000, put_oi=10_000)
    refined, _ = apply_handbook(snap, intel, now, call_oi=8000, put_oi=8000)
    assert refined.coa2 == "avoid"
    assert refined.signal == "FLAT"


def test_first_eos_hit_blocks_a_second_tag_at_same_support():
    chain = _strong()
    now = datetime(2026, 10, 16, 10, 0, tzinfo=IST)
    snap = evaluate(chain, spot=23980, atm=ATM, extension_pts=20)
    assert snap.signal == "BUY_CE"
    intel = IntelState(session_date="2026-10-16")
    first, intel = apply_handbook(snap, intel, now)
    assert first.signal == "BUY_CE"
    second, _ = apply_handbook(snap, intel, now + timedelta(minutes=5))
    assert second.signal == "FLAT"
    assert "first EOS hit" in (second.blocker or "")


def test_atm_oi_reads_the_atm_row():
    chain = _ladder(ce_oi_24050=111, pe_oi_24050=222, ce_oi_24100=9, pe_oi_23900=8)
    assert atm_oi(chain, ATM) == (111.0, 222.0)
