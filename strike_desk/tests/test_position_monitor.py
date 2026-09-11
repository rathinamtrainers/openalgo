"""Adoption, exit, reconciliation and escalation, on a real journal."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from freezegun import freeze_time

from strike_desk.execution_client import ExecutionClient
from strike_desk.exit_executor import ExitExecutor
from strike_desk.journal import (
    EXIT_MANUAL,
    POSITION_ARMED,
    POSITION_FLAT,
    POSITION_ORPHANED,
    POSITION_STOOD_DOWN,
)
from strike_desk.openalgo_client import OpenAlgoClient
from strike_desk.position_monitor import PositionMonitor
from strike_desk.price_feed import SOURCE_WEBSOCKET, Tick
from tests.position_fixtures import SEED_DAY, FakeFeed, seed_chain

BASE = "http://127.0.0.1:5000"
SYMBOL = "NIFTY30SEP2625000CE"


@pytest.fixture(autouse=True)
def _morning_session():
    """Hold the clock at 11:30 IST so a 14:45 time-stop is still ahead of the evaluator."""
    with freeze_time("2026-09-08T06:00:00+00:00", ignore=["threading"]):
        yield


def _patch_price_feed(monkeypatch, factory) -> None:
    """Patch the module the collected PositionMonitor class actually closes over.

    Guardrail tests temporarily reimport this module; setattr on the live sys.modules
    entry can miss the class object this file imported at collection time.
    """
    monkeypatch.setattr(inspect.getmodule(PositionMonitor), "PriceFeed", factory)


@pytest.fixture(autouse=True)
def _no_real_feed(monkeypatch):
    """Never let a monitor test open the real WebSocket proxy — a leaked thread trips respx."""

    def factory(*_args, **_kwargs):
        return FakeFeed(tick(110.0))

    _patch_price_feed(monkeypatch, factory)


@pytest.fixture
def oa_http():
    """One router for the whole test, including monitor construction and teardown."""
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


class FakeMirror:
    def analyze_mode(self) -> bool:
        return True

    def order_mode(self) -> str:
        return "semi_auto"


def build(settings, journal) -> PositionMonitor:
    client = OpenAlgoClient(settings)
    executor = ExitExecutor(settings, ExecutionClient(settings), client, "strike-desk-NIFTY")
    return PositionMonitor(settings, journal, client, executor, FakeMirror())


@pytest.fixture
def monitor(oa_http, monitor_settings, journal):
    instance = build(monitor_settings, journal)
    yield instance
    instance.close()


def mock_book(router, quantity: int = 75, average: float = 102.5) -> None:
    data = [] if quantity == 0 else [
        {"symbol": SYMBOL, "quantity": quantity, "average_price": average}
    ]
    router.post("/api/v1/positionbook").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": data})
    )


def tick(price: float) -> Tick:
    return Tick(price=price, source=SOURCE_WEBSOCKET, received_at_utc=datetime.now(tz=UTC))


def states(journal, position_id: str) -> list[str]:
    return [row.state for row in journal.position_states(position_id)]


def test_adoption_stamps_levels_from_the_proposal(oa_http, monitor, journal, monkeypatch):
    ids = seed_chain(journal, stop=80.0, target=140.0, time_stop_ist="14:45")
    mock_book(oa_http)
    _patch_price_feed(monkeypatch, lambda *a, **k: FakeFeed(tick(110.0)))
    monitor._adopt(ids["order_id"])

    live = journal.live_position()
    assert live is not None
    assert live.symbol == SYMBOL
    assert live.stop_price == 80.0 and live.target_price == 140.0
    assert live.quantity == 75 and live.entry_price == 102.5
    assert POSITION_ARMED in states(journal, live.position_id)


def test_a_stop_breach_exits_and_records_latency(
    oa_http, monitor, monitor_settings, journal, monkeypatch
):
    ids = seed_chain(journal, stop=100.0, target=200.0)
    mock_book(oa_http)
    feed = FakeFeed(tick(99.0))
    _patch_price_feed(monkeypatch, lambda *a, **k: feed)
    oa_http.post("/api/v1/closeposition").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    monitor._adopt(ids["order_id"])
    monitor._evaluate()

    position_id = journal.list_positions(SEED_DAY)[0].position_id
    assert POSITION_FLAT in states(journal, position_id)
    exits = journal.exits_for_position(position_id)
    assert len(exits) == 1
    assert exits[0].reason == "stop"
    assert exits[0].level_price == 100.0 and exits[0].observed_price == 99.0
    assert 0 <= exits[0].latency_ms <= monitor_settings.exit_latency_budget_ms
    assert exits[0].feed_source == SOURCE_WEBSOCKET
    assert feed.closed


def test_the_time_stop_exits_between_the_levels(oa_http, monitor, journal, monkeypatch):
    ids = seed_chain(journal, stop=10.0, target=1000.0, time_stop_ist="00:01")
    mock_book(oa_http)
    _patch_price_feed(monkeypatch, lambda *a, **k: FakeFeed(tick(110.0)))
    oa_http.post("/api/v1/closeposition").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    monitor._adopt(ids["order_id"])
    monitor._evaluate()

    position_id = journal.list_positions(SEED_DAY)[0].position_id
    assert journal.exits_for_position(position_id)[0].reason in {"time-stop", "session-deadline"}


def test_a_manual_close_stands_the_monitor_down(oa_http, monitor, journal, monkeypatch):
    ids = seed_chain(journal)
    mock_book(oa_http)
    feed = FakeFeed(tick(110.0))
    _patch_price_feed(monkeypatch, lambda *a, **k: feed)
    monitor._adopt(ids["order_id"])

    oa_http.post("/api/v1/positionbook").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": []})
    )
    monitor._last_reconcile = 0.0
    monitor._reconcile_if_due()

    position_id = journal.list_positions(SEED_DAY)[0].position_id
    rows = journal.position_states(position_id)
    stood_down = [row for row in rows if row.state == POSITION_STOOD_DOWN]
    assert stood_down and stood_down[0].exit_reason == EXIT_MANUAL
    assert journal.exits_for_position(position_id) == []
    assert feed.closed


def test_a_failing_exit_escalates_and_kills(
    oa_http, monitor, monitor_settings, journal, monkeypatch
):
    ids = seed_chain(journal, stop=100.0, target=200.0)
    mock_book(oa_http)
    _patch_price_feed(monkeypatch, lambda *a, **k: FakeFeed(tick(90.0)))
    oa_http.post("/api/v1/closeposition").mock(
        return_value=httpx.Response(500, json={"status": "error", "message": "boom"})
    )
    oa_http.post("/api/v1/placeorder").mock(
        return_value=httpx.Response(500, json={"status": "error", "message": "boom"})
    )
    monitor._adopt(ids["order_id"])
    monitor._evaluate()

    position_id = journal.list_positions(SEED_DAY)[0].position_id
    assert POSITION_ORPHANED in states(journal, position_id)
    assert len(journal.exits_for_position(position_id)) == monitor_settings.exit_max_attempts
    assert monitor_settings.kill_switch_path.exists()


def test_a_blackout_exits_at_market(oa_http, monitor, journal, monkeypatch):
    ids = seed_chain(journal, stop=10.0, target=1000.0)
    mock_book(oa_http)
    stale = Tick(
        price=110.0,
        source=SOURCE_WEBSOCKET,
        received_at_utc=datetime.now(tz=UTC) - timedelta(seconds=600),
    )
    _patch_price_feed(monkeypatch, lambda *a, **k: FakeFeed(stale))
    oa_http.post("/api/v1/quotes").mock(side_effect=httpx.ConnectError("down"))
    oa_http.post("/api/v1/closeposition").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    monitor._adopt(ids["order_id"])
    monitor._blackout_since = datetime.now(tz=UTC) - timedelta(seconds=600)
    monitor._evaluate()

    position_id = journal.list_positions(SEED_DAY)[0].position_id
    assert journal.exits_for_position(position_id)[0].reason == "feed-blackout"


def test_a_stale_feed_falls_back_to_quotes(oa_http, monitor, journal, monkeypatch):
    ids = seed_chain(journal, stop=100.0, target=200.0)
    mock_book(oa_http)
    stale = Tick(
        price=110.0,
        source=SOURCE_WEBSOCKET,
        received_at_utc=datetime.now(tz=UTC) - timedelta(seconds=60),
    )
    _patch_price_feed(monkeypatch, lambda *a, **k: FakeFeed(stale))
    oa_http.post("/api/v1/quotes").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": {"ltp": 95.0}})
    )
    oa_http.post("/api/v1/closeposition").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    monitor._adopt(ids["order_id"])
    monitor._evaluate()

    position_id = journal.list_positions(SEED_DAY)[0].position_id
    exits = journal.exits_for_position(position_id)
    assert exits[0].reason == "stop" and exits[0].feed_source == "quotes"


def test_adoption_is_idempotent(oa_http, monitor, journal, monkeypatch):
    ids = seed_chain(journal)
    mock_book(oa_http)
    _patch_price_feed(monkeypatch, lambda *a, **k: FakeFeed(tick(110.0)))
    monitor._adopt(ids["order_id"])
    monitor._position = None
    monitor._adopt(ids["order_id"])
    assert len(journal.list_positions(SEED_DAY)) == 2  # adopted + armed, once


def test_an_unexplained_broker_position_is_not_adopted(oa_http, monitor, journal):
    mock_book(oa_http)
    monitor._adopt_unadopted_orders()
    assert journal.live_position() is None
