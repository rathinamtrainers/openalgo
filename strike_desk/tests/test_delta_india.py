from datetime import date

from strike_desk.delta_india import chain_from_tickers


def _ticker(symbol: str, strike: str, *, close: float, volume: float, oi: str, spot: str):
    kind = "call_options" if symbol.startswith("C-") else "put_options"
    return {
        "symbol": symbol,
        "strike_price": strike,
        "contract_type": kind,
        "close": close,
        "mark_price": str(close),
        "volume": volume,
        "oi": oi,
        "spot_price": spot,
        "greeks": {"spot": spot},
    }


def test_chain_from_tickers_picks_requested_expiry_and_atm():
    tickers = [
        _ticker("C-BTC-80800-301026", "80800", close=410, volume=12, oi="4.1", spot="80910"),
        _ticker("P-BTC-80800-301026", "80800", close=390, volume=9, oi="3.2", spot="80910"),
        _ticker("C-BTC-81000-301026", "81000", close=320, volume=40, oi="8.0", spot="80910"),
        _ticker("P-BTC-81000-301026", "81000", close=280, volume=22, oi="5.5", spot="80910"),
        _ticker("C-BTC-81000-311026", "81000", close=500, volume=1, oi="0.2", spot="80910"),
    ]
    payload = chain_from_tickers(
        tickers, underlying="BTC", expiry=date(2026, 10, 30)
    )
    assert payload["underlying_ltp"] == 80910.0
    assert payload["atm_strike"] == 81000.0
    strikes = [row["strike"] for row in payload["chain"]]
    assert strikes == [80800.0, 81000.0]
    atm = payload["chain"][1]
    assert atm["ce"]["symbol"] == "C-BTC-81000-301026"
    assert atm["ce"]["ltp"] == 320
    assert atm["pe"]["oi"] == 5.5
    assert "C-BTC-81000-311026" not in str(payload)


def test_chain_clips_far_strikes_so_coa_stays_near_atm():
    tickers = [
        _ticker("P-BTC-60000-301026", "60000", close=20000, volume=9999, oi="90", spot="80910"),
        _ticker("C-BTC-80800-301026", "80800", close=410, volume=12, oi="4.1", spot="80910"),
        _ticker("P-BTC-80800-301026", "80800", close=390, volume=80, oi="8.0", spot="80910"),
        _ticker("C-BTC-81000-301026", "81000", close=320, volume=40, oi="8.0", spot="80910"),
        _ticker("P-BTC-81000-301026", "81000", close=280, volume=22, oi="5.5", spot="80910"),
        _ticker("C-BTC-120000-301026", "120000", close=1, volume=8000, oi="70", spot="80910"),
    ]
    payload = chain_from_tickers(
        tickers,
        underlying="BTC",
        expiry=date(2026, 10, 30),
        strike_count=2,
        strike_step=200.0,
    )
    strikes = [row["strike"] for row in payload["chain"]]
    assert 60000.0 not in strikes
    assert 120000.0 not in strikes
    assert strikes == [80800.0, 81000.0]
