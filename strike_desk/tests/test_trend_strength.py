"""ADX/DMI floors for a NIFTY premium buy. Pure arithmetic, no tools."""

from __future__ import annotations

from strike_desk.trend_strength import assess_trend_strength, trend_snapshot

# Host 28 Sep 2026 enter tick: trending bearish, later the PE went green.
FILL_28_SEP = trend_snapshot((11.06, 35.62, 33.46), (14.24, 33.39, 41.31))


def test_a_missing_snapshot_is_not_assessed(settings):
    assert assess_trend_strength(None, settings).ok is True
    assert assess_trend_strength({}, settings).ok is True


def test_the_28_sep_fill_still_clears_the_floors(settings):
    verdict = assess_trend_strength(FILL_28_SEP, settings)
    assert verdict.ok is True


def test_daily_adx_below_25_is_too_weak_to_buy_premium(settings):
    snap = trend_snapshot((12.4, 28.3, 18.2), (14.0, 22.0, 24.0))
    verdict = assess_trend_strength(snap, settings)
    assert verdict.ok is False
    assert "daily ADX 18.2" in verdict.detail
    assert "25" in verdict.detail


def test_a_coin_flip_di_spread_is_not_a_trend(settings):
    snap = trend_snapshot((20.0, 24.0, 28.0), (18.0, 25.0, 22.0))
    verdict = assess_trend_strength(snap, settings)
    assert verdict.ok is False
    assert "DI spread 4" in verdict.detail


def test_10m_must_agree_with_the_daily_side(settings):
    snap = trend_snapshot((12.0, 28.0, 30.0), (28.0, 12.0, 24.0))
    verdict = assess_trend_strength(snap, settings)
    assert verdict.ok is False
    assert "disagrees" in verdict.detail


def test_10m_adx_below_20_has_no_entry_impulse(settings):
    snap = trend_snapshot((12.0, 28.0, 30.0), (14.0, 22.0, 16.4))
    verdict = assess_trend_strength(snap, settings)
    assert verdict.ok is False
    assert "10m ADX 16.4" in verdict.detail


def test_unread_daily_adx_fails_closed(settings):
    snap = trend_snapshot((12.0, 28.0, None), (14.0, 22.0, 24.0))
    verdict = assess_trend_strength(snap, settings)
    assert verdict.ok is False
    assert "not readable" in verdict.detail
