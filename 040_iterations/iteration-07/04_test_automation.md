# Iteration 07 — Test Automation

The suite this slice adds has an unusual centre. Every other iteration's hardest tests were
about a model's judgement; this one has no model in it at all, so there is no eval set to score
and no prompt to regress. What replaces them is a **replay regression suite**: a frozen file of
tick sequences — prices, timestamps, feed states — played through the real evaluator, each with
the exit it must produce. That file is this slice's equivalent of an eval set, and it is what
stops someone "simplifying" the precedence rules six months from now: change the order in which
levels are checked and the gap scenario fails in CI.

Everything runs on `pytest==9.1.1` with `respx==0.23.1` for the HTTP edges and
`freezegun==1.5.5` for the clock, exactly as iterations 01 to 06 use them — no framework is
added, because none is needed and a second test runner is a second thing to keep current.

## What is real and what is faked

The **evaluator, the journal, the exit ladder's decision logic and the monitor's state machine
are real** in every test below. What is faked is only what crosses a socket: OpenAlgo's HTTP
surface is served by `respx`, and the WebSocket proxy is replaced by a fake connection object
that the feed's own `connect` call returns. Nothing mocks `levels.evaluate`, nothing mocks the
journal, and nothing mocks `PositionMonitor` — a test that mocks the thing under test proves
the mock.

The split by layer: **unit** tests cover `levels.py`, the feed's frame parsing and the journal's
new tables; **integration** tests cover the executor against a faked OpenAlgo and the monitor
against a faked feed plus a real SQLite journal; the **regression** suite replays scenarios; and
the **guardrail** tests assert the two structural properties this slice must never lose — the
monitor imports no reasoning-plane module, and the exit path is preflighted before an entry.

Coverage intent is blunt: `levels.py` at 100 % of branches, `position_monitor.py` and
`exit_executor.py` above 85 % of lines, and every acceptance criterion mapped in §7.

## 1. Fixtures

### `strike_desk/tests/position_fixtures.py`

Shared builders, plus the small CLI Block B of the manual cases uses to seed a proposal with
levels you choose.

```python
"""Fixtures and a seeding CLI for the position-monitor tests."""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from strike_desk.config import IST, Settings, get_settings
from strike_desk.journal import Journal, SCHEMA_VERSION
from strike_desk.levels import ExitLevels

STRATEGY = "strike-desk-NIFTY"
SYMBOL = "NIFTY30SEP2625000CE"


def levels(
    stop: float = 80.0,
    target: float = 140.0,
    minutes_ahead: float = 30.0,
    reason: str = "time-stop",
) -> ExitLevels:
    return ExitLevels(
        stop_price=stop,
        target_price=target,
        time_stop_utc=datetime.now(tz=UTC) + timedelta(minutes=minutes_ahead),
        time_stop_reason=reason,
    )


def today() -> str:
    return datetime.now(tz=IST).date().isoformat()


def seed_chain(
    journal: Journal,
    *,
    stop: float = 80.0,
    target: float = 140.0,
    time_stop_ist: str = "14:45",
    quantity: int = 75,
    symbol: str = SYMBOL,
) -> dict[str, str]:
    """Write the proposal, approval and order rows one adoption needs, and return the ids."""
    now = datetime.now(tz=UTC)
    day = today()
    tick_id = f"test:{uuid.uuid4()}"
    trace_id = uuid.uuid4().hex[:32]
    proposal_id = str(uuid.uuid4())
    approval_id = str(uuid.uuid4())
    order_id = str(uuid.uuid4())

    journal.record_proposal(
        proposal_id=proposal_id,
        tick_id=tick_id,
        trace_id=trace_id,
        created_at_utc=now,
        trading_day=day,
        index_symbol="NIFTY",
        source="tick",
        status="proposed",
        regime_label="trending",
        regime_confidence=0.8,
        direction="bullish",
        symbol=symbol,
        expiry="2026-09-30",
        strike=25000.0,
        option_type="CE",
        lots=1,
        lot_size=quantity,
        quantity=quantity,
        entry_price_low=100.0,
        entry_price_high=105.0,
        delta=0.45,
        theta_per_day=-4.2,
        implied_volatility=14.5,
        open_interest=250000,
        breakeven=25105.0,
        stop_price=stop,
        target_price=target,
        time_stop_ist=time_stop_ist,
        rationale="seeded by the test fixtures for the position monitor",
        evidence_json="[]",
        playbook_artifact="pb-1",
        playbook_verdict="pass",
        violations_json="[]",
        defect=None,
        tool_call_count=0,
        tool_error_count=0,
        rejected_tool_count=0,
        model_calls=0,
        model_version="test",
        input_tokens=0,
        output_tokens=0,
        token_cost_micros=0,
        prompt_name="options_strategist",
        prompt_version="v1",
        prompt_digest="0" * 64,
        prompt_set_version="ps-1",
        latency_ms=0,
        schema_version=SCHEMA_VERSION,
    )
    journal.record_approval(
        approval_id=approval_id,
        status="approved",
        tick_id=tick_id,
        trace_id=trace_id,
        tick_trace_id=trace_id,
        proposal_id=proposal_id,
        verdict_id=None,
        created_at_utc=now,
        trading_day=day,
        index_symbol="NIFTY",
        symbol=symbol,
        exchange="NFO",
        action="BUY",
        product="MIS",
        lots=1,
        lot_size=quantity,
        quantity=quantity,
        limit_price=105.0,
        band_low=100.0,
        band_high=105.0,
        pending_order_id=1,
        strategy_tag=STRATEGY,
        deadline_utc=now + timedelta(minutes=5),
        wait_seconds=3.0,
        approved_by="tester",
        resolved_at_ist=None,
        broker_order_id="B-1",
        withdrawal=None,
        detail="seeded",
        payload_json="{}",
        response_json="{}",
        defect=False,
    )
    journal.record_order(
        order_id=order_id,
        approval_id=approval_id,
        tick_id=tick_id,
        trace_id=trace_id,
        created_at_utc=now,
        trading_day=day,
        pending_order_id=1,
        broker_order_id="B-1",
        symbol=symbol,
        exchange="NFO",
        action="BUY",
        product="MIS",
        price_type="LIMIT",
        limit_price=105.0,
        lots=1,
        lot_size=quantity,
        quantity=quantity,
        order_status="complete",
        average_price=102.5,
        raw_json="{}",
    )
    return {
        "proposal_id": proposal_id,
        "approval_id": approval_id,
        "order_id": order_id,
        "tick_id": tick_id,
    }


class FakeFeed:
    """Stands in for PriceFeed: the monitor only ever asks it for the last tick."""

    def __init__(self, tick: Any = None) -> None:
        self.tick = tick
        self.closed = False
        self.started = False

    def start(self) -> None:
        self.started = True

    def wait_for_first_tick(self, timeout: float) -> bool:
        return self.tick is not None

    def last_tick(self) -> Any:
        return self.tick

    def is_connected(self) -> bool:
        return not self.closed

    def close(self) -> None:
        self.closed = True


def _seed_cli(args: argparse.Namespace) -> int:
    settings: Settings = get_settings()
    journal = Journal(settings.db_path)
    try:
        journal.create_schema()
        ids = seed_chain(
            journal,
            stop=args.stop_offset and float(args.premium - args.stop_offset) or 80.0,
            target=float(args.premium + args.target_offset),
            time_stop_ist=args.time_stop,
        )
        print(json.dumps(ids, indent=2))
    finally:
        journal.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="position_fixtures")
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed", help="write a proposal/approval/order chain to the journal")
    seed.add_argument("--premium", type=float, default=100.0)
    seed.add_argument("--stop-offset", type=float, default=2.0)
    seed.add_argument("--target-offset", type=float, default=40.0)
    seed.add_argument("--time-stop", default="14:45")
    args = parser.parse_args(argv)
    return _seed_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

### `strike_desk/tests/conftest.py` — additions

Two fixtures join the existing file: a journal on a temporary path, and a settings object whose
monitor knobs are fast enough that a test is not a wait.

```python
import pytest

from strike_desk.config import Settings
from strike_desk.journal import Journal


@pytest.fixture
def journal(tmp_path):
    journal = Journal(tmp_path / "strike_desk.db")
    journal.create_schema()
    yield journal
    journal.close()


@pytest.fixture
def monitor_settings(tmp_path) -> Settings:
    return Settings(
        openalgo_api_key="test-key-0123456789",
        openalgo_user="tester",
        openalgo_db_path=tmp_path / "openalgo.db",
        state_dir=tmp_path / "state",
        execution_enabled=True,
        monitor_enabled=True,
        monitor_poll_seconds=0.05,
        exit_retry_seconds=0.01,
        fill_deadline_seconds=30,
        feed_stale_seconds=5.0,
        feed_blackout_seconds=10.0,
        reconcile_interval_seconds=0.0,
    )
```

## 2. Unit tests — the evaluator

This is the file with the most tests and the fewest lines of setup, which is the point of
keeping `levels.py` pure.

### `strike_desk/tests/test_levels.py`

```python
"""The exit evaluator: every branch, every boundary, every precedence rule."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from strike_desk.config import IST
from strike_desk.errors import LevelsUnavailable
from strike_desk.levels import ExitLevels, distances, evaluate, resolve_time_stop

NOW = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)


def build(stop: float = 80.0, target: float = 140.0, minutes: float = 30.0) -> ExitLevels:
    return ExitLevels(
        stop_price=stop,
        target_price=target,
        time_stop_utc=NOW + timedelta(minutes=minutes),
        time_stop_reason="time-stop",
    )


def test_between_levels_holds():
    assert evaluate(build(), 110.0, NOW) is None


@pytest.mark.parametrize("price", [80.0, 79.99, 0.05])
def test_stop_breach_at_or_below(price):
    trigger = evaluate(build(), price, NOW)
    assert trigger is not None
    assert trigger.reason == "stop"
    assert trigger.level_price == 80.0
    assert trigger.observed_price == price


@pytest.mark.parametrize("price", [140.0, 140.01, 1000.0])
def test_target_breach_at_or_above(price):
    trigger = evaluate(build(), price, NOW)
    assert trigger is not None and trigger.reason == "target"


def test_time_stop_fires_regardless_of_price():
    levels = build(minutes=-1)
    trigger = evaluate(levels, 110.0, NOW)
    assert trigger is not None and trigger.reason == "time-stop"


def test_time_beats_price():
    levels = build(minutes=-1)
    trigger = evaluate(levels, 10.0, NOW)
    assert trigger.reason == "time-stop"


def test_stop_beats_target_when_both_breached():
    levels = ExitLevels(
        stop_price=100.0,
        target_price=100.0001,
        time_stop_utc=NOW + timedelta(minutes=30),
        time_stop_reason="time-stop",
    )
    assert evaluate(levels, 100.0, NOW).reason == "stop"


def test_missing_price_still_honours_the_clock():
    assert evaluate(build(), None, NOW) is None
    assert evaluate(build(minutes=-1), None, NOW).reason == "time-stop"


@pytest.mark.parametrize(
    "stop,target",
    [(0.0, 140.0), (-1.0, 140.0), (140.0, 80.0), (100.0, 100.0)],
)
def test_impossible_levels_are_refused(stop, target):
    with pytest.raises(LevelsUnavailable):
        ExitLevels(stop, target, NOW + timedelta(minutes=5), "time-stop")


def test_naive_time_stop_is_refused():
    with pytest.raises(LevelsUnavailable):
        ExitLevels(80.0, 140.0, datetime(2026, 9, 8, 12, 0), "time-stop")


def test_unknown_time_stop_reason_is_refused():
    with pytest.raises(LevelsUnavailable):
        ExitLevels(80.0, 140.0, NOW, "vibes")


def test_resolve_time_stop_takes_the_earlier_clock():
    day = date(2026, 9, 8)
    stamp, reason = resolve_time_stop("15:40", time(15, 10), day)
    assert reason == "session-deadline"
    assert stamp.astimezone(IST).strftime("%H:%M") == "15:10"

    stamp, reason = resolve_time_stop("14:20", time(15, 10), day)
    assert reason == "time-stop"
    assert stamp.astimezone(IST).strftime("%H:%M") == "14:20"


def test_resolve_time_stop_without_a_proposal_time():
    stamp, reason = resolve_time_stop(None, time(15, 10), date(2026, 9, 8))
    assert reason == "session-deadline"
    assert stamp.astimezone(IST).strftime("%H:%M") == "15:10"


def test_resolve_time_stop_rejects_garbage():
    with pytest.raises(LevelsUnavailable):
        resolve_time_stop("half past three", time(15, 10), date(2026, 9, 8))


def test_distances_are_signed_from_the_price():
    gaps = distances(build(), 110.0)
    assert gaps == {"to_stop": 30.0, "to_target": 30.0}
    assert distances(build(), None) == {"to_stop": None, "to_target": None}
```

## 3. Unit tests — the feed and the journal

The feed's socket is not exercised here; its **frame parsing and its liveness arithmetic** are,
because those are the parts that can silently accept a price that is not a price.

### `strike_desk/tests/test_price_feed.py`

```python
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
```

### `strike_desk/tests/test_journal_positions.py`

```python
"""The two new tables: append-only, unique-by-state, queryable."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from strike_desk.errors import AlreadyJournalled
from strike_desk.journal import POSITION_ADOPTED, POSITION_FLAT, SCHEMA_VERSION
from tests.position_fixtures import seed_chain, today


def base_row(position_id: str, state: str) -> dict:
    return {
        "position_id": position_id,
        "state": state,
        "order_id": None,
        "approval_id": None,
        "proposal_id": None,
        "tick_id": None,
        "trace_id": "0" * 32,
        "created_at_utc": datetime.now(tz=UTC),
        "trading_day": today(),
        "symbol": "NIFTY30SEP2625000CE",
        "exchange": "NFO",
        "product": "MIS",
        "quantity": 75,
        "entry_price": 102.5,
        "stop_price": 80.0,
        "target_price": 140.0,
        "time_stop_utc": datetime.now(tz=UTC),
        "theta_per_day": -4.2,
        "exit_reason": None,
        "realised_pnl": None,
        "detail": "test",
        "defect": False,
    }


def test_schema_version_is_seven(journal):
    assert SCHEMA_VERSION == 7
    assert {"positions", "exits"} <= set(journal.table_names())


def test_a_state_may_be_written_once(journal):
    position_id = str(uuid.uuid4())
    journal.record_position_state(**base_row(position_id, POSITION_ADOPTED))
    with pytest.raises(AlreadyJournalled):
        journal.record_position_state(**base_row(position_id, POSITION_ADOPTED))


def test_live_position_ignores_terminal_ones(journal):
    live = str(uuid.uuid4())
    done = str(uuid.uuid4())
    journal.record_position_state(**base_row(done, POSITION_ADOPTED))
    journal.record_position_state(**base_row(done, POSITION_FLAT))
    journal.record_position_state(**base_row(live, POSITION_ADOPTED))
    found = journal.live_position()
    assert found is not None and found.position_id == live


def test_unadopted_orders_finds_a_filled_order(journal):
    ids = seed_chain(journal)
    orphans = journal.unadopted_orders(today())
    assert [order.order_id for order in orphans] == [ids["order_id"]]

    journal.record_position_state(
        **{**base_row(str(uuid.uuid4()), POSITION_ADOPTED), "order_id": ids["order_id"]}
    )
    assert journal.unadopted_orders(today()) == []


def test_rows_cannot_be_updated_or_deleted(journal):
    position_id = str(uuid.uuid4())
    journal.record_position_state(**base_row(position_id, POSITION_ADOPTED))
    with journal.session_scope() as session:
        with pytest.raises(Exception):
            session.execute(
                __import__("sqlalchemy").text("UPDATE positions SET quantity = 1")
            )
```

## 4. Integration tests — the exit ladder

`respx` serves OpenAlgo. The ladder's logic is real, and so is the classification of a queued
placement as `gated` — the failure this slice must never treat as success.

### `strike_desk/tests/test_exit_executor.py`

```python
"""The preflight and the two-rung ladder against a faked OpenAlgo."""

from __future__ import annotations

import httpx
import pytest
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
    body = place.calls[0].request.content.decode()
    assert '"action": "SELL"' in body.replace(" ", " ")
    assert '"pricetype": "MARKET"' in body.replace(" ", " ")


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
```

## 5. Integration tests — the monitor

A real journal, a real evaluator, a fake feed and a faked OpenAlgo. These are the tests that
say the reflex works end to end.

### `strike_desk/tests/test_position_monitor.py`

```python
"""Adoption, exit, reconciliation and escalation, on a real journal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import respx

from strike_desk.exit_executor import ExitExecutor
from strike_desk.execution_client import ExecutionClient
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
from tests.position_fixtures import FakeFeed, seed_chain, today

BASE = "http://127.0.0.1:5000"
SYMBOL = "NIFTY30SEP2625000CE"


class FakeMirror:
    def analyze_mode(self) -> bool:
        return True

    def order_mode(self) -> str:
        return "semi_auto"


def build(settings, journal) -> PositionMonitor:
    client = OpenAlgoClient(settings)
    executor = ExitExecutor(settings, ExecutionClient(settings), client, "strike-desk-NIFTY")
    return PositionMonitor(settings, journal, client, executor, FakeMirror())


def mock_book(quantity: int = 75, average: float = 102.5) -> None:
    data = [] if quantity == 0 else [
        {"symbol": SYMBOL, "quantity": quantity, "average_price": average}
    ]
    respx.post(f"{BASE}/api/v1/positionbook").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": data})
    )


def tick(price: float) -> Tick:
    return Tick(price=price, source=SOURCE_WEBSOCKET, received_at_utc=datetime.now(tz=UTC))


def states(journal, position_id: str) -> list[str]:
    return [row.state for row in journal.position_states(position_id)]


@respx.mock
def test_adoption_stamps_levels_from_the_proposal(monitor_settings, journal, monkeypatch):
    ids = seed_chain(journal, stop=80.0, target=140.0, time_stop_ist="14:45")
    mock_book()
    monkeypatch.setattr(
        "strike_desk.position_monitor.PriceFeed", lambda *a, **k: FakeFeed(tick(110.0))
    )
    monitor = build(monitor_settings, journal)
    monitor._adopt(ids["order_id"])

    live = journal.live_position()
    assert live is not None
    assert live.symbol == SYMBOL
    assert live.stop_price == 80.0 and live.target_price == 140.0
    assert live.quantity == 75 and live.entry_price == 102.5
    assert POSITION_ARMED in states(journal, live.position_id)


@respx.mock
def test_a_stop_breach_exits_and_records_latency(monitor_settings, journal, monkeypatch):
    ids = seed_chain(journal, stop=100.0, target=200.0)
    mock_book()
    feed = FakeFeed(tick(99.0))
    monkeypatch.setattr("strike_desk.position_monitor.PriceFeed", lambda *a, **k: feed)
    respx.post(f"{BASE}/api/v1/closeposition").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    monitor = build(monitor_settings, journal)
    monitor._adopt(ids["order_id"])
    monitor._evaluate()

    position_id = journal.list_positions(today())[0].position_id
    assert POSITION_FLAT in states(journal, position_id)
    exits = journal.exits_for_position(position_id)
    assert len(exits) == 1
    assert exits[0].reason == "stop"
    assert exits[0].level_price == 100.0 and exits[0].observed_price == 99.0
    assert 0 <= exits[0].latency_ms <= monitor_settings.exit_latency_budget_ms
    assert exits[0].feed_source == SOURCE_WEBSOCKET
    assert feed.closed


@respx.mock
def test_the_time_stop_exits_between_the_levels(monitor_settings, journal, monkeypatch):
    ids = seed_chain(journal, stop=10.0, target=1000.0, time_stop_ist="00:01")
    mock_book()
    monkeypatch.setattr(
        "strike_desk.position_monitor.PriceFeed", lambda *a, **k: FakeFeed(tick(110.0))
    )
    respx.post(f"{BASE}/api/v1/closeposition").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    monitor = build(monitor_settings, journal)
    monitor._adopt(ids["order_id"])
    monitor._evaluate()

    position_id = journal.list_positions(today())[0].position_id
    assert journal.exits_for_position(position_id)[0].reason in {"time-stop", "session-deadline"}


@respx.mock
def test_a_manual_close_stands_the_monitor_down(monitor_settings, journal, monkeypatch):
    ids = seed_chain(journal)
    mock_book()
    feed = FakeFeed(tick(110.0))
    monkeypatch.setattr("strike_desk.position_monitor.PriceFeed", lambda *a, **k: feed)
    monitor = build(monitor_settings, journal)
    monitor._adopt(ids["order_id"])

    respx.post(f"{BASE}/api/v1/positionbook").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": []})
    )
    monitor._last_reconcile = 0.0
    monitor._reconcile_if_due()

    position_id = journal.list_positions(today())[0].position_id
    rows = journal.position_states(position_id)
    stood_down = [row for row in rows if row.state == POSITION_STOOD_DOWN]
    assert stood_down and stood_down[0].exit_reason == EXIT_MANUAL
    assert journal.exits_for_position(position_id) == []
    assert feed.closed


@respx.mock
def test_a_failing_exit_escalates_and_kills(monitor_settings, journal, monkeypatch):
    ids = seed_chain(journal, stop=100.0, target=200.0)
    mock_book()
    monkeypatch.setattr(
        "strike_desk.position_monitor.PriceFeed", lambda *a, **k: FakeFeed(tick(90.0))
    )
    respx.post(f"{BASE}/api/v1/closeposition").mock(
        return_value=httpx.Response(500, json={"status": "error", "message": "boom"})
    )
    respx.post(f"{BASE}/api/v1/placeorder").mock(
        return_value=httpx.Response(500, json={"status": "error", "message": "boom"})
    )
    monitor = build(monitor_settings, journal)
    monitor._adopt(ids["order_id"])
    monitor._evaluate()

    position_id = journal.list_positions(today())[0].position_id
    assert POSITION_ORPHANED in states(journal, position_id)
    assert len(journal.exits_for_position(position_id)) == monitor_settings.exit_max_attempts
    assert monitor_settings.kill_switch_path.exists()


@respx.mock
def test_a_blackout_exits_at_market(monitor_settings, journal, monkeypatch):
    ids = seed_chain(journal, stop=10.0, target=1000.0)
    mock_book()
    stale = Tick(
        price=110.0,
        source=SOURCE_WEBSOCKET,
        received_at_utc=datetime.now(tz=UTC) - timedelta(seconds=600),
    )
    monkeypatch.setattr(
        "strike_desk.position_monitor.PriceFeed", lambda *a, **k: FakeFeed(stale)
    )
    respx.post(f"{BASE}/api/v1/quotes").mock(side_effect=httpx.ConnectError("down"))
    respx.post(f"{BASE}/api/v1/closeposition").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    monitor = build(monitor_settings, journal)
    monitor._adopt(ids["order_id"])
    monitor._blackout_since = datetime.now(tz=UTC) - timedelta(seconds=600)
    monitor._evaluate()

    position_id = journal.list_positions(today())[0].position_id
    assert journal.exits_for_position(position_id)[0].reason == "feed-blackout"


@respx.mock
def test_a_stale_feed_falls_back_to_quotes(monitor_settings, journal, monkeypatch):
    ids = seed_chain(journal, stop=100.0, target=200.0)
    mock_book()
    stale = Tick(
        price=110.0,
        source=SOURCE_WEBSOCKET,
        received_at_utc=datetime.now(tz=UTC) - timedelta(seconds=60),
    )
    monkeypatch.setattr(
        "strike_desk.position_monitor.PriceFeed", lambda *a, **k: FakeFeed(stale)
    )
    respx.post(f"{BASE}/api/v1/quotes").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": {"ltp": 95.0}})
    )
    respx.post(f"{BASE}/api/v1/closeposition").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    monitor = build(monitor_settings, journal)
    monitor._adopt(ids["order_id"])
    monitor._evaluate()

    position_id = journal.list_positions(today())[0].position_id
    exits = journal.exits_for_position(position_id)
    assert exits[0].reason == "stop" and exits[0].feed_source == "quotes"


@respx.mock
def test_adoption_is_idempotent(monitor_settings, journal, monkeypatch):
    ids = seed_chain(journal)
    mock_book()
    monkeypatch.setattr(
        "strike_desk.position_monitor.PriceFeed", lambda *a, **k: FakeFeed(tick(110.0))
    )
    monitor = build(monitor_settings, journal)
    monitor._adopt(ids["order_id"])
    monitor._position = None
    monitor._adopt(ids["order_id"])
    assert len(journal.list_positions(today())) == 2  # adopted + armed, once


@respx.mock
def test_an_unexplained_broker_position_is_not_adopted(monitor_settings, journal):
    mock_book()
    monitor = build(monitor_settings, journal)
    monitor._adopt_unadopted_orders()
    assert journal.live_position() is None
```

## 6. Regression and guardrail suites

### `strike_desk/tests/regression/position_scenarios.json`

The frozen set. Each case is a sequence of observations against fixed levels and the exit it
must produce — this is what a change to the precedence rules breaks.

```json
[
  {
    "name": "quiet-hold",
    "stop": 80.0,
    "target": 140.0,
    "minutes_to_time_stop": 60,
    "observations": [[110.0, 0], [112.0, 10], [108.5, 20], [111.0, 30]],
    "expect": null
  },
  {
    "name": "clean-stop",
    "stop": 80.0,
    "target": 140.0,
    "minutes_to_time_stop": 60,
    "observations": [[110.0, 0], [92.0, 10], [79.5, 20]],
    "expect": {"reason": "stop", "at_index": 2}
  },
  {
    "name": "stop-touched-exactly",
    "stop": 80.0,
    "target": 140.0,
    "minutes_to_time_stop": 60,
    "observations": [[110.0, 0], [80.0, 5]],
    "expect": {"reason": "stop", "at_index": 1}
  },
  {
    "name": "gap-through-the-stop",
    "stop": 80.0,
    "target": 140.0,
    "minutes_to_time_stop": 60,
    "observations": [[110.0, 0], [41.0, 5]],
    "expect": {"reason": "stop", "at_index": 1}
  },
  {
    "name": "clean-target",
    "stop": 80.0,
    "target": 140.0,
    "minutes_to_time_stop": 60,
    "observations": [[110.0, 0], [138.0, 10], [141.0, 20]],
    "expect": {"reason": "target", "at_index": 2}
  },
  {
    "name": "time-stop-while-profitable",
    "stop": 80.0,
    "target": 200.0,
    "minutes_to_time_stop": 1,
    "observations": [[150.0, 0], [155.0, 120]],
    "expect": {"reason": "time-stop", "at_index": 1}
  },
  {
    "name": "time-beats-a-simultaneous-stop",
    "stop": 80.0,
    "target": 200.0,
    "minutes_to_time_stop": 1,
    "observations": [[110.0, 0], [70.0, 120]],
    "expect": {"reason": "time-stop", "at_index": 1}
  },
  {
    "name": "no-price-then-a-stop",
    "stop": 80.0,
    "target": 140.0,
    "minutes_to_time_stop": 60,
    "observations": [[null, 0], [null, 10], [78.0, 20]],
    "expect": {"reason": "stop", "at_index": 2}
  }
]
```

### `strike_desk/tests/regression/test_position_scenarios.py`

```python
"""Replay the frozen scenarios through the real evaluator."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from strike_desk.levels import ExitLevels, evaluate

SCENARIOS = json.loads((Path(__file__).parent / "position_scenarios.json").read_text("utf-8"))
START = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)


@pytest.mark.parametrize("case", SCENARIOS, ids=[case["name"] for case in SCENARIOS])
def test_scenario(case):
    levels = ExitLevels(
        stop_price=case["stop"],
        target_price=case["target"],
        time_stop_utc=START + timedelta(minutes=case["minutes_to_time_stop"]),
        time_stop_reason="time-stop",
    )
    fired = None
    for index, (price, offset) in enumerate(case["observations"]):
        trigger = evaluate(levels, price, START + timedelta(seconds=offset))
        if trigger is not None:
            fired = (index, trigger)
            break

    expected = case["expect"]
    if expected is None:
        assert fired is None, f"{case['name']} should have held, fired {fired}"
        return
    assert fired is not None, f"{case['name']} should have exited and did not"
    index, trigger = fired
    assert index == expected["at_index"]
    assert trigger.reason == expected["reason"]
```

### `strike_desk/tests/test_guardrails_position.py`

The two structural properties. Neither is about behaviour under a given input; both are about
what the code *is*, which is why they belong in CI rather than in a review checklist.

```python
"""The monitor path holds no reasoning plane, and no entry outlives a gated exit path."""

from __future__ import annotations

import importlib
import sys

import pytest

REASONING_MODULES = (
    "strike_desk.regime_analyst",
    "strike_desk.options_strategist",
    "strike_desk.model_client",
    "strike_desk.mcp_toolbox",
    "strike_desk.prompt_registry",
    "anthropic",
)

MONITOR_PATH = (
    "strike_desk.levels",
    "strike_desk.price_feed",
    "strike_desk.exit_executor",
    "strike_desk.position_monitor",
)


def test_no_reasoning_module_is_imported_by_the_monitor_path():
    for name in (*REASONING_MODULES, *MONITOR_PATH):
        sys.modules.pop(name, None)
    for name in MONITOR_PATH:
        importlib.import_module(name)
    leaked = [name for name in REASONING_MODULES if name in sys.modules]
    assert leaked == [], f"the monitor path pulled in the reasoning plane: {leaked}"


def test_exit_paths_are_whitelisted():
    from strike_desk.execution_client import EXECUTION_PATHS
    from strike_desk.openalgo_client import READ_ONLY_PATHS

    assert "/api/v1/closeposition" in EXECUTION_PATHS
    assert "/api/v1/quotes" in READ_ONLY_PATHS
    assert "/api/v1/placeorder" not in READ_ONLY_PATHS


def test_the_tick_declines_when_the_exit_path_is_gated(monitor_settings, journal):
    from strike_desk.decline_taxonomy import TAXONOMY_VERSION, entry_for
    from strike_desk.exit_executor import exit_path_status

    class Gated:
        def analyze_mode(self) -> bool:
            return False

        def order_mode(self) -> str:
            return "semi_auto"

    status = exit_path_status(Gated())
    assert not status.ok
    entry = entry_for("exit-path-gated")
    assert TAXONOMY_VERSION == "dt-5"
    assert entry.outcome == "decline" and entry.disposition == "defect"


@pytest.mark.parametrize("quantity", [0, -1, -75])
def test_a_non_positive_quantity_never_reaches_the_wire(monitor_settings, quantity):
    from strike_desk.execution_client import ExecutionClient
    from strike_desk.exit_executor import ExitExecutor
    from strike_desk.openalgo_client import OpenAlgoClient

    executor = ExitExecutor(
        monitor_settings,
        ExecutionClient(monitor_settings),
        OpenAlgoClient(monitor_settings),
        "strike-desk-NIFTY",
    )
    assert executor.fire("X", "NFO", quantity).status == "failed"
```

## 7. Tests — unattended mode

Two files. `test_autonomy.py` is pure unit work against the guard module — no OpenAlgo, no
journal beyond the in-memory one, no clock that is not injected. `test_unattended_entry.py`
drives the graph end to end with the fake mirror and fake execution client the iteration-06
fixtures already provide, and its most important test is the one that asserts *attended mode
did not change*.

### `strike_desk/tests/test_autonomy.py`

```python
"""The guards that replace the human. Every one of them is arithmetic, so every one is a unit test."""

from datetime import UTC, date, datetime, timedelta

import pytest

from strike_desk import autonomy
from strike_desk.openalgo_mirror import ORDER_MODE_AUTO, ORDER_MODE_SEMI_AUTO


class _Mirror:
    def __init__(self, mode: str) -> None:
        self._mode = mode

    def order_mode(self) -> str:
        return self._mode


class _Monitor:
    def __init__(self, alive: bool, age_seconds: float) -> None:
        self._alive = alive
        self.heartbeat_utc = datetime.now(UTC) - timedelta(seconds=age_seconds)

    def is_alive(self) -> bool:
        return self._alive


@pytest.mark.parametrize(
    ("mode", "reported", "ok"),
    [
        ("unattended", ORDER_MODE_AUTO, True),
        ("unattended", ORDER_MODE_SEMI_AUTO, False),
        ("attended", ORDER_MODE_SEMI_AUTO, True),
        ("attended", ORDER_MODE_AUTO, False),
    ],
)
def test_mode_must_agree_with_openalgo(monitor_settings, mode, reported, ok):
    settings = monitor_settings.model_copy(update={"autonomy": mode})
    verdict = autonomy.check_mode(settings, _Mirror(reported))
    assert verdict.ok is ok
    if not ok:
        assert verdict.reason == "autonomy-mode-mismatch"
        assert reported in verdict.detail


def test_an_unreadable_mirror_blocks_rather_than_guesses(monitor_settings):
    class _Broken:
        def order_mode(self):
            raise MirrorUnavailable("database is locked")

    verdict = autonomy.check_mode(monitor_settings, _Broken())
    assert verdict.ok is False
    assert verdict.reason == "autonomy-mode-mismatch"


@pytest.mark.parametrize(
    ("monitor", "ok"),
    [(None, False), (_Monitor(False, 0), False), (_Monitor(True, 900), False), (_Monitor(True, 1), True)],
)
def test_the_dead_man_switch(monitor_settings, monitor, ok):
    settings = monitor_settings.model_copy(update={"autonomy": "unattended"})
    verdict = autonomy.check_monitor(settings, monitor, datetime.now(UTC))
    assert verdict.ok is ok


def test_attended_mode_needs_no_monitor(monitor_settings):
    """Attended, a person is the fallback, so a dead monitor does not block an entry."""
    settings = monitor_settings.model_copy(update={"autonomy": "attended"})
    assert autonomy.check_monitor(settings, None, datetime.now(UTC)).ok is True


def test_the_loss_cap_reads_the_journal_not_memory(monitor_settings, journal, seeded_flat_loss):
    settings = monitor_settings.model_copy(
        update={"autonomy": "unattended", "unattended_daily_loss_cap": 500.0}
    )
    verdict = autonomy.check_day(settings, journal, date(2026, 9, 8))
    assert verdict.ok is False
    assert verdict.reason == "daily-loss-cap"


def test_the_loss_cap_is_checked_before_the_trade_count(monitor_settings, journal, seeded_flat_loss):
    """A desk out of budget says so; it never reports that it has room for one more."""
    settings = monitor_settings.model_copy(
        update={
            "autonomy": "unattended",
            "unattended_daily_loss_cap": 500.0,
            "unattended_max_trades_per_day": 1,
        }
    )
    assert autonomy.check_day(settings, journal, date(2026, 9, 8)).reason == "daily-loss-cap"


def test_the_trade_count_caps_the_day(monitor_settings, journal, seeded_two_entries):
    settings = monitor_settings.model_copy(
        update={"autonomy": "unattended", "unattended_max_trades_per_day": 2}
    )
    verdict = autonomy.check_day(settings, journal, date(2026, 9, 8))
    assert verdict.ok is False and verdict.reason == "daily-trade-cap"


def test_every_guard_is_inert_when_attended(monitor_settings, journal, seeded_flat_loss):
    """The whole of this module must be a no-op in the opt-out mode."""
    settings = monitor_settings.model_copy(update={"autonomy": "attended"})
    verdict = autonomy.evaluate(
        settings, _Mirror(ORDER_MODE_SEMI_AUTO), journal, None, date(2026, 9, 8)
    )
    assert verdict.ok is True


def test_unattended_is_the_default(tmp_path, monkeypatch):
    """The iteration's headline: an unconfigured desk runs with no human in the loop."""
    monkeypatch.delenv("STRIKE_DESK_AUTONOMY", raising=False)
    settings = Settings(
        openalgo_api_key="test-key-0123456789",
        openalgo_user="tester",
        openalgo_db_path=tmp_path / "openalgo.db",
        state_dir=tmp_path / "state",
    )
    assert settings.autonomy == "unattended"
    assert settings.unattended is True


def test_an_upgraded_semi_auto_desk_declines_rather_than_enters(monitor_settings, journal):
    """AC-22: default-unattended against an un-migrated key must stop, not guess."""
    verdict = autonomy.evaluate(
        monitor_settings, _Mirror(ORDER_MODE_SEMI_AUTO), journal, None, date(2026, 9, 8)
    )
    assert verdict.ok is False
    assert verdict.reason == "autonomy-mode-mismatch"
```

Note what the `monitor_settings` fixture now means: it sets no `autonomy`, so it *is* an
unattended desk, and the two attended tests above opt out explicitly. That inversion is
deliberate — the fixture every other test in this suite shares should be the configuration
every deploy will actually run.

The two new fixtures — `seeded_flat_loss` writes one `positions` row at `flat` with a negative
`realised_pnl` on 2026-09-08, and `seeded_two_entries` writes two BUY `orders` rows on the same
day — join `position_fixtures.py` beside the ones §1 already defines. Both write through the
journal's own append methods rather than raw SQL, so a schema drift breaks them loudly.

### `strike_desk/tests/test_unattended_entry.py`

```python
"""The graph, end to end, with the human removed — and the proof that attended did not move."""


def test_an_unattended_tick_places_without_suspending(unattended_deps, fake_execution):
    """No interrupt, no pending order, one broker order id, in a single pass."""
    result = run_tick(unattended_deps)
    assert result.outcome == "enter"
    assert result.interrupted is False
    assert fake_execution.placed[0]["response"]["orderid"] == "SANDBOX-1"
    assert "pending_order_id" not in fake_execution.placed[0]["response"]


def test_the_next_tick_is_not_held_by_an_approval(unattended_deps):
    run_tick(unattended_deps)
    second = run_tick(unattended_deps)
    assert second.reason != "approval-pending"


def test_an_unattended_entry_writes_an_auto_approved_row(unattended_deps, journal):
    run_tick(unattended_deps)
    row = journal.latest_approval()
    assert row.status == "auto-approved"
    assert row.approver == "strike-desk"
    assert row.deadline_utc is None
    assert row.pending_order_id is None


def test_an_unattended_entry_is_logged_at_warning(unattended_deps, caplog):
    with caplog.at_level("WARNING"):
        run_tick(unattended_deps)
    line = next(r for r in caplog.records if "unattended entry" in r.message)
    assert "stop" in line.getMessage() and "time-stop" in line.getMessage()


def test_a_queued_response_kills_the_desk(unattended_deps, fake_execution, kill_switch):
    """Unattended plus a pending_order_id means a human is silently holding our entry."""
    fake_execution.next_response = {"status": "success", "mode": "semi_auto", "pending_order_id": 7}
    result = run_tick(unattended_deps)
    assert result.disposition == "defect"
    assert kill_switch.engaged is True


def test_a_dead_monitor_declines_before_a_token_is_spent(unattended_deps, fake_model):
    unattended_deps.monitor = None
    result = run_tick(unattended_deps)
    assert result.outcome == "decline"
    assert result.reason == "monitor-unavailable"
    assert fake_model.call_count == 0


def test_the_loss_cap_declines_and_kills(unattended_deps, journal, kill_switch, seeded_flat_loss):
    result = run_tick(unattended_deps)
    assert result.reason == "daily-loss-cap"
    assert kill_switch.engaged is True


def test_attended_mode_is_byte_for_byte_unchanged(attended_deps, fake_execution, snapshot):
    """The regression that matters most in this section: iteration 06 still behaves as it did."""
    result = run_tick(attended_deps)
    assert result.interrupted is True
    assert fake_execution.placed[0]["response"]["pending_order_id"] == 41
    assert snapshot(journal_rows(attended_deps)) == snapshot.stored
```

`attended_deps` sets `autonomy="attended"` explicitly; `unattended_deps` leaves it at the
default. They are otherwise the same `TickDeps`, differing only in that setting and
in the order mode the fake mirror reports — which is the point. If a test passes under one and
fails under the other for any reason other than the four behaviours named above, the switch has
leaked into code it should not have touched.

### `test_guardrails_position.py` — two additions

```python
def test_autonomy_imports_no_reasoning_module():
    """The guards that replace the human must be as unreachable from an agent as the exits are."""
    assert_no_reasoning_imports("strike_desk.autonomy")


def test_unattended_mode_cannot_be_enabled_without_a_mirror(monitor_settings):
    """A desk that cannot read OpenAlgo's order mode may not claim to be unattended."""
    settings = monitor_settings.model_copy(update={"autonomy": "unattended"})
    with pytest.raises(ConfigError, match="mirror"):
        build_service(settings.model_copy(update={"openalgo_db_path": None}))
```

## 8. Running it

Locally, from `strike_desk/`:

```bash
uv run ruff check .
uv run pytest -q                                  # everything
uv run pytest tests/test_levels.py -q             # the evaluator alone
uv run pytest tests/regression -q                 # the frozen scenarios
uv run pytest --cov=strike_desk --cov-report=term-missing
```

In CI, the existing GitHub Actions workflow gains no new job — it gains three lines in the one
that already runs the suite, because the regression and guardrail files are ordinary pytest
files and are collected automatically. What does change is the failure policy: the guardrail
file and the regression file are **not** allowed to be skipped or marked `xfail`. Add them to
the workflow as an explicit second invocation so a collection error cannot pass silently as
zero tests:

```yaml
      - name: Guardrails and regression must run
        working-directory: strike_desk
        run: |
          uv run pytest tests/test_guardrails_position.py tests/test_unattended_entry.py tests/regression -q \
            --strict-markers -p no:randomly
```

`tests/test_unattended_entry.py` joins the same must-run list as the guardrails, for the same
reason: it is the only place attended mode is proven unchanged, and a suite that silently
collects zero of those tests would let the switch leak into the default path unnoticed.

The tests need no network, no OpenAlgo, no broker session and no API key beyond the dummy in
`monitor_settings`, so the whole suite runs on a laptop and in CI identically and in under a
minute.

## 9. Traceability

| Test | Kind | Covers | Backstops manual case |
| --- | --- | --- | --- |
| `test_levels.py::test_impossible_levels_are_refused`, `::test_resolve_time_stop_without_a_proposal_time` | unit | AC-1 | MT-06, MT-08 |
| `test_position_monitor.py::test_adoption_stamps_levels_from_the_proposal` | integration | AC-1, AC-2 | MT-11, MT-12 |
| `test_journal_positions.py::test_unadopted_orders_finds_a_filled_order` | unit | AC-2 | MT-11 |
| `test_position_monitor.py::test_an_unexplained_broker_position_is_not_adopted` | integration | AC-2 | MT-22 |
| `test_levels.py::test_stop_breach_at_or_below` | unit | AC-3 | MT-01 |
| `test_position_monitor.py::test_a_stop_breach_exits_and_records_latency` | integration | AC-3, AC-7, AC-10 | MT-13, MT-15, MT-16 |
| `test_levels.py::test_target_breach_at_or_above` | unit | AC-4 | MT-02 |
| `test_levels.py::test_time_stop_fires_regardless_of_price`, `::test_resolve_time_stop_takes_the_earlier_clock` | unit | AC-5 | MT-04, MT-07 |
| `test_position_monitor.py::test_the_time_stop_exits_between_the_levels` | integration | AC-5 | MT-14 |
| `test_levels.py::test_time_beats_price`, `::test_stop_beats_target_when_both_breached` | unit | AC-6 | MT-04, MT-05 |
| `regression/test_position_scenarios.py` (8 cases) | regression | AC-3, AC-4, AC-5, AC-6, AC-10 | MT-01…MT-05, MT-13 |
| `test_guardrails_position.py::test_no_reasoning_module_is_imported_by_the_monitor_path` | guardrail | AC-8 | MT-09 |
| `test_exit_executor.py::test_preflight_blocks_live_semi_auto`, `::test_preflight_allows_analyze_mode` | integration | AC-9 | MT-23, MT-24 |
| `test_guardrails_position.py::test_the_tick_declines_when_the_exit_path_is_gated` | guardrail | AC-9 | MT-23, MT-26 |
| `test_exit_executor.py::test_a_queued_exit_is_gated_not_submitted` | integration | AC-9, AC-11 | MT-20 |
| `test_position_monitor.py::test_a_failing_exit_escalates_and_kills` | integration | AC-11 | MT-20 |
| `test_position_monitor.py::test_a_stale_feed_falls_back_to_quotes`, `::test_a_blackout_exits_at_market` | integration | AC-12 | MT-17, MT-18 |
| `test_price_feed.py::test_tick_age_is_never_negative`, `::test_rejects_zero_and_garbage_prices` | unit | AC-12 | MT-10 |
| `test_position_monitor.py::test_a_manual_close_stands_the_monitor_down` | integration | AC-13 | MT-19 |
| `test_journal_positions.py::test_a_state_may_be_written_once`, `::test_rows_cannot_be_updated_or_deleted` | unit | AC-14 | MT-21 |
| `test_position_monitor.py::test_adoption_is_idempotent` | integration | AC-14 | MT-21 |
| `test_exit_executor.py::test_exclusive_book_uses_closeposition`, `::test_a_foreign_position_forces_a_targeted_sell` | integration | AC-11 | MT-13 |
| `test_unattended_entry.py::test_an_unattended_tick_places_without_suspending`, `::test_the_next_tick_is_not_held_by_an_approval` | integration | AC-15 | MT-31 |
| `test_autonomy.py::test_mode_must_agree_with_openalgo`, `::test_an_unreadable_mirror_blocks_rather_than_guesses` | unit | AC-16 | MT-28, MT-29 |
| `test_unattended_entry.py::test_a_queued_response_kills_the_desk` | integration | AC-17 | MT-35 |
| `test_autonomy.py::test_the_dead_man_switch`, `::test_attended_mode_needs_no_monitor` | unit | AC-18 | MT-30 |
| `test_unattended_entry.py::test_a_dead_monitor_declines_before_a_token_is_spent` | integration | AC-18 | MT-30, MT-34 |
| `test_autonomy.py::test_the_loss_cap_reads_the_journal_not_memory`, `::test_the_loss_cap_is_checked_before_the_trade_count` | unit | AC-19 | MT-36, MT-37 |
| `test_unattended_entry.py::test_the_loss_cap_declines_and_kills` | integration | AC-19 | MT-36 |
| `test_autonomy.py::test_the_trade_count_caps_the_day` | unit | AC-20 | MT-38 |
| `test_unattended_entry.py::test_an_unattended_entry_writes_an_auto_approved_row`, `::test_an_unattended_entry_is_logged_at_warning` | integration | AC-21 | MT-32, MT-33 |
| `test_unattended_entry.py::test_attended_mode_is_byte_for_byte_unchanged`, `test_autonomy.py::test_every_guard_is_inert_when_attended` | regression | AC-21 | MT-27, MT-39 |
| `test_guardrails_position.py::test_autonomy_imports_no_reasoning_module` | guardrail | AC-8, AC-15 | MT-09 |
