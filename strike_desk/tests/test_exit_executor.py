"""The preflight and the two-rung ladder against a faked OpenAlgo."""

from __future__ import annotations

import httpx
import respx

from strike_desk.execution_client import ExecutionClient
from strike_desk.exit_executor import (
    PATH_CLOSE_POSITION,
    PATH_TARGETED_SELL,
    ExitExecutor,
    exit_path_status,
)
from strike_desk.journal import EXIT_GATED, EXIT_REFUSED, EXIT_SUBMITTED
from strike_desk.openalgo_client import OpenAlgoClient

BASE = "http://127.0.0.1:5000"
SYMBOL = "NIFTY30SEP2625000CE"


class FakeMirror:
    def __init__(self, analyze: bool, mode: str, raises: Exception | None = None) -> None:
        self._analyze = analyze
        self._mode = mode
        self._raises = raises

    def analyze_mode(self) -> bool:
        if self._raises:
            raise self._raises
        return self._analyze

    def order_mode(self) -> str:
        if self._raises:
            raise self._raises
        return self._mode


def build(settings) -> ExitExecutor:
    return ExitExecutor(
        settings, ExecutionClient(settings), OpenAlgoClient(settings), "strike-desk-NIFTY"
    )


def test_preflight_allows_analyze_mode():
    status = exit_path_status(FakeMirror(True, "semi_auto"))
    assert status.ok and status.analyze_mode is True


def test_preflight_allows_auto_mode():
    assert exit_path_status(FakeMirror(False, "auto")).ok


def test_preflight_blocks_live_semi_auto():
    status = exit_path_status(FakeMirror(False, "semi_auto"))
    assert not status.ok
    assert "semi-auto" in status.detail


def test_preflight_fails_closed_when_the_mirror_is_unreadable():
    from strike_desk.errors import MirrorUnavailable

    status = exit_path_status(FakeMirror(True, "auto", MirrorUnavailable("no file")))
    assert not status.ok and "unverifiable" in status.detail


@respx.mock
def test_exclusive_book_uses_closeposition(monitor_settings):
    respx.post(f"{BASE}/api/v1/positionbook").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": [{"symbol": SYMBOL, "quantity": 75}]}
        )
    )
    close = respx.post(f"{BASE}/api/v1/closeposition").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    attempt = build(monitor_settings).fire(SYMBOL, "NFO", 75)
    assert attempt.status == EXIT_SUBMITTED
    assert attempt.path == PATH_CLOSE_POSITION
    assert close.called


@respx.mock
def test_a_foreign_position_forces_a_targeted_sell(monitor_settings):
    respx.post(f"{BASE}/api/v1/positionbook").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {"symbol": SYMBOL, "quantity": 75},
                    {"symbol": "SBIN", "quantity": 100},
                ],
            },
        )
    )
    place = respx.post(f"{BASE}/api/v1/placeorder").mock(
        return_value=httpx.Response(200, json={"status": "success", "orderid": "B-9"})
    )
    attempt = build(monitor_settings).fire(SYMBOL, "NFO", 75)
    assert attempt.status == EXIT_SUBMITTED
    assert attempt.path == PATH_TARGETED_SELL
    body = place.calls[0].request.content.decode().replace(" ", "")
    assert '"action":"SELL"' in body
    assert '"pricetype":"MARKET"' in body


@respx.mock
def test_a_refused_closeposition_falls_through(monitor_settings):
    respx.post(f"{BASE}/api/v1/positionbook").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": [{"symbol": SYMBOL, "quantity": 75}]}
        )
    )
    respx.post(f"{BASE}/api/v1/closeposition").mock(
        return_value=httpx.Response(
            403, json={"status": "error", "message": "not allowed in Semi-Auto mode"}
        )
    )
    place = respx.post(f"{BASE}/api/v1/placeorder").mock(
        return_value=httpx.Response(200, json={"status": "success", "orderid": "B-9"})
    )
    attempt = build(monitor_settings).fire(SYMBOL, "NFO", 75)
    assert attempt.status == EXIT_SUBMITTED and place.called


@respx.mock
def test_a_queued_exit_is_gated_not_submitted(monitor_settings):
    respx.post(f"{BASE}/api/v1/positionbook").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": [{"symbol": SYMBOL, "quantity": 75}, {"symbol": "SBIN", "quantity": 5}],
            },
        )
    )
    respx.post(f"{BASE}/api/v1/placeorder").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "mode": "semi_auto", "pending_order_id": 42}
        )
    )
    attempt = build(monitor_settings).fire(SYMBOL, "NFO", 75)
    assert attempt.status == EXIT_GATED
    assert "42" in attempt.detail


@respx.mock
def test_a_transport_failure_is_a_failure_not_a_success(monitor_settings):
    respx.post(f"{BASE}/api/v1/positionbook").mock(side_effect=httpx.ConnectError("down"))
    respx.post(f"{BASE}/api/v1/placeorder").mock(side_effect=httpx.ConnectError("down"))
    attempt = build(monitor_settings).fire(SYMBOL, "NFO", 75)
    assert attempt.status not in {EXIT_SUBMITTED, EXIT_REFUSED}


def test_a_non_positive_quantity_is_refused(monitor_settings):
    assert build(monitor_settings).fire(SYMBOL, "NFO", 0).status == "failed"
