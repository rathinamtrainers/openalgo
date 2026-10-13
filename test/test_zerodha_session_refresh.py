"""Zerodha unattended session refresh — no live Kite calls."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from services.zerodha_session_refresh import (
    extract_request_token,
    kite_request_token,
    refresh_if_needed,
)


def test_extract_request_token_from_redirect():
    url = "https://127.0.0.1:5000/?request_token=abc123&action=login&status=success"
    assert extract_request_token(url) == "abc123"


def test_extract_request_token_missing():
    assert extract_request_token("https://kite.zerodha.com/connect/login") is None


def test_kite_request_token_login_twofa_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(
                200,
                json={"status": "success", "data": {"request_id": "rid-1", "twofa_type": "totp"}},
            )
        if request.url.path == "/api/twofa":
            return httpx.Response(200, json={"status": "success", "data": {}})
        if request.url.path == "/connect/login":
            return httpx.Response(
                302,
                headers={
                    "location": "https://example.test/cb?request_token=req-99&status=success"
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, follow_redirects=False) as client:
        token = kite_request_token(
            user_id="AB1234",
            password="secret",
            totp_secret="JBSWY3DPEHPK3PXP",
            api_key="kitekey",
            client=client,
        )
    assert token == "req-99"


def test_refresh_skips_kite_when_token_live(monkeypatch):
    monkeypatch.setenv("BROKER_API_KEY", "kitekey")
    monkeypatch.setenv("OPENALGO_USER", "admin")
    with (
        patch("services.zerodha_session_refresh.stored_token", return_value="kitekey:old"),
        patch("services.zerodha_session_refresh.token_is_live", return_value=True),
        patch("services.zerodha_session_refresh.kite_request_token") as login,
    ):
        assert refresh_if_needed() == "live"
        login.assert_not_called()


def test_refresh_persists_new_token(monkeypatch):
    monkeypatch.setenv("BROKER_API_KEY", "kitekey")
    monkeypatch.setenv("OPENALGO_USER", "admin")
    monkeypatch.setenv("KITE_USER_ID", "AB1234")
    monkeypatch.setenv("KITE_PASSWORD", "secret")
    monkeypatch.setenv("KITE_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    persist = MagicMock()
    with (
        patch("services.zerodha_session_refresh.stored_token", return_value=None),
        patch("services.zerodha_session_refresh.kite_request_token", return_value="req-1"),
        patch(
            "broker.zerodha.api.auth_api.authenticate_broker",
            return_value=("access-1", None),
        ),
        patch("services.zerodha_session_refresh.persist_session", persist),
    ):
        assert refresh_if_needed() == "refreshed"
    persist.assert_called_once_with("admin", "access-1", "kitekey")


def test_refresh_fails_closed_without_credentials(monkeypatch):
    monkeypatch.setenv("BROKER_API_KEY", "kitekey")
    monkeypatch.delenv("KITE_USER_ID", raising=False)
    monkeypatch.delenv("KITE_PASSWORD", raising=False)
    monkeypatch.delenv("KITE_TOTP_SECRET", raising=False)
    with patch("services.zerodha_session_refresh.stored_token", return_value=None):
        with pytest.raises(SystemExit, match="KITE_USER_ID"):
            refresh_if_needed()
