"""Believable OpenAlgo tool payloads, small enough to read in a diff."""

from __future__ import annotations

import json
from typing import Any


def quote(ltp: float, oi: int = 0, volume: int = 0) -> str:
    return json.dumps(
        {
            "status": "success",
            "data": {
                "ltp": ltp,
                "open": round(ltp * 0.996, 2),
                "high": round(ltp * 1.004, 2),
                "low": round(ltp * 0.994, 2),
                "prev_close": round(ltp * 0.998, 2),
                "volume": volume,
                "oi": oi,
            },
        },
        indent=2,
    )


def trend(last_close: float, sma_20: float, sma_50: float, adx: float, direction: int) -> str:
    return json.dumps(
        {
            "symbol": "NIFTY",
            "exchange": "NSE_INDEX",
            "interval": "15m",
            "bars_loaded": 252,
            "last_close": last_close,
            "indicators": {
                "sma_20": sma_20,
                "sma_50": sma_50,
                "ema_20": round((sma_20 + last_close) / 2, 2),
                "supertrend": [round(last_close * 0.995, 2), direction],
                "adx_di": [22.4, 18.1, adx],
            },
            "legend": {"adx_di": "[+DI, -DI, ADX]"},
        },
        indent=2,
    )


def momentum(rsi: float, macd_hist: float) -> str:
    return json.dumps(
        {
            "symbol": "NIFTY",
            "interval": "15m",
            "indicators": {
                "rsi_14": rsi,
                "macd": [12.4, 9.8, macd_hist],
                "stochastic": [61.2, 58.7],
                "cci_20": 74.3,
            },
        },
        indent=2,
    )


def volatility(atr: float, bb_width: float, hv: float) -> str:
    return json.dumps(
        {
            "symbol": "NIFTY",
            "interval": "15m",
            "indicators": {
                "atr_14": atr,
                "natr_14": round(atr / 245.0, 3),
                "bb_width": bb_width,
                "historical_volatility": hv,
            },
        },
        indent=2,
    )


def expiries(dates: list[str]) -> str:
    return json.dumps({"status": "success", "data": dates}, indent=2)


TREND_SNAPSHOT: dict[str, Any] = {
    "get_quote": quote(24512.35),
    "get_trend_snapshot": trend(24512.35, 24380.10, 24105.60, 31.7, 1),
    "get_momentum_snapshot": momentum(63.8, 4.6),
    "get_volatility_snapshot": volatility(88.4, 2.1, 11.9),
    "get_expiry_dates": expiries(["26AUG26", "24SEP26"]),
    "get_historical_data": json.dumps({"count": 60, "returned": 5, "data": []}),
}
