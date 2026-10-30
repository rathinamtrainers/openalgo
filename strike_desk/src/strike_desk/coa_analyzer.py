"""OpenAlgo Analyze fills for CoA. Never toggles live. Never uses OpenAlgoClient."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx

from .config import Settings
from .errors import AnalyzerOff, OpenAlgoError
from .ofa_analyzer import AnalyzerFill, expiry_token, pick_atm_option
from .openalgo_client import OpenAlgoClient

logger = logging.getLogger(__name__)

STRATEGY = "COA"
STATUS_PATH = "/api/v1/analyzer"
PLACE_PATH = "/api/v1/placeorder"
TOGGLE_PATH = "/api/v1/analyzer/toggle"


class CoaAnalyzer:
    """placeorder only while OpenAlgo reports analyze_mode=true. Tag: COA."""

    def __init__(self, settings: Settings, quotes: OpenAlgoClient) -> None:
        self._settings = settings
        self._quotes = quotes
        self._api_key = settings.openalgo_api_key.get_secret_value()
        self._client = httpx.Client(
            base_url=settings.openalgo_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.openalgo_timeout_seconds),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            headers={"User-Agent": "strike-desk-coa/0.2"},
        )

    def close(self) -> None:
        self._client.close()

    def analyze_mode(self) -> bool:
        payload = self._post(STATUS_PATH, {})
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        return bool(data.get("analyze_mode"))

    def resolve_atm(
        self,
        option_type: str,
        expiry_day: date,
        *,
        underlying: str | None = None,
        spot_exchange: str | None = None,
    ) -> tuple[str, int]:
        token = expiry_token(expiry_day)
        payload = self._quotes.optionchain(
            underlying or self._settings.index_symbol,
            spot_exchange or self._settings.index_spot_exchange,
            token,
            strike_count=4,
        )
        symbol, lot = pick_atm_option(payload, option_type)
        qty = lot * self._settings.coa_lots
        return symbol, qty

    def place(
        self,
        symbol: str,
        action: str,
        quantity: int,
        *,
        exchange: str | None = None,
    ) -> AnalyzerFill:
        if not self.analyze_mode():
            raise AnalyzerOff("OpenAlgo analyze_mode is off; refusing placeorder")
        payload = self._post(
            PLACE_PATH,
            {
                "strategy": STRATEGY,
                "symbol": symbol,
                "action": action.upper(),
                "exchange": exchange or self._settings.option_exchange,
                "pricetype": "MARKET",
                "product": self._settings.coa_product,
                "quantity": str(quantity),
            },
        )
        if payload.get("status") != "success":
            raise OpenAlgoError(f"CoA analyzer {action} {symbol}: {payload.get('message')}")
        fill = AnalyzerFill(
            action=action.upper(),
            symbol=symbol,
            quantity=quantity,
            orderid=str(payload.get("orderid") or ""),
            mode=str(payload.get("mode") or "analyze"),
        )
        logger.info(
            "coa analyzer %s %s qty=%s orderid=%s mode=%s",
            fill.action,
            fill.symbol,
            fill.quantity,
            fill.orderid,
            fill.mode,
        )
        return fill

    def _post(self, path: str, extra: dict[str, Any]) -> dict[str, Any]:
        if path == TOGGLE_PATH:
            raise OpenAlgoError("CoA analyzer will not toggle analyze mode")
        body = {"apikey": self._api_key, **extra}
        try:
            response = self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise OpenAlgoError(f"CoA analyzer {path}: {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise OpenAlgoError(
                f"CoA analyzer {path}: non-JSON {response.status_code}"
            ) from exc
        if not isinstance(payload, dict):
            raise OpenAlgoError(f"CoA analyzer {path}: unexpected payload")
        if response.status_code >= 400:
            raise OpenAlgoError(
                f"CoA analyzer {path}: HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return payload
