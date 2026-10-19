"""Tradetron webhook hands for StrikeShield. Never logs the auth token."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import Settings

logger = logging.getLogger(__name__)

USER_AGENT = "StrikeDesk-StrikeShield/0.2"

BUY_WINGS = "buy_wings"
SELL_SHORTS = "sell_shorts"
EXIT_SHORTS = "exit_shorts"
EXIT_ALL = "exit_all"

_PAIR_MAP: dict[str, tuple[tuple[str, int], ...]] = {
    BUY_WINGS: (("qs_buy", 1), ("qs_sell", 0), ("qs_buy_exit", 0), ("qs_sell_exit", 0)),
    SELL_SHORTS: (("qs_sell", 1), ("qs_buy", 0), ("qs_sell_exit", 0)),
    EXIT_SHORTS: (("qs_sell_exit", 1), ("qs_sell", 0)),
    EXIT_ALL: (("qs_sell_exit", 1), ("qs_buy_exit", 1), ("qs_sell", 0), ("qs_buy", 0)),
}


@dataclass(frozen=True)
class HandsResult:
    action: str
    http_status: int
    body: str


def build_payload(token: str, pairs: tuple[tuple[str, int], ...]) -> dict[str, str]:
    if not pairs:
        raise ValueError("no signal pairs")
    payload = {"auth-token": token, "key": pairs[0][0], "value": str(pairs[0][1])}
    for i, (key, value) in enumerate(pairs[1:], start=1):
        payload[f"key{i}"] = key
        payload[f"value{i}"] = str(value)
    return payload


COA_BUY_CE = "buy_ce"
COA_BUY_PE = "buy_pe"
COA_EXIT = "exit"

_COA_MAP: dict[str, tuple[tuple[str, int], ...]] = {
    COA_BUY_CE: (("ltp_buy_ce", 1), ("ltp_buy_pe", 0), ("ltp_exit", 0)),
    COA_BUY_PE: (("ltp_buy_pe", 1), ("ltp_buy_ce", 0), ("ltp_exit", 0)),
    COA_EXIT: (("ltp_exit", 1), ("ltp_buy_ce", 0), ("ltp_buy_pe", 0)),
}


class TradetronHands:
    """POST BUY/SELL/EXIT flags to the Strike Desk QuadShield NIFTY Hands template."""

    def __init__(self, settings: Settings) -> None:
        token = ""
        for secret in (settings.tt_auth_token_hands, settings.tt_auth_token):
            if secret is None:
                continue
            token = secret.get_secret_value().strip()
            if token:
                break
        if not token:
            raise RuntimeError("STRIKE_DESK_TT_AUTH_TOKEN_HANDS is not set")
        self._token = token
        self._url = settings.tt_webhook_url

    def send(self, action: str) -> HandsResult:
        pairs = _PAIR_MAP.get(action)
        if pairs is None:
            raise ValueError(f"unknown hands action {action!r}")
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
            logger.error("tradetron hands %s HTTP %s: %s", action, exc.code, err[:300])
            raise
        logger.info("tradetron hands %s -> %s %s", action, status, text[:160])
        return HandsResult(action=action, http_status=status, body=text)


class CoaHands:
    """POST ltp_buy_ce / ltp_buy_pe / ltp_exit to the CoA Hands template."""

    def __init__(self, settings: Settings) -> None:
        token = ""
        if settings.tt_auth_token_coa is not None:
            token = settings.tt_auth_token_coa.get_secret_value().strip()
        if not token:
            raise RuntimeError("STRIKE_DESK_TT_AUTH_TOKEN_COA is not set")
        self._token = token
        self._url = settings.tt_webhook_url

    def send(self, action: str) -> HandsResult:
        pairs = _COA_MAP.get(action)
        if pairs is None:
            raise ValueError(f"unknown CoA hands action {action!r}")
        payload = build_payload(self._token, pairs)
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "StrikeDesk-CoA/0.2"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            err = exc.read().decode("utf-8", errors="replace")
            logger.error("coa hands %s HTTP %s: %s", action, exc.code, err[:300])
            raise
        logger.info("coa hands %s -> %s %s", action, status, text[:160])
        return HandsResult(action=action, http_status=status, body=text)
