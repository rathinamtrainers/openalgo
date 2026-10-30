"""QuadShield-style desk read on Greeks Lab."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.greeks_lab_insights import build_desk_insights

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 30, 12, 30, tzinfo=IST)
CE = {"ltp": 90.0, "theta": -6.0, "iv": 12.0, "days_to_expiry": 4}
PE = {"ltp": 92.0, "theta": -6.2, "iv": 12.4}


def _base(**extra):
    payload = {
        "now": NOW,
        "spot": 23000.0,
        "atm_iv": 12.0,
        "ce": CE,
        "pe": PE,
        "adx": 22.0,
        "adx_prev": 24.0,
        "plus_di": 18.0,
        "minus_di": 16.0,
        "atr": 40.0,
        "prev_atr": 39.0,
        "vix": 13.5,
        "day_high": 23040.0,
        "day_low": 22980.0,
        "is_expiry": False,
        "dte": 4.0,
    }
    payload.update(extra)
    return build_desk_insights(**payload)


def test_quiet_range_is_selling_day_and_run():
    out = _base()
    assert out["decay"]["state"] == "on"
    assert out["session"]["state"] == "selling_day"
    assert out["quadshield"]["state"] == "run"
    assert out["trend"]["state"] == "fading"


def test_high_adx_pauses_shorts_and_marks_buying_day():
    out = _base(adx=48.0, adx_prev=42.0, plus_di=32.0, minus_di=12.0, day_high=23200.0)
    assert out["decay"]["state"] == "off"
    assert out["quadshield"]["state"] == "pause_shorts"
    assert out["session"]["state"] == "buying_day"
    assert out["trend"]["state"] == "stronger"


def test_vix_outside_band_pauses_all():
    out = _base(vix=9.5)
    assert out["quadshield"]["state"] == "pause"


def test_expiry_afternoon_flattens():
    out = _base(now=datetime(2026, 10, 30, 14, 35, tzinfo=IST), is_expiry=True)
    assert out["quadshield"]["state"] == "pause"
    assert "14:30" in out["quadshield"]["detail"]
