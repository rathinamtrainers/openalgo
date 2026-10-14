from datetime import datetime, time
from types import SimpleNamespace

from pydantic import SecretStr

from strike_desk.config import IST
from strike_desk.strikeshield import (
    Indicators,
    ShieldState,
    StrikeShieldRunner,
    adx_last,
    apply_actions,
    atr_series,
    decide,
    short_exit_reason,
)
from strike_desk.tradetron_hands import BUY_WINGS, EXIT_ALL, EXIT_SHORTS, SELL_SHORTS


def _now(hhmm: str) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime(2026, 10, 14, hour, minute, tzinfo=IST)


def _ind(*, spot=24500.0, atr=20.0, prev_atr=19.0, adx=25.0, vix=14.0) -> Indicators:
    return Indicators(spot=spot, atr=atr, prev_atr=prev_atr, adx=adx, vix=vix)


def test_wings_fire_after_915_when_flat(settings):
    actions = decide(_now("09:16"), _ind(), ShieldState(), settings, is_expiry_day=False)
    assert actions == [BUY_WINGS]


def test_wings_blocked_on_expiry_after_1420(settings):
    actions = decide(_now("14:25"), _ind(), ShieldState(), settings, is_expiry_day=True)
    assert actions == []


def test_expiry_flat_exits_everything(settings):
    state = ShieldState(set1=True, set2open=True, eup=24500.0)
    actions = decide(_now("14:31"), _ind(), state, settings, is_expiry_day=True)
    assert actions == [EXIT_ALL]


def test_shorts_need_wings_adx_and_vix(settings):
    state = ShieldState(set1=True)
    assert decide(_now("09:20"), _ind(adx=25, vix=14), state, settings, is_expiry_day=False) == [
        SELL_SHORTS
    ]
    assert decide(_now("09:20"), _ind(adx=41, vix=14), state, settings, is_expiry_day=False) == []
    assert decide(_now("09:20"), _ind(adx=25, vix=10), state, settings, is_expiry_day=False) == []


def test_short_exit_on_atr_multiple(settings):
    state = ShieldState(set1=True, set2open=True, eup=24500.0)
    reason = short_exit_reason(time(10, 0), _ind(spot=24580, atr=20), state, settings)
    assert reason and reason.startswith("spot move")
    actions = decide(_now("10:00"), _ind(spot=24580, atr=20), state, settings, is_expiry_day=False)
    assert actions == [EXIT_SHORTS]


def test_apply_actions_latches_set1_and_eup(settings):
    now = _now("09:20")
    state = apply_actions(ShieldState(), [BUY_WINGS], now, 24500.0)
    assert state.set1 is True
    state = apply_actions(state, [SELL_SHORTS], now, 24510.0)
    assert state.set2open is True
    assert state.eup == 24510.0


def test_atr_and_adx_from_synthetic_trend():
    closes = [100.0 + i for i in range(80)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    atrs = atr_series(highs, lows, closes, 14)
    assert atrs and atrs[-1] > 0
    assert adx_last(highs, lows, closes, 15) > 20


class _FakeHands:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, action: str):
        self.sent.append(action)
        return SimpleNamespace(action=action, http_status=200, body="Ok")


def _session(now: datetime):
    start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return start, end


def test_runner_sends_wings_then_persists(settings, monkeypatch):
    settings.tt_auth_token = SecretStr("token-aaaaaaaa")
    hands = _FakeHands()
    now = _now("09:16")
    runner = StrikeShieldRunner(
        settings,
        client=SimpleNamespace(),
        hands=hands,
        is_trading_day=lambda _day: True,
        expiry_date_for=lambda _day: None,
        trading_window=lambda _day: _session(now),
    )
    monkeypatch.setattr(
        "strike_desk.strikeshield.read_indicators",
        lambda *_args, **_kwargs: _ind(),
    )
    sent = runner.step(now)
    assert sent == [BUY_WINGS]
    assert hands.sent == [BUY_WINGS]
    persisted = settings.strikeshield_state_path.read_text(encoding="utf-8")
    assert '"set1": true' in persisted


def test_runner_respects_kill_switch(settings):
    settings.kill_switch_path.write_text("stop", encoding="utf-8")
    runner = StrikeShieldRunner(
        settings,
        client=SimpleNamespace(),
        hands=_FakeHands(),
        is_trading_day=lambda _day: True,
        expiry_date_for=lambda _day: None,
    )
    assert runner.step(_now("09:20")) == []


def test_runner_skips_outside_nfo_hours(settings, monkeypatch):
    now = _now("20:23")
    hands = _FakeHands()
    runner = StrikeShieldRunner(
        settings,
        client=SimpleNamespace(),
        hands=hands,
        is_trading_day=lambda _day: True,
        expiry_date_for=lambda _day: None,
        trading_window=lambda _day: _session(now),
    )
    monkeypatch.setattr(
        "strike_desk.strikeshield.read_indicators",
        lambda *_args, **_kwargs: _ind(),
    )
    assert runner.step(now) == []
    assert hands.sent == []
