"""120-minute 21-EMA playbook: math, mapping, overlay regression, strategist reach."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from freezegun import freeze_time

from strike_desk.approval_gate import build_intent
from strike_desk.config import IST, Settings
from strike_desk.ema120 import (
    EmaBar,
    EmaExits,
    band_exit,
    closed_bars,
    ema_series,
    format_trailer,
    history_interval,
    parse_trailer,
    signal_from_bars,
)
from strike_desk.graph import _decide_outcome, _wants_a_contract
from strike_desk.options_strategist import direction_from_regime
from strike_desk.playbook import Playbook, check
from strike_desk.specialists import ROLE_STRATEGIST, SpecialistRequest, SpecialistResult
from tests.approval_fixtures import cleared_context
from tests.chain_fixtures import FIXED_NOW, valid_proposal
from tests.conftest import StubSpecialist

WEAK_ADX = {
    "daily_plus_di": 12.4,
    "daily_minus_di": 18.1,
    "daily_adx": 18.2,
    "ten_plus_di": 14.0,
    "ten_minus_di": 20.0,
    "ten_adx": 22.0,
}


def _bar(start: datetime, high: float, low: float, close: float) -> EmaBar:
    return EmaBar(start=start, high=high, low=low, close=close)


def _ladder(
    n: int,
    *,
    start: datetime,
    high: float,
    low: float,
    close: float,
    interval: int = 120,
) -> list[EmaBar]:
    return [_bar(start + timedelta(minutes=interval * i), high, low, close) for i in range(n)]


def test_history_interval_maps_two_hours_to_openalgo_token():
    assert history_interval(120) == "2h"
    assert history_interval(15) == "15m"


def test_ema_seeds_from_the_sma_then_recurses():
    values = [float(i) for i in range(1, 6)]
    series = ema_series(values, 3)
    seed = (1.0 + 2.0 + 3.0) / 3.0
    alpha = 2.0 / 4.0
    assert series[0] == pytest.approx(seed)
    assert series[1] == pytest.approx(4.0 * alpha + seed * (1.0 - alpha))
    assert len(series) == 3


def test_the_forming_bar_is_dropped_and_the_closed_bar_fires():
    start = datetime(2026, 9, 1, 9, 15, tzinfo=IST)
    closed = _ladder(21, start=start, high=25000.0, low=24800.0, close=25100.0)
    forming = _bar(start + timedelta(minutes=120 * 21), 26000.0, 24000.0, 25900.0)
    now = forming.start + timedelta(minutes=30)
    assert len(closed_bars(closed + [forming], now, 120)) == 21
    signal = signal_from_bars(closed + [forming], length=21, now=now, interval_minutes=120)
    assert signal is not None
    assert signal.side == "long"
    assert signal.close == 25100.0
    assert signal.bar_start == closed[-1].start


def test_close_below_the_low_band_is_a_short():
    start = datetime(2026, 9, 1, 9, 15, tzinfo=IST)
    bars = _ladder(21, start=start, high=25100.0, low=24900.0, close=24700.0)
    now = bars[-1].start + timedelta(minutes=120)
    signal = signal_from_bars(bars, length=21, now=now, interval_minutes=120)
    assert signal is not None
    assert signal.side == "short"


def test_close_between_the_bands_is_flat():
    start = datetime(2026, 9, 1, 9, 15, tzinfo=IST)
    bars = _ladder(21, start=start, high=25100.0, low=24900.0, close=25000.0)
    now = bars[-1].start + timedelta(minutes=120)
    signal = signal_from_bars(bars, length=21, now=now, interval_minutes=120)
    assert signal is not None
    assert signal.side is None


def test_trailer_round_trips_and_band_exit_follows_pine():
    text = format_trailer("long", 25110.25, 24880.5)
    exits = parse_trailer(f"ATM CE. {text}")
    assert exits == EmaExits(side="long", ema_high=25110.25, ema_low=24880.5)
    assert band_exit(exits, high=25150.0, low=24870.0, ltp=25000.0) is True
    assert band_exit(exits, high=25150.0, low=24890.0, ltp=25000.0) is False
    short = parse_trailer(format_trailer("short", 25110.25, 24880.5))
    assert short is not None
    assert band_exit(short, high=25120.0, low=24800.0, ltp=None) is True
    assert band_exit(short, high=None, low=None, ltp=25120.0) is True


def test_overlay_still_binds_a_weak_adx(settings: Settings):
    assert settings.playbook_mode == "overlay"
    state = {
        "book": {"open_positions": []},
        "regime_label": "trending",
        "regime_confidence": 0.80,
        "trend_snapshot": WEAK_ADX,
    }
    assert _wants_a_contract(state, settings) is False
    outcome, code, _text = _decide_outcome(state, settings)
    assert (outcome, code) == ("decline", "regime-weak-trend")


def test_ema120_reaches_the_strategist_with_a_weak_adx(settings: Settings):
    settings.playbook_mode = "ema120"
    state = {
        "book": {"open_positions": []},
        "regime_label": "trending",
        "regime_confidence": 1.0,
        "trend_snapshot": WEAK_ADX,
        "ema_signal": {
            "side": "long",
            "ema_high": 25010.0,
            "ema_low": 24820.0,
            "close": 25040.0,
        },
        "ema_no_signal": False,
    }
    assert _wants_a_contract(state, settings) is True
    outcome, code, _text = _decide_outcome(state, settings)
    assert code != "regime-weak-trend"
    assert code == "specialist-unavailable"


def test_ema120_side_selects_ce_or_pe():
    req = SpecialistRequest(
        tick_id="t",
        index_symbol="NIFTY",
        as_of=datetime(2026, 9, 8, 11, 30, tzinfo=IST),
        book={"ema120": {"side": "long", "ema_high": 1.0, "ema_low": 0.5}},
    )
    assert direction_from_regime(req) == ("bullish", "CE")
    req.book["ema120"]["side"] = "short"
    assert direction_from_regime(req) == ("bearish", "PE")


def test_ema120_playbook_skips_the_same_day_time_stop(settings: Settings):
    book = Playbook.from_settings(
        settings.model_copy(update={"playbook_mode": "ema120", "expiry_cutoff": "14:00"})
    )
    assert book.time_stop == "14:00"
    rationale = "EMA120 ATM CE. [ema120] side=long high=25100.00 low=24800.00"
    violations = check(
        valid_proposal(time_stop_ist="09:00", rationale=rationale),
        book,
        now_ist=FIXED_NOW,
    )
    assert not any("not in the future" in item for item in violations)


def test_ema120_intent_is_nrml(execution_settings: Settings):
    execution_settings.playbook_mode = "ema120"
    intent = build_intent(cleared_context(), execution_settings, datetime.now(tz=UTC))
    assert intent.product == "NRML"
    assert intent.payload()["product"] == "NRML"


def test_consult_skips_the_regime_specialist_and_calls_the_strategist(
    settings: Settings, runner, deps, openalgo, journal
):
    settings.playbook_mode = "ema120"
    start = datetime(2026, 9, 1, 9, 15, tzinfo=IST)
    bars = [
        {
            "timestamp": (start + timedelta(minutes=120 * i)).isoformat(),
            "high": 25000.0,
            "low": 24800.0,
            "close": 25100.0,
            "open": 24950.0,
        }
        for i in range(24)
    ]
    openalgo.post("/api/v1/history").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": bars})
    )
    captured: list[SpecialistRequest] = []

    class Capture(StubSpecialist):
        def __init__(self) -> None:
            super().__init__(role=ROLE_STRATEGIST, payload={"status": "degraded", "defect": "x"})

        def run(self, request):
            captured.append(request)
            return SpecialistResult(role=self.role, payload=self._payload)

    deps.registry.register(Capture())
    with freeze_time("2026-09-08T06:00:00+00:00"):
        runner.run_tick("schedule")
        decisions = journal.list_decisions("2026-09-08")
    assert captured, "the EMA book must reach the strategist"
    ema = captured[0].book.get("ema120") or {}
    assert ema.get("side") == "long"
    assert decisions
    assert decisions[0].reason_code != "regime-weak-trend"
