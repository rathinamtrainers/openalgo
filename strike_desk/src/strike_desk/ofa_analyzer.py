"""OpenAlgo Analyzer fills for OFA. Never toggles live. Never uses OpenAlgoClient."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from .config import Settings
from .errors import AnalyzerOff, OpenAlgoError
from .openalgo_client import OpenAlgoClient

logger = logging.getLogger(__name__)

STRATEGY = "OFA"
STATUS_PATH = "/api/v1/analyzer"
PLACE_PATH = "/api/v1/placeorder"
TOGGLE_PATH = "/api/v1/analyzer/toggle"


def expiry_token(day: date) -> str:
    """OpenAlgo weekly expiry token, e.g. 15OCT25."""
    return day.strftime("%d%b%y").upper()


@dataclass(frozen=True)
class AnalyzerFill:
    action: str
    symbol: str
    quantity: int
    orderid: str
    mode: str


def pick_atm_option(payload: dict[str, Any], option_type: str) -> tuple[str, int]:
    """Return (OpenAlgo symbol, lotsize) for the chain's ATM CE or PE."""
    side_key = "ce" if option_type.upper() == "CE" else "pe"
    atm = float(payload.get("atm_strike") or 0)
    chain = payload.get("chain") or []
    if atm <= 0 or not isinstance(chain, list):
        raise OpenAlgoError("OFA analyzer: empty option chain")
    nearest: tuple[float, str, int] | None = None
    for row in chain:
        if not isinstance(row, dict):
            continue
        try:
            strike = float(row.get("strike") or 0)
        except (TypeError, ValueError):
            continue
        side = row.get(side_key) if isinstance(row.get(side_key), dict) else {}
        symbol = str(side.get("symbol") or "").strip()
        if not symbol:
            continue
        try:
            lot = int(float(side.get("lotsize") or 65))
        except (TypeError, ValueError):
            lot = 65
        gap = abs(strike - atm)
        if nearest is None or gap < nearest[0]:
            nearest = (gap, symbol, lot)
    if nearest is None:
        raise OpenAlgoError(f"OFA analyzer: no ATM {option_type}")
    return nearest[1], nearest[2]


class OfaAnalyzer:
    """placeorder only while OpenAlgo reports analyze_mode=true."""

    def __init__(self, settings: Settings, quotes: OpenAlgoClient) -> None:
        self._settings = settings
        self._quotes = quotes
        self._api_key = settings.openalgo_api_key.get_secret_value()
        self._client = httpx.Client(
            base_url=settings.openalgo_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.openalgo_timeout_seconds),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            headers={"User-Agent": "strike-desk-ofa/0.2"},
        )

    def close(self) -> None:
        self._client.close()

    def analyze_mode(self) -> bool:
        payload = self._post(STATUS_PATH, {})
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        return bool(data.get("analyze_mode"))

    def resolve_atm(self, option_type: str, expiry_day: date) -> tuple[str, int]:
        token = expiry_token(expiry_day)
        payload = self._quotes.optionchain(
            self._settings.index_symbol,
            self._settings.index_spot_exchange,
            token,
            strike_count=4,
        )
        symbol, lot = pick_atm_option(payload, option_type)
        qty = lot * self._settings.ofa_lots
        return symbol, qty

    def place(self, symbol: str, action: str, quantity: int) -> AnalyzerFill:
        if not self.analyze_mode():
            raise AnalyzerOff("OpenAlgo analyze_mode is off; refusing placeorder")
        payload = self._post(
            PLACE_PATH,
            {
                "strategy": STRATEGY,
                "symbol": symbol,
                "action": action.upper(),
                "exchange": self._settings.option_exchange,
                "pricetype": "MARKET",
                "product": self._settings.ofa_product,
                "quantity": str(quantity),
            },
        )
        if payload.get("status") != "success":
            raise OpenAlgoError(f"OFA analyzer {action} {symbol}: {payload.get('message')}")
        fill = AnalyzerFill(
            action=action.upper(),
            symbol=symbol,
            quantity=quantity,
            orderid=str(payload.get("orderid") or ""),
            mode=str(payload.get("mode") or "analyze"),
        )
        logger.info(
            "ofa analyzer %s %s qty=%s orderid=%s mode=%s",
            fill.action,
            fill.symbol,
            fill.quantity,
            fill.orderid,
            fill.mode,
        )
        return fill

    def _post(self, path: str, extra: dict[str, Any]) -> dict[str, Any]:
        if path == TOGGLE_PATH:
            raise OpenAlgoError("OFA analyzer will not toggle analyze mode")
        body = {"apikey": self._api_key, **extra}
        try:
            response = self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise OpenAlgoError(f"OFA analyzer {path}: {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise OpenAlgoError(f"OFA analyzer {path}: non-JSON {response.status_code}") from exc
        if not isinstance(payload, dict):
            raise OpenAlgoError(f"OFA analyzer {path}: unexpected payload")
        if response.status_code >= 400:
            raise OpenAlgoError(
                f"OFA analyzer {path}: HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return payload
