"""Tradetron webhook hands for Strike Desk NIFTY Analyzer. Never logs the auth token."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import Settings
from .tradetron_hands import build_payload

logger = logging.getLogger(__name__)

USER_AGENT = "StrikeDesk-Analyzer/0.1"

ANALYZER_BUY_CE = "buy_ce"
ANALYZER_BUY_PE = "buy_pe"
ANALYZER_EXIT = "exit"

_ANALYZER_MAP: dict[str, tuple[tuple[str, int], ...]] = {
    ANALYZER_BUY_CE: (("an_ce", 1), ("an_pe", 0), ("an_exit", 0)),
    ANALYZER_BUY_PE: (("an_pe", 1), ("an_ce", 0), ("an_exit", 0)),
    ANALYZER_EXIT: (("an_exit", 1), ("an_ce", 0), ("an_pe", 0)),
}


@dataclass(frozen=True)
class AnalyzerHandsResult:
    action: str
    http_status: int
    body: str


class AnalyzerHands:
    """POST an_ce / an_pe / an_exit to the Strike Desk NIFTY Analyzer template."""

    def __init__(self, settings: Settings) -> None:
        token = ""
        secret = getattr(settings, "tt_auth_token_analyzer", None)
        if secret is not None:
            token = secret.get_secret_value().strip()
        if not token:
            raise RuntimeError("STRIKE_DESK_TT_AUTH_TOKEN_ANALYZER is not set")
        self._token = token
        self._url = settings.tt_webhook_url

    def send(self, action: str) -> AnalyzerHandsResult:
        pairs = _ANALYZER_MAP.get(action)
        if pairs is None:
            raise ValueError(f"unknown analyzer hands action {action!r}")
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
            logger.error("analyzer hands %s HTTP %s: %s", action, exc.code, err[:300])
            raise
        logger.info("analyzer hands %s -> %s %s", action, status, text[:160])
        return AnalyzerHandsResult(action=action, http_status=status, body=text)


def notify_analyzer_hands(settings: Settings, symbol: str, kind: str) -> bool:
    """Fan-out a cleared Analyzer fill. Never raises; skip when the token is unset."""
    try:
        secret = getattr(settings, "tt_auth_token_analyzer", None)
        if secret is None or not secret.get_secret_value().strip():
            return False
        text = str(symbol or "").strip().upper()
        if kind == "exit":
            action = ANALYZER_EXIT
        elif text.endswith("CE"):
            action = ANALYZER_BUY_CE
        elif text.endswith("PE"):
            action = ANALYZER_BUY_PE
        else:
            logger.warning("analyzer hands skipped: cannot map %r to %s", symbol, kind)
            return False
        AnalyzerHands(settings).send(action)
        return True
    except Exception:
        logger.exception("analyzer hands fan-out failed for %s %s", kind, symbol)
        return False
