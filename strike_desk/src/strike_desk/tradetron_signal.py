"""Fan-out Strike Desk entries and exits to a Tradetron Signal Bridge.

OpenAlgo stays the book of record. This module posts one official webhook
(`auth-token` + `key`/`value`, no `sid`) after an order has already been
accepted locally. Failures are logged and swallowed — a down Tradetron must
never change the OpenAlgo path, kill the desk, or raise into the tick.
"""

from __future__ import annotations

import logging
import threading
from typing import Literal

import httpx

from .config import Settings

logger = logging.getLogger(__name__)

USER_AGENT = "tt-webhook/1.0"
SIGNAL_VALUE = "1"

Kind = Literal["entry", "exit"]

_lock = threading.Lock()
_client: httpx.Client | None = None


def option_side(symbol: str) -> str | None:
    """CE or PE from an OpenAlgo option symbol; None if the suffix is missing."""
    text = str(symbol or "").strip().upper()
    if text.endswith("PE"):
        return "PE"
    if text.endswith("CE"):
        return "CE"
    return None


def signal_key(index_token: str, symbol: str, kind: Kind) -> str | None:
    """Map a desk contract to the Signal Bridge runtime variable.

    BUY ATM uses ``_el``; BUY EXIT uses ``_xl``. Shorts (``_es`` / ``_xs``)
    are not produced by this playbook.
    """
    side = option_side(symbol)
    token = str(index_token or "").strip().upper()
    if side is None or not token:
        return None
    suffix = "el" if kind == "entry" else "xl"
    return f"{token}_{side}_ATM_{suffix}"


def _shared_client(settings: Settings) -> httpx.Client:
    """One pooled client for the process. Never instantiate one per signal."""
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(
                timeout=httpx.Timeout(settings.tradetron_webhook_timeout_seconds),
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
                headers={"User-Agent": USER_AGENT},
            )
        return _client


def close_tradetron_client() -> None:
    """Release the shared HTTP client. Safe to call when nothing was opened."""
    global _client
    with _lock:
        if _client is not None:
            _client.close()
            _client = None


def notify_tradetron(settings: Settings, symbol: str, kind: Kind) -> bool:
    """POST one entry or exit variable. Returns True only on HTTP 2xx. Never raises."""
    try:
        return _notify(settings, symbol, kind)
    except Exception:
        logger.exception("tradetron fan-out failed for %s %s", kind, symbol)
        return False


def _notify(settings: Settings, symbol: str, kind: Kind) -> bool:
    if not settings.tradetron_webhook_enabled:
        return False
    token = settings.tradetron_webhook_token.get_secret_value().strip()
    if not token:
        logger.warning("tradetron fan-out skipped: webhook token is unset")
        return False
    key = signal_key(settings.tradetron_index_token, symbol, kind)
    if key is None:
        logger.warning(
            "tradetron fan-out skipped: cannot map %r to a %s variable",
            symbol,
            kind,
        )
        return False

    url = settings.tradetron_webhook_url.rstrip()
    if not url.endswith("?"):
        url = url.rstrip("/") + "?"
    body = {"auth-token": token, "key": key, "value": SIGNAL_VALUE}
    response = _shared_client(settings).post(
        url,
        json=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    if 200 <= response.status_code < 300:
        logger.info("tradetron fan-out %s %s -> HTTP %s", kind, key, response.status_code)
        return True
    logger.error(
        "tradetron fan-out %s %s returned HTTP %s",
        kind,
        key,
        response.status_code,
    )
    return False
