"""Tradetron fan-out: mapping, official webhook, and the rule that a down TT never blocks OA."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from pydantic import SecretStr

from strike_desk.approval_gate import ApprovalGate
from strike_desk.execution_client import ExecutionClient
from strike_desk.journal import APPROVAL_AUTO_APPROVED, POSITION_FLAT
from strike_desk.openalgo_mirror import ORDER_MODE_AUTO
from strike_desk.tradetron_signal import (
    close_tradetron_client,
    notify_tradetron,
    option_side,
    signal_key,
)
from tests.approval_fixtures import SYMBOL, cleared_context, set_order_mode
from tests.conftest import BASE_URL
from tests.position_fixtures import FakeFeed, seed_chain
from tests.position_fixtures import today as fixture_today
from tests.test_position_monitor import _patch_price_feed, build, states, tick

OA_BASE = "http://127.0.0.1:5000"


def _mock_tt(router, **mock):
    return router.post(url__regex=r"https://api\.tradetron\.tech/api.*").mock(**mock)


def _place_ok(router, orderid: str = "SANDBOX-TT"):
    return router.post(f"{BASE_URL}/api/v1/placeorder").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "mode": "auto", "orderid": orderid}
        )
    )


@pytest.fixture(autouse=True)
def _close_tt_client():
    yield
    close_tradetron_client()


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("NIFTY13OCT2625300CE", "CE"),
        ("NIFTY13OCT2622400PE", "PE"),
        ("nifty13oct2622400pe", "PE"),
        ("NIFTY", None),
        ("", None),
    ],
)
def test_option_side_reads_the_openalgo_suffix(symbol, expected):
    assert option_side(symbol) == expected


@pytest.mark.parametrize(
    ("symbol", "kind", "expected"),
    [
        ("NIFTY13OCT2625300CE", "entry", "NIFTY50_CE_ATM_el"),
        ("NIFTY13OCT2625300CE", "exit", "NIFTY50_CE_ATM_xl"),
        ("NIFTY13OCT2622400PE", "entry", "NIFTY50_PE_ATM_el"),
        ("NIFTY13OCT2622400PE", "exit", "NIFTY50_PE_ATM_xl"),
        ("NIFTY", "entry", None),
    ],
)
def test_signal_key_maps_buy_atm_and_buy_exit(symbol, kind, expected):
    assert signal_key("NIFTY50", symbol, kind) == expected


def _enabled(settings, **extra):
    payload = {
        "tradetron_webhook_enabled": True,
        "tradetron_webhook_token": SecretStr("test-token"),
        "tradetron_webhook_url": "https://api.tradetron.tech/api",
        "tradetron_index_token": "NIFTY50",
    }
    payload.update(extra)
    return settings.model_copy(update=payload)


def test_disabled_fanout_never_opens_a_socket(settings):
    with respx.mock(assert_all_called=False) as router:
        router.post(url__regex=r".*").mock(return_value=httpx.Response(200, text="Ok"))
        assert notify_tradetron(settings, "NIFTY13OCT2622400PE", "entry") is False
        assert router.calls.call_count == 0


def test_missing_token_never_opens_a_socket(settings):
    armed = _enabled(settings, tradetron_webhook_token=SecretStr(""))
    with respx.mock(assert_all_called=False) as router:
        router.post(url__regex=r".*").mock(return_value=httpx.Response(200, text="Ok"))
        assert notify_tradetron(armed, "NIFTY13OCT2622400PE", "entry") is False
        assert router.calls.call_count == 0


def test_notify_posts_official_json_without_sid(settings):
    armed = _enabled(settings)
    with respx.mock(assert_all_called=True) as router:
        route = router.post(url__regex=r"https://api\.tradetron\.tech/api.*").mock(
            return_value=httpx.Response(200, text="Ok")
        )
        assert notify_tradetron(armed, "NIFTY13OCT2622400PE", "entry") is True
    request = route.calls.last.request
    assert "sid=" not in str(request.url)
    body = json.loads(request.content)
    assert body == {
        "auth-token": "test-token",
        "key": "NIFTY50_PE_ATM_el",
        "value": "1",
    }
    assert "sid" not in body
    assert request.headers["user-agent"] == "tt-webhook/1.0"


def test_a_down_tradetron_is_swallowed(settings):
    armed = _enabled(settings)
    with respx.mock(assert_all_called=True) as router:
        router.post(url__regex=r"https://api\.tradetron\.tech/api.*").mock(
            side_effect=httpx.ConnectError("refused")
        )
        assert notify_tradetron(armed, "NIFTY13OCT2625300CE", "exit") is False


def test_an_unattended_place_fans_out_entry(execution_settings, journal, mirror, openalgo_db):
    set_order_mode(openalgo_db, ORDER_MODE_AUTO)
    settings = _enabled(execution_settings, autonomy="unattended")
    with respx.mock(assert_all_called=False) as router:
        _place_ok(router)
        tt = _mock_tt(router, return_value=httpx.Response(200, text="Ok"))
        client = ExecutionClient(settings)
        try:
            result = ApprovalGate(settings, journal, mirror, client).submit(cleared_context())
        finally:
            client.close()
    assert result.status == APPROVAL_AUTO_APPROVED
    assert result.symbol == SYMBOL
    assert tt.call_count == 1
    body = json.loads(tt.calls.last.request.content)
    assert body["key"] == "NIFTY50_CE_ATM_el"
    assert "sid" not in body


def test_a_down_tradetron_does_not_block_the_openalgo_place(
    execution_settings, journal, mirror, openalgo_db
):
    set_order_mode(openalgo_db, ORDER_MODE_AUTO)
    settings = _enabled(execution_settings, autonomy="unattended")
    with respx.mock(assert_all_called=False) as router:
        place = _place_ok(router, orderid="SANDBOX-OA")
        _mock_tt(router, side_effect=httpx.ReadTimeout("tradetron down"))
        client = ExecutionClient(settings)
        try:
            result = ApprovalGate(settings, journal, mirror, client).submit(cleared_context())
        finally:
            client.close()
    assert result.status == APPROVAL_AUTO_APPROVED
    assert place.call_count == 1
    assert journal.latest_approval() is not None
    assert journal.latest_approval().status == APPROVAL_AUTO_APPROVED
    assert journal.latest_approval().broker_order_id == "SANDBOX-OA"


def test_stop_exit_posts_xl_and_flats_even_if_tradetron_is_down(
    monitor_settings, journal, monkeypatch
):
    settings = _enabled(monitor_settings)
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{OA_BASE}/api/v1/positionbook").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": [
                        {"symbol": "NIFTY30SEP2625000CE", "quantity": 75, "average_price": 102.5}
                    ],
                },
            )
        )
        close = router.post(f"{OA_BASE}/api/v1/closeposition").mock(
            return_value=httpx.Response(200, json={"status": "success"})
        )
        tt = _mock_tt(router, side_effect=httpx.ConnectError("refused"))
        monitor = build(settings, journal)
        try:
            ids = seed_chain(journal, stop=100.0, target=200.0)
            _patch_price_feed(monkeypatch, lambda *a, **k: FakeFeed(tick(99.0)))
            monitor._adopt(ids["order_id"])
            monitor._evaluate()
        finally:
            monitor.close()
    assert tt.call_count == 1
    body = json.loads(tt.calls.last.request.content)
    assert body["key"] == "NIFTY50_CE_ATM_xl"
    position_id = journal.list_positions(fixture_today())[0].position_id
    assert POSITION_FLAT in states(journal, position_id)
    assert close.call_count == 1


def test_a_failed_exit_does_not_fan_out(monitor_settings, journal, monkeypatch):
    settings = _enabled(monitor_settings)
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{OA_BASE}/api/v1/positionbook").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": [
                        {"symbol": "NIFTY30SEP2625000CE", "quantity": 75, "average_price": 102.5}
                    ],
                },
            )
        )
        router.post(f"{OA_BASE}/api/v1/closeposition").mock(
            return_value=httpx.Response(500, json={"status": "error", "message": "boom"})
        )
        router.post(f"{OA_BASE}/api/v1/placeorder").mock(
            return_value=httpx.Response(500, json={"status": "error", "message": "boom"})
        )
        tt = _mock_tt(router, return_value=httpx.Response(200, text="Ok"))
        monitor = build(settings, journal)
        try:
            ids = seed_chain(journal, stop=100.0, target=200.0)
            _patch_price_feed(monkeypatch, lambda *a, **k: FakeFeed(tick(90.0)))
            monitor._adopt(ids["order_id"])
            monitor._evaluate()
        finally:
            monitor.close()
    assert tt.call_count == 0
    position_id = journal.list_positions(fixture_today())[0].position_id
    assert POSITION_FLAT not in states(journal, position_id)
