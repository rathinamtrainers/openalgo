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
    load_state,
    save_state,
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


def test_short_exit_ignores_sub_threshold_chop(settings):
    state = ShieldState(set1=True, set2open=True, eup=24500.0)
    # 80 pts used to fire (3.5× 5m ATR). That lunch chop is why today's roll paid round-trip.
    assert short_exit_reason(time(10, 0), _ind(spot=24580, atr=20), state, settings) is None
    assert decide(_now("10:00"), _ind(spot=24580, atr=20), state, settings, is_expiry_day=False) == []


def test_short_exit_on_min_spot_move(settings):
    state = ShieldState(set1=True, set2open=True, eup=24500.0)
    reason = short_exit_reason(time(10, 0), _ind(spot=24700, atr=20), state, settings)
    assert reason and reason.startswith("spot move")
    actions = decide(_now("10:00"), _ind(spot=24700, atr=20), state, settings, is_expiry_day=False)
    assert actions == [EXIT_SHORTS]


def test_short_exit_skips_after_daily_cap(settings):
    state = ShieldState(set1=True, set2open=True, eup=24500.0, adjustments_today=1)
    actions = decide(_now("10:00"), _ind(spot=24700, atr=20), state, settings, is_expiry_day=False)
    assert actions == []


def test_session_window_50pt_no_longer_exits(settings):
    state = ShieldState(set1=True, set2open=True, eup=24500.0)
    assert short_exit_reason(time(9, 20), _ind(spot=24560, atr=20), state, settings) is None
    assert decide(_now("09:20"), _ind(spot=24560, atr=20), state, settings, is_expiry_day=False) == []


def test_apply_actions_latches_set1_and_eup(settings):
    now = _now("09:20")
    state = apply_actions(ShieldState(), [BUY_WINGS], now, 24500.0)
    assert state.set1 is True
    state = apply_actions(state, [SELL_SHORTS], now, 24510.0)
    assert state.set2open is True
    assert state.eup == 24510.0
    state = apply_actions(state, [EXIT_SHORTS], now, 24510.0)
    assert state.set2open is False
    assert state.adjustments_today == 1
    assert state.rollovers == 1


def test_load_state_keeps_positional_book_across_midnight(settings):
    yesterday = ShieldState(
        set1=True,
        set2open=True,
        eup=22700.0,
        date="2026-10-13",
        rollovers=2,
        adjustments_today=1,
    )
    save_state(settings.strikeshield_state_path, yesterday)
    loaded = load_state(settings.strikeshield_state_path, "2026-10-14")
    assert loaded.set1 is True
    assert loaded.set2open is True
    assert loaded.eup == 22700.0
    assert loaded.rollovers == 2
    assert loaded.adjustments_today == 0
    assert loaded.date == "2026-10-14"


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
    now = _now("09:20")
    assert runner.step(now) == []
    assert settings.strikeshield_heartbeat_path.exists()


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
    assert settings.strikeshield_heartbeat_path.exists()
