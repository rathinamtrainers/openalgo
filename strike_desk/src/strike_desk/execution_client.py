"""The desk's only write path to a broker: three endpoints, whitelisted by constant.

Nothing here is reachable from an agent. The reasoning plane holds tools, and no tool in any
agent's whitelist places, modifies or cancels an order; placement is control-plane code that
runs after a deterministic verdict and behind a human click.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Settings
from .errors import ExecutionPathViolation, InvalidOrderPayload, OpenAlgoError

logger = logging.getLogger(__name__)

PATH_PLACE = "/api/v1/placeorder"
PATH_STATUS = "/api/v1/orderstatus"
PATH_CANCEL = "/api/v1/cancelorder"
PATH_CLOSE = "/api/v1/closeposition"
EXECUTION_PATHS = frozenset({PATH_PLACE, PATH_STATUS, PATH_CANCEL, PATH_CLOSE})

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

VALID_ACTIONS = frozenset({"BUY", "SELL"})
VALID_PRICE_TYPES = frozenset({"MARKET", "LIMIT", "SL", "SL-M"})
VALID_PRODUCTS = frozenset({"MIS", "NRML", "CNC"})
REQUIRED_FIELDS = (
    "strategy",
    "symbol",
    "exchange",
    "action",
    "quantity",
    "pricetype",
    "product",
    "price",
)


def validate_order_payload(payload: dict[str, Any]) -> None:
    """The last check before the wire. Raises rather than sending something malformed."""
    missing = [name for name in REQUIRED_FIELDS if name not in payload]
    if missing:
        raise InvalidOrderPayload(f"order payload is missing {', '.join(missing)}")
    if payload["action"] not in VALID_ACTIONS:
        raise InvalidOrderPayload(f"action {payload['action']!r} is not BUY or SELL")
    if payload["pricetype"] not in VALID_PRICE_TYPES:
        raise InvalidOrderPayload(f"pricetype {payload['pricetype']!r} is not a valid price type")
    if payload["product"] not in VALID_PRODUCTS:
        raise InvalidOrderPayload(f"product {payload['product']!r} is not a valid product")
    quantity = payload["quantity"]
    if not isinstance(quantity, int) or quantity <= 0:
        raise InvalidOrderPayload(f"quantity {quantity!r} must be a positive whole number")
    price = float(payload["price"])
    if payload["pricetype"] == "LIMIT" and price <= 0:
        raise InvalidOrderPayload("a LIMIT order needs a positive price")
    if not str(payload["symbol"]).strip() or not str(payload["exchange"]).strip():
        raise InvalidOrderPayload("symbol and exchange must both be non-empty")


@dataclass(frozen=True)
class PlacementReceipt:
    """What came back from a placement, classified."""

    queued: bool
    pending_order_id: int | None
    mode: str
    broker_order_id: str | None
    raw: dict[str, Any] = field(default_factory=dict)


class ExecutionClient:
    """One pooled client for the process. Never instantiate one per order."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._api_key = settings.openalgo_api_key.get_secret_value()
        self._client = httpx.Client(
            base_url=settings.openalgo_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.openalgo_timeout_seconds),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            headers={"User-Agent": "strike-desk-execution/0.6"},
        )

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        return min(0.25 * (2**attempt), 2.0)

    def _request(
        self, path: str, payload: dict[str, Any], *, retries: int
    ) -> tuple[int, dict[str, Any]]:
        """POST and return (status code, parsed body). The whitelist is checked first."""
        if path not in EXECUTION_PATHS:
            raise ExecutionPathViolation(path)

        body: dict[str, Any] = dict(payload)
        body["apikey"] = self._api_key
        last_error: OpenAlgoError | None = None

        for attempt in range(retries + 1):
            try:
                response = self._client.post(path, json=body)
            except httpx.HTTPError as exc:
                last_error = OpenAlgoError(f"{path}: transport failure {type(exc).__name__}")
            else:
                if response.status_code in RETRYABLE_STATUS and attempt < retries:
                    last_error = OpenAlgoError(
                        f"{path}: HTTP {response.status_code}", status_code=response.status_code
                    )
                else:
                    try:
                        parsed = response.json()
                    except ValueError as exc:
                        raise OpenAlgoError(f"{path}: response body was not JSON") from exc
                    if not isinstance(parsed, dict):
                        raise OpenAlgoError(f"{path}: response was not a JSON object")
                    return response.status_code, parsed

            if attempt < retries:
                delay = self._backoff_seconds(attempt)
                logger.warning(
                    "%s failed (attempt %d), retrying in %.2fs", path, attempt + 1, delay
                )
                time.sleep(delay)

        raise last_error or OpenAlgoError(f"{path}: exhausted retries")

    def place_order(self, payload: dict[str, Any]) -> PlacementReceipt:
        """Submit one order. Never retried: a repeat POST is a second order."""
        validate_order_payload(payload)
        status_code, parsed = self._request(PATH_PLACE, payload, retries=0)
        if status_code != 200 or parsed.get("status") != "success":
            raise OpenAlgoError(
                f"{PATH_PLACE}: HTTP {status_code} status={parsed.get('status')!r} "
                f"message={str(parsed.get('message'))[:200]!r}",
                status_code=status_code,
            )
        mode = str(parsed.get("mode") or "")
        raw_id = parsed.get("pending_order_id")
        pending_order_id = (
            int(raw_id) if isinstance(raw_id, int | str) and str(raw_id).isdigit() else None
        )
        queued = mode == "semi_auto" and bool(pending_order_id)
        broker_order_id = parsed.get("orderid")
        return PlacementReceipt(
            queued=queued,
            pending_order_id=pending_order_id if queued else None,
            mode=mode or "unknown",
            broker_order_id=str(broker_order_id) if broker_order_id else None,
            raw=parsed,
        )

    def order_status(self, orderid: str, strategy: str) -> dict[str, Any]:
        """One order as OpenAlgo's normalised order book reports it."""
        status_code, parsed = self._request(
            PATH_STATUS, {"orderid": str(orderid), "strategy": strategy}, retries=1
        )
        if status_code != 200 or parsed.get("status") != "success":
            raise OpenAlgoError(
                f"{PATH_STATUS}: HTTP {status_code} message={str(parsed.get('message'))[:200]!r}",
                status_code=status_code,
            )
        data = parsed.get("data")
        if not isinstance(data, dict):
            raise OpenAlgoError(f"{PATH_STATUS}: 'data' was not an object")
        return data

    def cancel_order(self, orderid: str, strategy: str) -> tuple[bool, str]:
        """Try to withdraw an order. Returns (permitted, detail) and never raises for a refusal.

        OpenAlgo blocks ``cancelorder`` for an API key in semi-auto mode unless the platform is
        in analyze (sandbox) mode, so a 403 here is the platform's policy rather than a fault.
        The caller records which it was; it does not retry a refusal.
        """
        try:
            status_code, parsed = self._request(
                PATH_CANCEL, {"orderid": str(orderid), "strategy": strategy}, retries=1
            )
        except OpenAlgoError as exc:
            return False, f"cancel failed: {exc}"
        if status_code == 200 and parsed.get("status") == "success":
            return True, f"order {orderid} cancelled"
        detail = str(parsed.get("message") or f"HTTP {status_code}")[:200]
        return False, f"cancel refused by the platform: {detail}"

    def close_position(self, strategy: str) -> tuple[bool, dict[str, Any]]:
        """Close the account's open position immediately. Never queued for approval.

        OpenAlgo lists ``closeposition`` among the operations that never route to the Action
        Center, and permits it in analyze mode regardless of order mode. In live semi-auto it
        answers 403, which the caller reads as 'this rung of the ladder is closed' rather than
        as a fault. Retried once: closing an already-flat book is a no-op, so it is safe.
        """
        try:
            status_code, parsed = self._request(PATH_CLOSE, {"strategy": strategy}, retries=1)
        except OpenAlgoError as exc:
            return False, {"status": "error", "message": str(exc)}
        if status_code == 200 and parsed.get("status") == "success":
            return True, parsed
        return False, parsed
