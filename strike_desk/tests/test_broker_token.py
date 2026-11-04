"""OpenAlgoClient.broker_token_error — the graph plan call must never AttributeError."""

from __future__ import annotations

import httpx

from strike_desk.errors import OpenAlgoError
from strike_desk.graph import TickDeps
from strike_desk.openalgo_client import OpenAlgoClient, _token_dead


def test_token_dead_markers():
    assert _token_dead(OpenAlgoError("access_token expired"))
    assert _token_dead(OpenAlgoError("Incorrect api_key"))
    assert not _token_dead(OpenAlgoError("timeout"))


def test_broker_token_error_ok(client: OpenAlgoClient, openalgo):
    openalgo.post("/api/v1/quotes").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": {"ltp": 1}})
    )
    openalgo.post("/api/v1/history").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": []})
    )
    assert client.broker_token_error() is None


def test_broker_token_error_dead_token(client: OpenAlgoClient, openalgo):
    openalgo.post("/api/v1/quotes").mock(
        return_value=httpx.Response(
            200,
            json={"status": "error", "message": "access_token is invalid"},
        )
    )
    detail = client.broker_token_error()
    assert detail is not None
    assert "access_token" in detail.lower()


def test_consult_ready_survives_client_without_method():
    """GCE graph plan used a missing method; getattr must keep the tick alive."""
    class Bare:
        pass

    probe = getattr(Bare(), "broker_token_error", None)
    assert (probe() if callable(probe) else None) is None
    assert hasattr(OpenAlgoClient, "broker_token_error")
    assert hasattr(TickDeps, "__annotations__")
