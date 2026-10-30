"""Greeks Lab: Black-76 theta path and what-if grid."""

from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opengreeks.black76 import black

from services.greeks_lab_service import (
    IV_SHOCKS_PTS,
    SPOT_SHOCKS_PCT,
    get_greeks_lab_data,
    theta_path,
    whatif_grid,
)


def test_theta_path_decays_for_atm_call():
    path = theta_path("c", 23000.0, 23000.0, 25 / 365.0, 0.0, 0.12, 60)
    assert path[0]["minute"] == 0
    assert path[-1]["minute"] == 60
    assert path[0]["price"] > path[-1]["price"]


def test_whatif_grid_higher_spot_lifts_call():
    grid = whatif_grid("c", 23000.0, 23000.0, 25 / 365.0, 0.0, 0.12, 60)
    assert len(grid) == len(SPOT_SHOCKS_PCT)
    assert len(grid[0]) == len(IV_SHOCKS_PTS)
    down = grid[0][1]
    flat = grid[2][1]
    up = grid[-1][1]
    assert down is not None and flat is not None and up is not None
    assert down < flat < up


def test_greeks_lab_snapshot_uses_live_chain(monkeypatch):
    ltp = float(black("c", 23000.0, 23000.0, 25 / 365.0, 0.0, 0.12))

    def fake_chain(**_kwargs):
        return (
            True,
            {
                "chain": [
                    {
                        "strike": 23000.0,
                        "ce": {
                            "symbol": "NIFTY03NOV2623000CE",
                            "ltp": ltp,
                            "lotsize": 65,
                            "oi": 1,
                        },
                        "pe": {
                            "symbol": "NIFTY03NOV2623000PE",
                            "ltp": ltp,
                            "lotsize": 65,
                            "oi": 1,
                        },
                    }
                ],
                "atm_strike": 23000.0,
                "underlying_ltp": 23000.0,
            },
            200,
        )

    monkeypatch.setattr("services.greeks_lab_service.get_option_chain", fake_chain)
    monkeypatch.setattr(
        "services.greeks_lab_service.collect_desk_insights",
        lambda **_kwargs: {
            "decay": {"state": "on", "label": "Decay ON", "detail": "test"},
            "quadshield": {"state": "run", "label": "QuadShield RUN", "detail": "test"},
            "session": {"state": "selling_day", "label": "Selling day", "detail": "test"},
            "trend": {"state": "sideways", "label": "Sideways", "detail": "test"},
        },
    )
    monkeypatch.setattr("services.greeks_lab_narrate._default_invoke", lambda: None)
    ok, payload, status = get_greeks_lab_data("NIFTY", "NFO", "03NOV26", api_key="test")
    assert ok is True
    assert status == 200
    assert payload["status"] == "success"
    assert payload["selected_strike"] == 23000.0
    assert payload["watched"]["ce"]["ltp"] > 0
    assert payload["projections"]["ce"]["theta_path"]
    assert payload["insights"]["decay"]["state"] == "on"
    assert payload["readout"]["line"]
    assert "Not an order" in payload["readout"]["line"]
    assert "Not a forecast" in payload["disclaimer"]
