from datetime import date

from strike_desk.coa_archive import lookback_window, strike_band, weeklies_to_archive


def test_lookback_window_includes_today():
    start, end = lookback_window(date(2026, 10, 29), days=3)
    assert start == date(2026, 10, 26)
    assert end == date(2026, 10, 29)


def test_weeklies_keep_front_week_only():
    listed = [date(2026, 11, 3), date(2026, 11, 9), date(2026, 11, 17)]
    assert weeklies_to_archive(date(2026, 10, 29), listed) == [date(2026, 11, 3)]


def test_weeklies_keep_next_week_on_expiry_day():
    listed = [date(2026, 11, 3), date(2026, 11, 9)]
    assert weeklies_to_archive(date(2026, 11, 3), listed) == [
        date(2026, 11, 3),
        date(2026, 11, 9),
    ]


def test_weeklies_empty_when_master_has_nothing_ahead():
    assert weeklies_to_archive(date(2026, 10, 29), [date(2026, 10, 6)]) == []


def test_strike_band_pads_ten_strikes():
    assert strike_band(22610.0, 22740.0) == (22100.0, 23250.0)


def test_strike_band_uses_banknifty_step():
    assert strike_band(54700.0, 55200.0, step=100.0) == (53700.0, 56200.0)


def test_monthly_front_expiry_is_nearest_listed():
    listed = [date(2026, 11, 25), date(2026, 12, 30)]
    assert weeklies_to_archive(date(2026, 10, 29), listed) == [date(2026, 11, 25)]
