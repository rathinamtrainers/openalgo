"""Unattended Zerodha session refresh for the weekday 08:20 IST timer.

Indian broker tokens die around 03:00 IST. This oneshot checks the stored
admin token; if funds() still works it exits 0. Otherwise it logs into Kite
with the host-only TOTP secret, exchanges the request_token, and upserts
``api_key:access_token`` — the same shape ``brlogin`` stores.

Secrets come from ``/etc/openalgo/zerodha-login.env`` (not git, not logs).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
import pyotp

LOGIN_ENV_PATH = Path(os.getenv("ZERODHA_LOGIN_ENV", "/etc/openalgo/zerodha-login.env"))
APP_ENV_PATH = Path(os.getenv("OPENALGO_ENV", "/opt/openalgo/current/.env"))
CONNECT_PATH = "https://kite.zerodha.com/connect/login"
LOGIN_PATH = "https://kite.zerodha.com/api/login"
TWOFA_PATH = "https://kite.zerodha.com/api/twofa"
KITE_UA = "Mozilla/5.0 (X11; Linux x86_64) OpenAlgoSessionRefresh/1.0"


def _load_env() -> None:
    from dotenv import load_dotenv

    if APP_ENV_PATH.exists():
        load_dotenv(APP_ENV_PATH, override=False)
    if LOGIN_ENV_PATH.exists():
        load_dotenv(LOGIN_ENV_PATH, override=True)
    load_dotenv(override=False)


def _require(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise SystemExit(f"missing {name} (set it in {LOGIN_ENV_PATH})")
    return value


def extract_request_token(url: str) -> str | None:
    query = parse_qs(urlparse(url).query)
    tokens = query.get("request_token") or query.get("request-token")
    if not tokens:
        return None
    token = (tokens[0] or "").strip()
    return token or None


def _json_status(payload: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return "error", {}
    data = payload.get("data")
    return str(payload.get("status") or ""), data if isinstance(data, dict) else {}


def kite_request_token(
    *,
    user_id: str,
    password: str,
    totp_secret: str,
    api_key: str,
    client: httpx.Client,
) -> str:
    """Login + TOTP, then follow Kite connect redirects to a request_token."""
    login = client.post(LOGIN_PATH, data={"user_id": user_id, "password": password})
    login.raise_for_status()
    status, data = _json_status(login.json())
    if status != "success" or not data.get("request_id"):
        raise RuntimeError("kite login rejected (password or user id)")

    totp = pyotp.TOTP(totp_secret.strip().replace(" ", "").upper()).now()
    twofa = client.post(
        TWOFA_PATH,
        data={
            "user_id": user_id,
            "request_id": data["request_id"],
            "twofa_value": totp,
            "twofa_type": data.get("twofa_type") or "totp",
            "skip_session": "true",
        },
    )
    try:
        twofa_payload = twofa.json()
    except Exception as exc:
        raise RuntimeError(f"kite TOTP returned {twofa.status_code}") from exc
    twofa_status, extra = _json_status(twofa_payload)
    if twofa.status_code >= 400 or twofa_status != "success":
        message = twofa_payload.get("message") if isinstance(twofa_payload, dict) else None
        remaining = extra.get("attempts_remaining")
        detail = message or f"HTTP {twofa.status_code}"
        if remaining is not None:
            detail = f"{detail} (attempts remaining: {remaining})"
        raise RuntimeError(detail)

    url = f"{CONNECT_PATH}?v=3&api_key={api_key}"
    for _ in range(10):
        token = extract_request_token(url)
        if token:
            return token
        response = client.get(url)
        location = response.headers.get("location")
        if location:
            url = urljoin(str(response.url), location)
            continue
        token = extract_request_token(str(response.url))
        if token:
            return token
        raise RuntimeError("kite connect did not return a request_token")
    raise RuntimeError("kite connect redirected too many times")


def token_is_live(auth_token: str) -> bool:
    from broker.zerodha.api.funds import get_margin_data

    funds = get_margin_data(auth_token)
    return bool(isinstance(funds, dict) and funds)


def persist_session(username: str, access_token: str, api_key: str) -> None:
    from database.auth_db import upsert_auth

    stored = f"{api_key}:{access_token}"
    upsert_auth(username, stored, "zerodha")


def stored_token(username: str) -> str | None:
    from database.auth_db import decrypt_token, get_auth_token_dbquery

    row = get_auth_token_dbquery(username)
    if not row or row.is_revoked or row.broker != "zerodha":
        return None
    return decrypt_token(row.auth) if row.auth else None


def refresh_if_needed() -> str:
    """Return 'live', 'refreshed', or raise. Never logs secrets."""
    username = (os.getenv("OPENALGO_USER") or "admin").strip()
    api_key = _require("BROKER_API_KEY")
    existing = stored_token(username)
    if existing and token_is_live(existing):
        return "live"

    user_id = _require("KITE_USER_ID")
    password = _require("KITE_PASSWORD")
    totp_secret = _require("KITE_TOTP_SECRET")

    with httpx.Client(
        follow_redirects=False,
        timeout=30.0,
        headers={"User-Agent": KITE_UA, "X-Kite-Version": "3"},
    ) as client:
        request_token = kite_request_token(
            user_id=user_id,
            password=password,
            totp_secret=totp_secret,
            api_key=api_key,
            client=client,
        )

    from broker.zerodha.api.auth_api import authenticate_broker

    access_token, error = authenticate_broker(request_token)
    if not access_token:
        raise RuntimeError(error or "authenticate_broker returned no access token")
    persist_session(username, access_token, api_key)
    return "refreshed"


def main(argv: list[str] | None = None) -> int:
    _ = argv
    _load_env()
    from utils.logging import get_logger

    logger = get_logger(__name__)
    try:
        outcome = refresh_if_needed()
    except SystemExit as exc:
        logger.error("%s", exc)
        return 2
    except Exception:
        logger.exception("Zerodha session refresh failed")
        return 1
    logger.info("Zerodha session %s", outcome)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
