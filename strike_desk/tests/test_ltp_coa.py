"""Chart of Accuracy 1.0 fixtures from howtouseltpcalculator.in chapter 11."""

from strike_desk.ltp_coa import evaluate

ATM = 24050.0
STEP = 50.0


def _row(
    strike: float,
    *,
    ce_vol=0,
    ce_oi=0,
    pe_vol=0,
    pe_oi=0,
    ce_ltp=0,
    pe_ltp=0,
    ce_delta=0,
    pe_delta=0,
) -> dict:
    return {
        "strike": strike,
        "ce": {"volume": ce_vol, "oi": ce_oi, "ltp": ce_ltp, "delta": ce_delta},
        "pe": {"volume": pe_vol, "oi": pe_oi, "ltp": pe_ltp, "delta": pe_delta},
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
        field = {"vol": "volume", "oi": "oi", "ltp": "ltp", "delta": "delta"}[metric]
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
    assert snap.allow_ce is False
    assert snap.allow_pe is True
    assert snap.signal == "BUY_PE"


def test_coa_1_2_does_not_buy_ce_at_eos():
    chain = _ladder(
        pe_vol_24000=1000,
        pe_vol_23900=400,
        ce_vol_24100=1000,
        ce_vol_24000=800,
    )
    snap = evaluate(chain, spot=23980, atm=24050, extension_pts=20)
    assert snap.scenario == "1.2"
    assert snap.signal == "FLAT"


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
    assert snap.allow_ce is True
    assert snap.allow_pe is False
    assert snap.signal == "BUY_CE"


def test_coa_1_3_does_not_buy_pe_at_eor():
    chain = _ladder(
        pe_vol_24000=1000,
        pe_vol_23900=400,
        ce_vol_24000=1000,
        ce_vol_24100=800,
    )
    snap = evaluate(chain, spot=24020, atm=24000, extension_pts=20)
    assert snap.scenario == "1.3"
    assert snap.signal == "FLAT"


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


def test_dual_factor_away_weakness_stays_strong():
    chain = _ladder(
        pe_vol_24000=1000,
        pe_oi_24000=1000,
        pe_vol_23900=800,
        pe_oi_23900=400,
        ce_vol_24100=1000,
        ce_oi_24100=1000,
        ce_vol_24200=400,
        ce_oi_24200=400,
    )
    snap = evaluate(chain, spot=24050, atm=24050, extension_pts=20)
    assert snap.support and snap.support.dual is True
    assert snap.support.state == "strong"
    assert snap.scenario == "1.1"


def test_dual_factor_towards_line_is_weak():
    chain = _ladder(
        pe_vol_24000=1000,
        pe_oi_24000=1000,
        pe_vol_24100=800,
        pe_oi_23900=400,
        ce_vol_24100=1000,
        ce_oi_24100=1000,
        ce_vol_24200=400,
        ce_oi_24200=400,
    )
    snap = evaluate(chain, spot=24050, atm=24050, extension_pts=20)
    assert snap.support and snap.support.dual is True
    assert snap.support.state == "wtt"


def _btc_ladder(**peaks: float) -> list[dict]:
    atm = 77400.0
    rows = []
    for i in range(-10, 11):
        strike = atm + i * 200
        rows.append(_row(strike))
    by_strike = {row["strike"]: row for row in rows}
    for key, value in peaks.items():
        side, metric, strike_s = key.split("_")
        strike = float(strike_s)
        field = {"vol": "volume", "oi": "oi", "ltp": "ltp", "delta": "delta"}[metric]
        by_strike[strike][side][field] = value
    return [by_strike[k] for k in sorted(by_strike)]


def test_btc_full_chain_yellow_can_mix_from_other_side():
    chain = _btc_ladder(
        pe_vol_77000=448,
        pe_vol_78000=489,
        ce_vol_79000=400,
        ce_vol_78600=312,
    )
    snap = evaluate(
        chain, spot=77400, atm=77400, strike_step=200, extension_pts=80, yellow_scope="chain"
    )
    assert snap.support and snap.support.strike == 77000
    assert snap.support.state == "wtt"
    assert snap.scenario == "1.9"
    assert snap.signal == "FLAT"


def test_btc_side_window_yellow_ignores_pe_volume_above_atm():
    chain = _btc_ladder(
        pe_vol_77000=448,
        pe_vol_76800=200,
        pe_vol_78000=489,
        ce_vol_79000=400,
        ce_vol_78600=312,
    )
    snap = evaluate(
        chain, spot=77400, atm=77400, strike_step=200, extension_pts=80, yellow_scope="side"
    )
    assert snap.support and snap.support.strike == 77000
    assert snap.support.state == "strong"
    assert snap.eos == 76920
    assert snap.eor == 79080
    assert snap.scenario != "1.9"


def test_eos_eor_use_live_put_and_call_premium():
    chain = _ladder(
        pe_vol_24000=1000,
        pe_vol_23900=400,
        pe_ltp_24000=80,
        ce_vol_24100=1000,
        ce_vol_24200=400,
        ce_ltp_24100=90,
    )
    snap = evaluate(chain, spot=23910, atm=24050, extension_pts=20)
    assert snap.support and snap.support.strike == 24000
    assert snap.resistance and snap.resistance.strike == 24100
    assert snap.eos == 23920.0
    assert snap.eor == 24190.0
    assert snap.signal == "BUY_CE"


def test_eos_scales_premium_by_put_delta():
    chain = _ladder(
        pe_vol_24000=1000,
        pe_vol_23900=400,
        pe_ltp_24000=80,
        pe_delta_24000=-0.4,
        ce_vol_24100=1000,
        ce_vol_24200=400,
        ce_ltp_24100=90,
        ce_delta_24100=0.45,
    )
    snap = evaluate(chain, spot=24050, atm=24050, extension_pts=20)
    assert snap.eos == 23800.0
    assert snap.eor == 24300.0
    assert snap.signal == "FLAT"
