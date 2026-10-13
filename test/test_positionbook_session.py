"""Session position book: Analyze uses sandbox; a stale SPA key must not 403."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Flask, session

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_DB = Path(__file__).resolve().parents[1] / "tmp" / "test_positionbook_session.db"
TEST_DB.parent.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB.as_posix()}")
os.environ.setdefault("API_KEY_PEPPER", "a" * 64)

from services.positionbook_service import (  # noqa: E402
    get_positionbook,
    get_positionbook_for_session,
)


def test_session_book_uses_sandbox_user_in_analyze(monkeypatch):
    monkeypatch.setattr(
        "database.settings_db.get_analyze_mode",
        lambda: True,
    )
    seen = []

    def fake_sandbox(user_id):
        seen.append(user_id)
        return True, {"status": "success", "data": [{"symbol": "NIFTY19OCT2622400PE"}], "mode": "analyze"}, 200

    monkeypatch.setattr(
        "services.sandbox_service.sandbox_get_positions_for_user",
        fake_sandbox,
    )

    ok, body, code = get_positionbook_for_session("admin", "zerodha")
    assert ok is True
    assert code == 200
    assert body["data"][0]["symbol"] == "NIFTY19OCT2622400PE"
    assert seen == ["admin"]


def test_session_book_analyze_does_not_need_api_key(monkeypatch):
    monkeypatch.setattr("database.settings_db.get_analyze_mode", lambda: True)
    monkeypatch.setattr("database.auth_db.get_api_key_for_tradingview", lambda username: None)
    monkeypatch.setattr(
        "services.sandbox_service.sandbox_get_positions_for_user",
        lambda user_id: (True, {"status": "success", "data": [], "mode": "analyze"}, 200),
    )

    ok, body, code = get_positionbook_for_session("admin", "zerodha")
    assert ok is True
    assert code == 200
    assert body["data"] == []


def test_stale_client_key_falls_back_to_session(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test"

    def fake_get_auth_token_broker(provided):
        if provided == "host-key":
            return "broker-token", "zerodha"
        return None, None

    monkeypatch.setattr(
        "services.positionbook_service.get_auth_token_broker",
        fake_get_auth_token_broker,
    )
    monkeypatch.setattr(
        "services.positionbook_service.get_positionbook_with_auth",
        lambda token, broker, original_data: (
            True,
            {"status": "success", "data": [{"symbol": "PE"}], "mode": "analyze"},
            200,
        ),
    )
    monkeypatch.setattr(
        "database.auth_db.get_api_key_for_tradingview",
        lambda username: "host-key",
    )

    with app.test_request_context("/api/v1/positionbook"):
        session["user"] = "admin"
        session["logged_in"] = True
        ok, body, code = get_positionbook(api_key="laptop-key")

    assert ok is True
    assert code == 200
    assert body["data"][0]["symbol"] == "PE"


def test_stale_key_without_session_still_403(monkeypatch):
    monkeypatch.setattr(
        "services.positionbook_service.get_auth_token_broker",
        lambda provided: (None, None),
    )

    ok, body, code = get_positionbook(api_key="laptop-key")
    assert ok is False
    assert code == 403
    assert body["message"] == "Invalid openalgo apikey"
