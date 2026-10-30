"""Delta Exchange India public option chain. No account key.

GCE OpenAlgo is a Zerodha session, so CRYPTO optionchain is not available
there. Tickers are public: GET /v2/tickers. Never posts orders.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

import httpx

from .errors import OpenAlgoError

logger = logging.getLogger(__name__)

DELTA_INDIA = "https://api.india.delta.exchange"
_SYMBOL = re.compile(
    r"^(?P<kind>[CP])-(?P<under>[A-Z]+)-(?P<strike>[0-9]+(?:\.[0-9]+)?)-(?P<exp>\d{6})$"
)


class DeltaIndiaChain:
    """OpenAlgoClient-shaped optionchain() over Delta India's public REST."""

    def __init__(
        self,
        *,
        timeout: float = 8.0,
        client: httpx.Client | None = None,
        strike_step: float = 200.0,
    ) -> None:
        self._strike_step = strike_step
        self._owned = client is None
        self._client = client or httpx.Client(
            base_url=DELTA_INDIA,
            timeout=httpx.Timeout(timeout),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            headers={
                "User-Agent": "strike-desk-coa-btc/0.1",
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        if self._owned:
            self._client.close()

    def optionchain(
        self,
        underlying: str,
        exchange: str,
        expiry_date: str,
        strike_count: int = 10,
    ) -> dict[str, Any]:
        del exchange
        try:
            response = self._client.get(
                "/v2/tickers",
                params={
                    "contract_types": "call_options,put_options",
                    "underlying_asset_symbols": underlying.upper(),
                },
            )
        except httpx.HTTPError as exc:
            raise OpenAlgoError(f"delta india tickers failed: {exc}") from exc
        if response.status_code >= 400:
            raise OpenAlgoError(
                f"delta india tickers http {response.status_code}",
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise OpenAlgoError("delta india tickers were not JSON") from exc
        tickers = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(tickers, list):
            raise OpenAlgoError("delta india tickers: 'result' was not a list")
        wanted = _expiry_from_token(expiry_date)
        return chain_from_tickers(
            tickers,
            underlying=underlying.upper(),
            expiry=wanted,
            strike_count=strike_count,
            strike_step=self._strike_step,
        )


def chain_from_tickers(
    tickers: list[Any],
    *,
    underlying: str,
    expiry: date | None,
    strike_count: int = 10,
    strike_step: float | None = None,
) -> dict[str, Any]:
    """Group call/put tickers into OpenAlgo optionchain rows.

    Keep an ATM window (same idea as OpenAlgo ``strike_count``). CoA walks every
    strike at or beyond ATM; a full BTC ladder would pin support on far OI.
    """
    by_expiry: dict[date, dict[float, dict[str, Any]]] = {}
    spot_by_strike: dict[float, float] = {}
    for raw in tickers:
        if not isinstance(raw, dict):
            continue
        parsed = _parse_ticker(raw, underlying)
        if parsed is None:
            continue
        exp, strike, side, row, spot = parsed
        if spot > 0:
            spot_by_strike[strike] = spot
        bucket = by_expiry.setdefault(exp, {})
        cell = bucket.setdefault(strike, {"strike": strike, "ce": {}, "pe": {}})
        cell[side] = row
    if not by_expiry:
        raise OpenAlgoError(f"delta india {underlying}: no option tickers")
    chosen = _pick_expiry(by_expiry, expiry)
    strikes = by_expiry[chosen]
    chain = [strikes[k] for k in sorted(strikes)]
    fallback_spot = next(iter(spot_by_strike.values()), 0.0)
    atm = _atm(chain, fallback_spot)
    spot = spot_by_strike.get(atm) or fallback_spot
    chain = _atm_window(chain, atm, strike_count, strike_step)
    return {
        "chain": chain,
        "underlying_ltp": spot,
        "atm_strike": atm,
        "expiry": chosen.isoformat(),
    }


def _atm_window(
    chain: list[dict[str, Any]],
    atm: float,
    count: int,
    strike_step: float | None,
) -> list[dict[str, Any]]:
    if count <= 0:
        return chain
    if strike_step and strike_step > 0:
        width = count * float(strike_step)
        window = [row for row in chain if abs(float(row["strike"]) - atm) <= width]
        return window or chain
    below = [row for row in chain if float(row["strike"]) <= atm]
    above = [row for row in chain if float(row["strike"]) > atm]
    return below[-count:] + above[:count]


def _parse_ticker(
    raw: dict[str, Any], underlying: str
) -> tuple[date, float, str, dict[str, Any], float] | None:
    symbol = str(raw.get("symbol") or "")
    match = _SYMBOL.match(symbol)
    if match is None:
        return None
    if match.group("under") != underlying:
        return None
    exp = _expiry_from_ddmmyy(match.group("exp"))
    if exp is None:
        return None
    try:
        strike = float(raw.get("strike_price") or match.group("strike"))
    except (TypeError, ValueError):
        return None
    kind = match.group("kind")
    side = "ce" if kind == "C" else "pe"
    ltp = _num(raw.get("close")) or _num(raw.get("mark_price"))
    row = {
        "symbol": symbol,
        "ltp": ltp,
        "volume": _num(raw.get("volume")),
        "oi": _num(raw.get("oi")) or _num(raw.get("oi_contracts")),
        "lotsize": 1,
    }
    spot = _num(raw.get("spot_price"))
    greeks = raw.get("greeks") if isinstance(raw.get("greeks"), dict) else {}
    if spot <= 0:
        spot = _num(greeks.get("spot"))
    return exp, strike, side, row, spot


def _pick_expiry(by_expiry: dict[date, dict], wanted: date | None) -> date:
    dates = sorted(by_expiry)
    if wanted is not None and wanted in by_expiry:
        return wanted
    if wanted is None:
        return dates[0]
    future = [day for day in dates if day >= wanted]
    return future[0] if future else dates[-1]


def _atm(chain: list[dict[str, Any]], spot: float) -> float:
    if not chain:
        return 0.0
    if spot <= 0:
        return float(chain[len(chain) // 2]["strike"])
    return float(min(chain, key=lambda row: abs(float(row["strike"]) - spot))["strike"])


def _expiry_from_token(token: str) -> date | None:
    try:
        return datetime.strptime(token, "%d%b%y").date()
    except (TypeError, ValueError):
        return None


def _expiry_from_ddmmyy(raw: str) -> date | None:
    try:
        return datetime.strptime(raw, "%d%m%y").date()
    except (TypeError, ValueError):
        return None


def _num(raw: Any) -> float:
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0
