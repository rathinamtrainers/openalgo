"""Read-only HTTP client for OpenAlgo's /api/v1/ surface."""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

import httpx

from .config import Settings
from .errors import OpenAlgoError, ReadOnlyViolation

logger = logging.getLogger(__name__)

READ_ONLY_PATHS = frozenset(
    {
        "/api/v1/ping",
        "/api/v1/funds",
        "/api/v1/positionbook",
        "/api/v1/market/timings",
    }
)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class OpenAlgoClient:
    """A single pooled client for the process. Never instantiate one per call."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._api_key = settings.openalgo_api_key.get_secret_value()
        self._client = httpx.Client(
            base_url=settings.openalgo_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.openalgo_timeout_seconds),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            headers={"User-Agent": "strike-desk/0.1"},
        )

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        return min(0.25 * (2**attempt), 2.0)

    def _post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if path not in READ_ONLY_PATHS:
            raise ReadOnlyViolation(path)

        body: dict[str, Any] = dict(payload or {})
        body["apikey"] = self._api_key
        last_error: OpenAlgoError | None = None

        for attempt in range(self._settings.openalgo_retries + 1):
            try:
                response = self._client.post(path, json=body)
            except httpx.HTTPError as exc:
                last_error = OpenAlgoError(f"{path}: transport failure {type(exc).__name__}")
            else:
                if response.status_code in RETRYABLE_STATUS:
                    last_error = OpenAlgoError(
                        f"{path}: HTTP {response.status_code}", status_code=response.status_code
                    )
                elif response.status_code != 200:
                    raise OpenAlgoError(
                        f"{path}: HTTP {response.status_code}", status_code=response.status_code
                    )
                else:
                    try:
                        parsed = response.json()
                    except ValueError as exc:
                        raise OpenAlgoError(f"{path}: response body was not JSON") from exc
                    if not isinstance(parsed, dict):
                        raise OpenAlgoError(f"{path}: response was not a JSON object")
                    if parsed.get("status") != "success":
                        raise OpenAlgoError(
                            f"{path}: status={parsed.get('status')!r} "
                            f"message={str(parsed.get('message'))[:200]!r}"
                        )
                    return parsed

            if attempt < self._settings.openalgo_retries:
                delay = self._backoff_seconds(attempt)
                logger.warning(
                    "%s failed (attempt %d), retrying in %.2fs", path, attempt + 1, delay
                )
                time.sleep(delay)

        raise last_error or OpenAlgoError(f"{path}: exhausted retries")

    def ping(self) -> dict[str, Any]:
        """Startup health check: proves OpenAlgo is up and the API key is valid."""
        return self._post("/api/v1/ping")

    def funds(self) -> dict[str, Any]:
        data = self._post("/api/v1/funds").get("data")
        if not isinstance(data, dict):
            raise OpenAlgoError("/api/v1/funds: 'data' was not an object")
        return data

    def positionbook(self) -> list[dict[str, Any]]:
        data = self._post("/api/v1/positionbook").get("data") or []
        if not isinstance(data, list):
            raise OpenAlgoError("/api/v1/positionbook: 'data' was not a list")
        return data

    def market_timings(self, day: date) -> list[dict[str, Any]]:
        data = self._post("/api/v1/market/timings", {"date": day.isoformat()}).get("data") or []
        if not isinstance(data, list):
            raise OpenAlgoError("/api/v1/market/timings: 'data' was not a list")
        return data
