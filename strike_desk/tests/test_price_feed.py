"""Frame parsing, tick age and the feed's own liveness bookkeeping."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from strike_desk.price_feed import SOURCE_WEBSOCKET, PriceFeed, Tick, _parse_ltp

NOW = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)


def test_parses_a_market_data_frame():
    frame = {"type": "market_data", "symbol": "X", "mode": 1, "data": {"ltp": 101.25}}
    assert _parse_ltp(frame) == 101.25


def test_accepts_last_price_as_an_alias():
    assert _parse_ltp({"type": "market_data", "data": {"last_price": 88.0}}) == 88.0


def test_rejects_non_market_data():
    assert _parse_ltp({"type": "subscribe", "status": "success"}) is None


def test_rejects_zero_and_garbage_prices():
    assert _parse_ltp({"type": "market_data", "data": {"ltp": 0}}) is None
    assert _parse_ltp({"type": "market_data", "data": {"ltp": "n/a"}}) is None
    assert _parse_ltp({"type": "market_data", "data": None}) is None


def test_tick_age_is_never_negative():
    tick = Tick(price=100.0, source=SOURCE_WEBSOCKET, received_at_utc=NOW)
    assert tick.age_ms(NOW + timedelta(seconds=2)) == 2000
    assert tick.age_ms(NOW - timedelta(seconds=2)) == 0


def test_handshake_rejects_a_refusal(monkeypatch, monitor_settings):
    class FakeSocket:
        def __init__(self):
            self.sent = []

        def send(self, payload):
            self.sent.append(json.loads(payload))

        def recv(self, timeout=None):
            return json.dumps({"status": "error", "message": "Invalid API key"})

    feed = PriceFeed(monitor_settings, "NIFTY30SEP2625000CE", "NFO")
    socket = FakeSocket()
    try:
        feed._handshake(socket)
    except Exception as exc:  # FeedUnavailable
        assert "authentication refused" in str(exc)
    else:  # pragma: no cover - the handshake must not pass an error frame
        raise AssertionError("a refused authentication must raise")
    assert socket.sent[0]["action"] == "authenticate"
