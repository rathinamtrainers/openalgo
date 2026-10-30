"""Realtime Greeks lab: live chain, theta path, and Black-76 what-if.

Projections hold implied vol unless the what-if grid shocks it. They are a
model revaluation, not a forecast of where spot or IV will go.
"""

from __future__ import annotations

from typing import Any

from services.greeks_lab_insights import collect_desk_insights
from services.greeks_lab_narrate import attach_readout
from services.option_chain_service import get_option_chain
from services.option_greeks_service import calculate_greeks
from utils.logging import get_logger

logger = get_logger(__name__)

MINUTES_PER_YEAR = 365.0 * 24.0 * 60.0
SPOT_SHOCKS_PCT = (-1.0, -0.5, 0.0, 0.5, 1.0)
IV_SHOCKS_PTS = (-2.0, 0.0, 2.0)
PATH_STEP_MINUTES = 5
DEFAULT_HORIZON_MINUTES = 60
MAX_HORIZON_MINUTES = 240
CHAIN_STRIKE_COUNT = 8


def _options_exchange(exchange: str) -> str:
    code = (exchange or "").upper()
    if code in ("NSE_INDEX", "NSE"):
        return "NFO"
    if code in ("BSE_INDEX", "BSE"):
        return "BFO"
    return code


def _model_price(
    flag: str,
    forward: float,
    strike: float,
    years: float,
    rate: float,
    iv_decimal: float,
) -> float | None:
    if forward <= 0 or strike <= 0 or iv_decimal <= 0:
        return None
    try:
        from opengreeks.black76 import black
    except ImportError:
        return None
    try:
        return float(black(flag, forward, strike, max(years, 1e-6), rate, max(iv_decimal, 1e-6)))
    except Exception:
        logger.exception("Black-76 reprice failed")
        return None


def theta_path(
    flag: str,
    forward: float,
    strike: float,
    years: float,
    rate: float,
    iv_decimal: float,
    horizon_minutes: int,
) -> list[dict[str, float]]:
    """Hold spot and IV; only calendar time decays."""
    points: list[dict[str, float]] = []
    for minute in range(0, horizon_minutes + 1, PATH_STEP_MINUTES):
        remain = years - (minute / MINUTES_PER_YEAR)
        price = _model_price(flag, forward, strike, remain, rate, iv_decimal)
        if price is None:
            continue
        points.append({"minute": float(minute), "price": round(price, 2)})
    return points


def whatif_grid(
    flag: str,
    forward: float,
    strike: float,
    years: float,
    rate: float,
    iv_decimal: float,
    horizon_minutes: int,
) -> list[list[float | None]]:
    """Rows = spot shocks, columns = IV shocks, values = model price at horizon."""
    remain = years - (horizon_minutes / MINUTES_PER_YEAR)
    rows: list[list[float | None]] = []
    for spot_pct in SPOT_SHOCKS_PCT:
        shocked_fwd = forward * (1.0 + spot_pct / 100.0)
        row: list[float | None] = []
        for iv_pts in IV_SHOCKS_PTS:
            shocked_iv = iv_decimal + (iv_pts / 100.0)
            price = _model_price(flag, shocked_fwd, strike, remain, rate, shocked_iv)
            row.append(None if price is None else round(price, 2))
        rows.append(row)
    return rows


def _leg_from_greeks(
    symbol: str,
    ltp: float,
    greeks_resp: dict[str, Any],
) -> dict[str, Any]:
    greeks = greeks_resp.get("greeks") or {}
    return {
        "symbol": symbol,
        "ltp": round(ltp, 2),
        "iv": greeks_resp.get("implied_volatility"),
        "delta": greeks.get("delta"),
        "gamma": greeks.get("gamma"),
        "theta": greeks.get("theta"),
        "vega": greeks.get("vega"),
        "days_to_expiry": greeks_resp.get("days_to_expiry"),
    }


def _project_side(
    flag: str,
    forward: float,
    strike: float,
    years: float,
    iv_pct: float | None,
    horizon_minutes: int,
) -> dict[str, Any]:
    iv_decimal = 0.0 if iv_pct is None else float(iv_pct) / 100.0
    if iv_decimal <= 0 or years <= 0:
        return {"theta_path": [], "whatif": []}
    return {
        "theta_path": theta_path(flag, forward, strike, years, 0.0, iv_decimal, horizon_minutes),
        "whatif": whatif_grid(flag, forward, strike, years, 0.0, iv_decimal, horizon_minutes),
    }


def get_greeks_lab_data(
    underlying: str,
    exchange: str,
    expiry_date: str,
    api_key: str,
    *,
    strike: float | None = None,
    horizon_minutes: int = DEFAULT_HORIZON_MINUTES,
) -> tuple[bool, dict[str, Any], int]:
    """Live chain Greeks plus a 1-hour (default) Black-76 projection."""
    try:
        horizon = int(horizon_minutes or DEFAULT_HORIZON_MINUTES)
        horizon = max(PATH_STEP_MINUTES, min(horizon, MAX_HORIZON_MINUTES))

        success, chain_response, status_code = get_option_chain(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            strike_count=CHAIN_STRIKE_COUNT,
            api_key=api_key,
        )
        if not success:
            return False, chain_response, status_code

        full_chain = chain_response.get("chain") or []
        atm_strike = chain_response.get("atm_strike")
        spot_price = chain_response.get("underlying_ltp")
        if not spot_price or spot_price <= 0:
            return False, {"status": "error", "message": "Could not determine spot price"}, 500
        if not full_chain:
            return False, {"status": "error", "message": "Empty option chain"}, 404

        options_exchange = _options_exchange(exchange)
        rows: list[dict[str, Any]] = []
        lot_size = None

        for item in full_chain:
            row_strike = item.get("strike")
            if row_strike is None:
                continue
            ce = item.get("ce") or {}
            pe = item.get("pe") or {}
            out: dict[str, Any] = {"strike": row_strike, "ce": None, "pe": None}

            for side, bag in (("ce", ce), ("pe", pe)):
                symbol = bag.get("symbol")
                ltp = bag.get("ltp") or 0
                current_lot = bag.get("lotsize") or 1
                if lot_size is None and current_lot:
                    lot_size = current_lot
                if not symbol or ltp <= 0:
                    continue
                try:
                    ok, greeks_resp, _status = calculate_greeks(
                        option_symbol=symbol,
                        exchange=options_exchange,
                        spot_price=spot_price,
                        option_price=ltp,
                    )
                except Exception:
                    logger.exception("Greeks failed for %s", symbol)
                    continue
                if ok and greeks_resp.get("status") == "success":
                    out[side] = _leg_from_greeks(symbol, float(ltp), greeks_resp)
            rows.append(out)

        strikes = [row["strike"] for row in rows]
        if not strikes:
            return False, {"status": "error", "message": "No priced strikes on this chain"}, 404

        selected = atm_strike if strike is None else float(strike)
        selected = min(strikes, key=lambda value: abs(value - selected))
        watched = next(row for row in rows if row["strike"] == selected)

        atm_iv = None
        atm_row = next((row for row in rows if row["strike"] == atm_strike), watched)
        for side in ("ce", "pe"):
            leg = atm_row.get(side) if atm_row else None
            if isinstance(leg, dict) and leg.get("iv"):
                atm_iv = float(leg["iv"])
                break

        hour_fraction = horizon / MINUTES_PER_YEAR
        expected_move = None
        if atm_iv and atm_iv > 0:
            expected_move = round(spot_price * (atm_iv / 100.0) * (hour_fraction**0.5), 2)

        insights: dict[str, Any] | None = None
        try:
            atm_ce = atm_row.get("ce") if isinstance(atm_row, dict) else None
            atm_pe = atm_row.get("pe") if isinstance(atm_row, dict) else None
            insights = collect_desk_insights(
                underlying=underlying,
                exchange=exchange,
                expiry_date=expiry_date,
                api_key=api_key,
                spot=float(spot_price),
                atm_iv=atm_iv,
                atm_ce=atm_ce if isinstance(atm_ce, dict) else None,
                atm_pe=atm_pe if isinstance(atm_pe, dict) else None,
            )
        except Exception:
            logger.exception("Greeks lab desk insights failed")

        projections: dict[str, Any] = {}
        for side, flag in (("ce", "c"), ("pe", "p")):
            leg = watched.get(side)
            if not isinstance(leg, dict):
                projections[side] = {"theta_path": [], "whatif": []}
                continue
            days = float(leg.get("days_to_expiry") or 0)
            years = days / 365.0 if days > 0 else 0.0
            projections[side] = _project_side(
                flag,
                float(spot_price),
                float(selected),
                years,
                leg.get("iv"),
                horizon,
            )

        payload = {
            "status": "success",
            "underlying": underlying,
            "exchange": exchange,
            "expiry_date": expiry_date,
            "spot_price": round(float(spot_price), 2),
            "atm_strike": atm_strike,
            "selected_strike": selected,
            "lot_size": lot_size or 1,
            "horizon_minutes": horizon,
            "atm_iv": atm_iv,
            "expected_move": expected_move,
            "spot_shocks_pct": list(SPOT_SHOCKS_PCT),
            "iv_shocks_pts": list(IV_SHOCKS_PTS),
            "disclaimer": (
                "Next-hour prices are Black-76 revaluations that hold today's IV "
                "unless the what-if grid shocks it. Not a forecast."
            ),
            "insights": insights,
            "watched": watched,
            "projections": projections,
            "chain": rows,
        }
        attach_readout(payload)
        return True, payload, 200
    except Exception as exc:
        logger.exception("Greeks lab failed: %s", exc)
        return (
            False,
            {"status": "error", "message": "Failed to build Greeks lab snapshot"},
            500,
        )
