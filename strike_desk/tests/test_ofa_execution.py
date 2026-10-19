from datetime import date, datetime

import pytest
import respx
from httpx import Response

from strike_desk.config import IST
from strike_desk.errors import AnalyzerOff
from strike_desk.ofa import OfaSnapshot
from strike_desk.ofa_analyzer import OfaAnalyzer, pick_atm_option
from strike_desk.ofa_hands import _OFA_MAP, OFA_BUY_CE, OFA_BUY_PE, OFA_EXIT
from strike_desk.ofa_live import OfaRunner
from strike_desk.tradetron_hands import build_payload
from tests.conftest import BASE_URL


def _snap(signal: str) -> OfaSnapshot:
    return OfaSnapshot(
        ts=datetime(2026, 10, 16, 10, 15, tzinfo=IST),
        symbol="NIFTY27OCT26FUT",
        exchange="NFO",
        ltp=25000.10,
        best_bid=25000.00,
        best_ask=25000.10,
        spread_ticks=2.0,
        last_side="LIFT_ASK" if signal == "LONG" else "HIT_BID",
        last_qty=80,
        session_cvd=80 if signal == "LONG" else -80,
        buy_vol=80,
        sell_vol=0,
        bid_wall=None,
        ask_wall=None,
        stacks=(),
        pulls=(),
        book_thin=False,
        spoof_suspect=signal == "FLAT",
        oi_sense=None,
        signal=signal,  # type: ignore[arg-type]
        of_long=1 if signal == "LONG" else 0,
        of_short=1 if signal == "SHORT" else 0,
        of_exit=1 if signal == "FLAT" else 0,
        reason="test",
    )


class _FakeHands:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, action: str) -> None:
        self.sent.append(action)


class _FakeAnalyzer:
    def __init__(self, *, on: bool = True) -> None:
        self.on = on
        self.placed: list[tuple[str, str, int]] = []

    def resolve_atm(self, option_type: str, expiry_day: date) -> tuple[str, int]:
        return f"NIFTY21OCT2625800{option_type}", 65

    def place(self, symbol: str, action: str, quantity: int):
        if not self.on:
            raise AnalyzerOff("OpenAlgo analyze_mode is off; refusing placeorder")
        self.placed.append((symbol, action, quantity))


def test_ofa_hands_flags_are_not_coa_or_quadshield():
    payload = build_payload("token-aaaaaaaa", _OFA_MAP[OFA_BUY_CE])
    assert payload["key"] == "of_long"
    assert payload["value"] == "1"
    assert payload["key1"] == "of_short"
    keys = {pair[0] for pairs in _OFA_MAP.values() for pair in pairs}
    assert keys == {"of_long", "of_short", "of_exit"}
    assert "ltp_buy_ce" not in keys
    assert "qs_buy" not in keys
    assert _OFA_MAP[OFA_BUY_PE][0] == ("of_short", 1)
    assert _OFA_MAP[OFA_EXIT][0] == ("of_exit", 1)


def test_pick_atm_option_from_chain():
    payload = {
        "atm_strike": 25800.0,
        "chain": [
            {
                "strike": 25750.0,
                "ce": {"symbol": "NIFTY21OCT2625750CE", "lotsize": 65},
                "pe": {"symbol": "NIFTY21OCT2625750PE", "lotsize": 65},
            },
            {
                "strike": 25800.0,
                "ce": {"symbol": "NIFTY21OCT2625800CE", "lotsize": 65},
                "pe": {"symbol": "NIFTY21OCT2625800PE", "lotsize": 65},
            },
        ],
    }
    assert pick_atm_option(payload, "CE") == ("NIFTY21OCT2625800CE", 65)
    assert pick_atm_option(payload, "PE") == ("NIFTY21OCT2625800PE", 65)


def test_analyzer_refuses_when_analyze_mode_off(settings, client):
    analyzer = OfaAnalyzer(settings, client)
    try:
        with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
            router.post("/api/v1/analyzer").mock(
                return_value=Response(200, json={"status": "success", "analyze_mode": False})
            )
            with pytest.raises(AnalyzerOff):
                analyzer.place("NIFTY21OCT2625800CE", "BUY", 65)
            assert not any(
                call.request.url.path == "/api/v1/placeorder" for call in router.calls
            )
    finally:
        analyzer.close()


def test_analyzer_places_only_in_analyze_mode(settings, client):
    analyzer = OfaAnalyzer(settings, client)
    try:
        with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
            router.post("/api/v1/analyzer").mock(
                return_value=Response(200, json={"status": "success", "analyze_mode": True})
            )
            router.post("/api/v1/placeorder").mock(
                return_value=Response(
                    200,
                    json={"status": "success", "orderid": "AN-1", "mode": "analyze"},
                )
            )
            fill = analyzer.place("NIFTY21OCT2625800CE", "BUY", 65)
            assert fill.orderid == "AN-1"
            assert fill.mode == "analyze"
    finally:
        analyzer.close()


def test_runner_fires_analyzer_and_hands_once_on_signal_change(settings, client):
    hands = _FakeHands()
    analyzer = _FakeAnalyzer()
    runner = OfaRunner(
        settings,
        client,
        hands=hands,
        analyzer=analyzer,
        expiry_date_for=lambda day: date(2026, 10, 20),
    )
    actions = runner._act(_snap("LONG"), datetime(2026, 10, 16, 10, 15, tzinfo=IST))
    assert actions == ["buy_ce"]
    assert hands.sent == [OFA_BUY_CE]
    assert analyzer.placed == [("NIFTY21OCT2625800CE", "BUY", 65)]

    again = runner._act(_snap("LONG"), datetime(2026, 10, 16, 10, 16, tzinfo=IST))
    assert again == []
    assert hands.sent == [OFA_BUY_CE]

    flip = runner._act(_snap("SHORT"), datetime(2026, 10, 16, 10, 17, tzinfo=IST))
    assert flip == ["exit", "buy_pe"]
    assert hands.sent[-1] == OFA_BUY_PE
    assert analyzer.placed[-2] == ("NIFTY21OCT2625800CE", "SELL", 65)
    assert analyzer.placed[-1] == ("NIFTY21OCT2625800PE", "BUY", 65)


def test_runner_skips_analyzer_fill_when_analyze_off(settings, client):
    hands = _FakeHands()
    analyzer = _FakeAnalyzer(on=False)
    runner = OfaRunner(
        settings,
        client,
        hands=hands,
        analyzer=analyzer,
        expiry_date_for=lambda day: date(2026, 10, 20),
    )
    actions = runner._act(_snap("LONG"), datetime(2026, 10, 16, 10, 15, tzinfo=IST))
    assert actions == ["buy_ce"]
    assert hands.sent == [OFA_BUY_CE]
    assert analyzer.placed == []
