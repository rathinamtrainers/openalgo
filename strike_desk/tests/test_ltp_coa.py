"""Chart of Accuracy 1.0 fixtures from howtouseltpcalculator.in chapter 11."""

from strike_desk.ltp_coa import evaluate

ATM = 24050.0
STEP = 50.0


def _row(strike: float, *, ce_vol=0, ce_oi=0, pe_vol=0, pe_oi=0) -> dict:
    return {
        "strike": strike,
        "ce": {"volume": ce_vol, "oi": ce_oi},
        "pe": {"volume": pe_vol, "oi": pe_oi},
    }


def _ladder(atm: float = ATM, n: int = 6, **peaks: float) -> list[dict]:
    """ATM ± n*50 ladder. Peak kwargs are ce_vol_24100 / pe_oi_24000 style."""
    rows = []
    for i in range(-n, n + 1):
        strike = atm + i * STEP
        rows.append(_row(strike))
    by_strike = {row["strike"]: row for row in rows}
    for key, value in peaks.items():
        side, metric, strike_s = key.split("_")
        strike = float(strike_s)
        field = "volume" if metric == "vol" else "oi"
        by_strike[strike][side][field] = value
    return [by_strike[k] for k in sorted(by_strike)]


def test_coa_1_1_both_strong_buys_ce_at_eos():
    chain = _ladder(
        pe_vol_24000=1000,
        pe_vol_23900=400,
        ce_vol_24100=1000,
        ce_vol_24200=400,
    )
    snap = evaluate(chain, spot=23980, atm=24050, extension_pts=20)
    assert snap.scenario == "1.1"
    assert snap.support and snap.support.strike == 24000
    assert snap.resistance and snap.resistance.strike == 24100
    assert snap.support.state == "strong"
    assert snap.resistance.state == "strong"
    assert snap.signal == "BUY_CE"


def test_coa_1_1_both_strong_buys_pe_at_eor():
    chain = _ladder(
        pe_vol_24000=1000,
        pe_vol_23900=400,
        ce_vol_24100=1000,
        ce_vol_24200=400,
    )
    snap = evaluate(chain, spot=24120, atm=24050, extension_pts=20)
    assert snap.scenario == "1.1"
    assert snap.signal == "BUY_PE"


def test_coa_1_1_waits_between_extensions():
    chain = _ladder(
        pe_vol_24000=1000,
        pe_vol_23900=400,
        ce_vol_24100=1000,
        ce_vol_24200=400,
    )
    snap = evaluate(chain, spot=24050, atm=24050, extension_pts=20)
    assert snap.scenario == "1.1"
    assert snap.signal == "FLAT"
    assert "waiting" in snap.reason


def test_coa_1_2_resistance_wtb_is_bearish():
    chain = _ladder(
        pe_vol_24000=1000,
        pe_vol_23900=400,
        ce_vol_24100=1000,
        ce_vol_24000=800,
    )
    snap = evaluate(chain, spot=24120, atm=24050, extension_pts=20)
    assert snap.resistance and snap.resistance.state == "wtb"
    assert snap.scenario == "1.2"
    assert snap.allow_ce is True
    assert snap.allow_pe is True
    assert snap.signal == "BUY_PE"


def test_coa_1_3_resistance_wtt_is_bullish():
    chain = _ladder(
        pe_vol_24000=1000,
        pe_vol_23900=400,
        ce_vol_24000=1000,
        ce_vol_24100=800,
    )
    snap = evaluate(chain, spot=23980, atm=24000, extension_pts=20)
    assert snap.resistance and snap.resistance.state == "wtt"
    assert snap.scenario == "1.3"
    assert snap.signal == "BUY_CE"


def test_coa_1_6_blood_bath_never_buys_ce():
    chain = _ladder(
        23650,
        pe_vol_23600=1000,
        pe_vol_23500=800,
        ce_vol_23700=1000,
        ce_vol_23600=800,
    )
    snap = evaluate(chain, spot=23580, atm=23650, extension_pts=20)
    assert snap.scenario == "1.6"
    assert snap.allow_ce is False
    assert snap.signal == "FLAT"


def test_coa_1_7_bull_run_never_buys_pe():
    chain = _ladder(
        23300,
        pe_vol_23200=1000,
        pe_vol_23400=800,
        ce_vol_23400=1000,
        ce_vol_23500=800,
    )
    snap = evaluate(chain, spot=23420, atm=23300, extension_pts=20)
    assert snap.scenario == "1.7"
    assert snap.allow_pe is False
    assert snap.signal == "FLAT"


def test_coa_1_8_mixed_weak_stands_aside():
    chain = _ladder(
        22650,
        pe_vol_22650=1000,
        pe_vol_22600=800,
        ce_vol_22700=1000,
        ce_vol_22800=800,
    )
    snap = evaluate(chain, spot=22630, atm=22650, extension_pts=20)
    assert snap.scenario == "1.8"
    assert snap.signal == "FLAT"
    assert snap.allow_ce is False
    assert snap.allow_pe is False


def test_coa_1_9_mixed_weak_stands_aside():
    chain = _ladder(
        23600,
        pe_vol_23500=1000,
        pe_vol_23600=800,
        ce_vol_23700=1000,
        ce_vol_23600=800,
    )
    snap = evaluate(chain, spot=23720, atm=23600, extension_pts=20)
    assert snap.scenario == "1.9"
    assert snap.signal == "FLAT"


def test_picks_oi_when_its_peak_is_closer_to_atm():
    chain = _ladder(
        pe_vol_23800=5000,
        pe_oi_24000=1000,
        ce_vol_24100=1000,
        ce_vol_24200=100,
    )
    snap = evaluate(chain, spot=24050, atm=24050, extension_pts=20)
    assert snap.support and snap.support.strike == 24000
    assert snap.support.column == "oi"


def test_yellow_box_requires_75_percent():
    chain = _ladder(
        pe_vol_24000=1000,
        pe_vol_23900=740,
        ce_vol_24100=1000,
        ce_vol_24200=740,
    )
    snap = evaluate(chain, spot=24050, atm=24050, extension_pts=20)
    assert snap.support and snap.support.state == "strong"
    assert snap.support.yellow is False
    assert snap.scenario == "1.1"
