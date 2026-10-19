"""Tradetron webhook hands for OFA. Never logs the auth token."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import Settings
from .tradetron_hands import build_payload

logger = logging.getLogger(__name__)

USER_AGENT = "StrikeDesk-OFA/0.2"

OFA_BUY_CE = "buy_ce"
OFA_BUY_PE = "buy_pe"
OFA_EXIT = "exit"

_OFA_MAP: dict[str, tuple[tuple[str, int], ...]] = {
    OFA_BUY_CE: (("of_long", 1), ("of_short", 0), ("of_exit", 0)),
    OFA_BUY_PE: (("of_short", 1), ("of_long", 0), ("of_exit", 0)),
    OFA_EXIT: (("of_exit", 1), ("of_long", 0), ("of_short", 0)),
}


@dataclass(frozen=True)
class OfaHandsResult:
    action: str
    http_status: int
    body: str


class OfaHands:
    """POST of_long / of_short / of_exit to the OFA Hands template."""

    def __init__(self, settings: Settings) -> None:
        token = ""
        if settings.tt_auth_token_ofa is not None:
            token = settings.tt_auth_token_ofa.get_secret_value().strip()
        if not token:
            raise RuntimeError("STRIKE_DESK_TT_AUTH_TOKEN_OFA is not set")
        self._token = token
        self._url = settings.tt_webhook_url

    def send(self, action: str) -> OfaHandsResult:
        pairs = _OFA_MAP.get(action)
        if pairs is None:
            raise ValueError(f"unknown OFA hands action {action!r}")
        payload = build_payload(self._token, pairs)
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            err = exc.read().decode("utf-8", errors="replace")
            logger.error("ofa hands %s HTTP %s: %s", action, exc.code, err[:300])
            raise
        logger.info("ofa hands %s -> %s %s", action, status, text[:160])
        return OfaHandsResult(action=action, http_status=status, body=text)
