"""Lab next-hour readout stays number-locked."""

from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.greeks_lab_narrate import attach_readout


def _payload(**overrides):
    base = {
        "underlying": "NIFTY",
        "horizon_minutes": 60,
        "projections": {
            "ce": {
                "theta_path": [
                    {"minute": 0, "price": 108.2},
                    {"minute": 60, "price": 107.8},
                ]
            },
            "pe": {
                "theta_path": [
                    {"minute": 0, "price": 137.1},
                    {"minute": 60, "price": 136.6},
                ]
            },
        },
        "insights": {
            "decay": {"state": "off"},
            "quadshield": {"state": "pause_shorts"},
            "session": {"state": "buying_day"},
        },
    }
    base.update(overrides)
    return base


def test_template_says_wait_when_decay_is_off():
    out = attach_readout(_payload(), invoke=lambda _s, _u: "no")
    line = out["readout"]["line"]
    assert "108.2" in line and "107.8" in line
    assert "not to sell premium" in line.lower() or "wait" in line.lower()
    assert "Not an order" in line
    assert out["readout"]["source"] == "template"


def test_llm_rewrite_kept_when_grounded():
    rewrite = (
        "If NIFTY does not move for 60 min, the call goes 108.2 to 107.8 "
        "(loses ₹0.4) and the put stays near 137.1. Wait — do not sell premium. "
        "Not an order."
    )
    out = attach_readout(_payload(), invoke=lambda _s, _u: rewrite)
    assert out["readout"]["source"] == "llm"
    assert out["readout"]["line"].startswith("If NIFTY")


def test_llm_invented_number_is_rejected():
    out = attach_readout(
        _payload(),
        invoke=lambda _s, _u: "The call will lose ₹50 in 60 min. Not an order.",
    )
    assert out["readout"]["source"] == "template"
    assert "₹50" not in out["readout"]["line"]


def test_selling_day_map_lets_premium_shrink():
    payload = _payload(
        insights={
            "decay": {"state": "on"},
            "quadshield": {"state": "run"},
            "session": {"state": "selling_day"},
        }
    )
    out = attach_readout(payload, invoke=lambda _s, _u: "nope")
    assert "let premium shrink" in out["readout"]["line"]
