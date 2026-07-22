# Iteration 01 — Test Automation

The manual pass in `03_manual_test_cases.md` proved the desk works once. This suite proves it keeps working — every time you touch the decision table, bump a pin, or register the first real specialist. It runs in under ten seconds on a laptop and gates every push in CI.

## 1. Framework and what is real

**pytest 9.1.1** is the runner, matching the host project's layout so `uv run pytest` means the same thing in both trees. Three plugins do the heavy lifting: **respx 0.23.1** intercepts httpx at the transport layer, so every OpenAlgo call is exercised through the real `OpenAlgoClient` — real retry logic, real status handling, real JSON parsing — against scripted responses; **freezegun 1.5.5** pins the clock for the session-gate boundary tests, which are entirely about what happens at 09:29:59 versus 09:30:00; and **pytest-cov 7.1.0** enforces the coverage floor.

What is mocked is exactly one thing: OpenAlgo's HTTP responses. Everything else is the real component. The journal is a real SQLite database in `tmp_path` with real triggers, so the append-only tests assert against SQLite itself rather than against a mock that agrees with them. The tracer provider is a real OpenTelemetry provider with the real `JournalSpanProcessor`, so trace assertions read rows the production code path wrote. The graph is the real compiled LangGraph with a real `SqliteSaver` checkpointer. Specialists are stubs, because that is what a specialist *is* at this point in the build — an object implementing the protocol.

No model is mocked, because none is called. That shapes the eval layer: there is no output-quality score to compute, no hallucination to catch and no prompt to grade in this slice. What replaces it is a **frozen regression suite over the decision table** (§6): a fixed set of tick states with golden outcomes, so any edit that changes what the desk decides in a known situation fails CI loudly rather than quietly shipping. Alongside it sit the **guardrail tests** (§5), which assert the properties that must hold no matter what anyone later adds — no order path, no `enter` without a strategist, no secret in the journal. Both are run as their own CI step before the general suite, because a passing build with a broken risk check is worse than a red one.

## 2. Shared fixtures

### `strike_desk/tests/conftest.py`

Builds a complete, isolated desk per test: fresh settings in `tmp_path`, a real journal, a real tracer provider, a scripted OpenAlgo, and a wired `TickRunner`.

Two details are worth flagging. The autouse `_clean_env` fixture strips every `STRIKE_DESK_*` variable, so a developer's exported production key can never leak into a test run. And `_reset_global_tracer_provider` pokes OpenTelemetry's set-once guard: the API deliberately allows only one global provider per process, which is right in production and impossible in a test suite that wants a fresh journal per test, so the fixture resets it on both sides of each test.

```python
"""Shared fixtures: an isolated Strike Desk per test."""

from __future__ import annotations

import json
from datetime import date, datetime, time
from typing import Any

import httpx
import pytest
import respx
from opentelemetry import trace as otel_trace

from strike_desk.config import IST, Settings
from strike_desk.graph import TickDeps
from strike_desk.journal import Journal
from strike_desk.observability import Redactor, configure_tracing
from strike_desk.openalgo_client import OpenAlgoClient
from strike_desk.prompt_registry import PromptRegistry
from strike_desk.runner import TickRunner
from strike_desk.session import SessionGate
from strike_desk.specialists import SpecialistRegistry, shutdown_executor

BASE_URL = "http://openalgo.test"
API_KEY = "test-api-key-0123456789abcdef"


def epoch_ms(day: date, at: time) -> int:
    return int(datetime.combine(day, at, tzinfo=IST).timestamp() * 1000)


def timings_for(day: date, exchange: str = "NFO", start: time = time(0, 0), end: time = time(23, 59)):
    return [
        {
            "exchange": exchange,
            "start_time": epoch_ms(day, start),
            "end_time": epoch_ms(day, end),
        }
    ]


def position(symbol: str = "NIFTY28JUL2624500CE", quantity: int = 75) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "exchange": "NFO",
        "product": "NRML",
        "quantity": str(quantity),
        "average_price": "142.50",
        "ltp": "151.05",
        "pnl": 641.25,
    }


FUNDS = {
    "availablecash": "482310.55",
    "collateral": "0.00",
    "m2munrealized": "0.00",
    "m2mrealized": "0.00",
    "utiliseddebits": "17689.45",
}


def _reset_global_tracer_provider() -> None:
    """OpenTelemetry allows one global provider per process; tests need one per test."""
    otel_trace._TRACER_PROVIDER = None  # noqa: SLF001
    otel_trace._TRACER_PROVIDER_SET_ONCE._done = False  # noqa: SLF001


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's real configuration must never reach a test."""
    import os

    for name in [key for key in os.environ if key.startswith("STRIKE_DESK_")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings(tmp_path) -> Settings:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        openalgo_base_url=BASE_URL,
        openalgo_api_key=API_KEY,
        openalgo_retries=0,
        openalgo_timeout_seconds=2.0,
        state_dir=state_dir,
        prompts_dir=tmp_path / "prompts",
        tick_interval_seconds=60,
        tick_budget_seconds=20.0,
        specialist_timeout_seconds=0.5,
        # Neutralised so integration tests exercise the graph, not the calendar.
        no_trade_windows="00:00-00:01",
        expiry_cutoff="23:59",
    )


@pytest.fixture
def journal(settings: Settings) -> Journal:
    journal = Journal(settings.db_path)
    journal.create_schema()
    yield journal
    journal.close()


@pytest.fixture
def tracing(settings: Settings, journal: Journal):
    _reset_global_tracer_provider()
    provider, processor = configure_tracing(settings, journal, Redactor([API_KEY]))
    yield processor
    provider.shutdown()
    _reset_global_tracer_provider()


@pytest.fixture
def openalgo():
    """A scripted OpenAlgo: open all day, flat book, healthy funds."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.post("/api/v1/ping").mock(
            return_value=httpx.Response(200, json={"status": "success", "data": {"broker": "test"}})
        )
        router.post("/api/v1/funds").mock(
            return_value=httpx.Response(200, json={"status": "success", "data": FUNDS})
        )
        router.post("/api/v1/positionbook").mock(
            return_value=httpx.Response(200, json={"status": "success", "data": []})
        )

        def timings(request: httpx.Request) -> httpx.Response:
            day = date.fromisoformat(json.loads(request.content)["date"])
            return httpx.Response(200, json={"status": "success", "data": timings_for(day)})

        router.post("/api/v1/market/timings").mock(side_effect=timings)
        yield router


@pytest.fixture
def client(settings: Settings, openalgo) -> OpenAlgoClient:
    client = OpenAlgoClient(settings)
    yield client
    client.close()


@pytest.fixture
def registry() -> SpecialistRegistry:
    registry = SpecialistRegistry()
    yield registry
    shutdown_executor()


@pytest.fixture
def deps(settings, client, journal, registry, tracing) -> TickDeps:
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    connection = sqlite3.connect(str(settings.checkpoint_path), check_same_thread=False)
    checkpointer = SqliteSaver(connection)
    checkpointer.setup()
    yield TickDeps(
        settings=settings,
        client=client,
        journal=journal,
        registry=registry,
        prompts=PromptRegistry.load(settings.prompts_dir),
        span_processor=tracing,
        checkpointer=checkpointer,
    )
    connection.close()


@pytest.fixture
def runner(deps: TickDeps) -> TickRunner:
    return TickRunner(deps, SessionGate(deps.client, deps.settings))


@pytest.fixture
def today() -> str:
    return datetime.now(tz=IST).date().isoformat()


class StubSpecialist:
    """A specialist that answers with whatever the test tells it to."""

    def __init__(self, role: str = "regime", payload: dict[str, Any] | None = None, **kwargs):
        self.role = role
        self._payload = payload or {}
        self._kwargs = kwargs

    def run(self, request):
        from strike_desk.specialists import SpecialistResult

        return SpecialistResult(role=self.role, payload=self._payload, **self._kwargs)
```

## 3. Unit tests

### `strike_desk/tests/test_session.py`

The gate is pure boundary logic, so it gets boundary tests: one second either side of every window, holidays, and the expiry walk-back.

```python
"""Session gate: hours, windows, expiry, kill switch."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from freezegun import freeze_time

from strike_desk.config import IST
from strike_desk.errors import OpenAlgoError
from strike_desk.session import (
    CALENDAR_UNAVAILABLE,
    EXPIRY_CUTOFF,
    KILL_SWITCH,
    MARKET_CLOSED,
    NO_TRADE_WINDOW,
    SessionGate,
)
from tests.conftest import epoch_ms


class FakeClient:
    """Returns NFO timings for open days and nothing for closed ones."""

    def __init__(self, open_days: set[date], fail: bool = False) -> None:
        self.open_days = open_days
        self.fail = fail
        self.calls: list[date] = []

    def market_timings(self, day: date):
        self.calls.append(day)
        if self.fail:
            raise OpenAlgoError("/api/v1/market/timings: transport failure")
        if day not in self.open_days:
            return []
        return [
            {
                "exchange": "NFO",
                "start_time": epoch_ms(day, time(9, 15)),
                "end_time": epoch_ms(day, time(15, 30)),
            }
        ]


WEDNESDAY = date(2026, 7, 22)
TUESDAY = date(2026, 7, 21)
MONDAY = date(2026, 7, 20)


def gate_for(settings, open_days, fail=False):
    settings.no_trade_windows = "09:15-09:30,15:15-15:30"
    settings.expiry_cutoff = "14:00"
    return SessionGate(FakeClient(open_days, fail), settings), settings


@pytest.mark.parametrize(
    ("clock", "blocked_by"),
    [
        ("2026-07-22 09:14:59+05:30", MARKET_CLOSED),
        ("2026-07-22 09:15:00+05:30", NO_TRADE_WINDOW),
        ("2026-07-22 09:29:59+05:30", NO_TRADE_WINDOW),
        ("2026-07-22 09:30:00+05:30", None),
        ("2026-07-22 15:14:59+05:30", None),
        ("2026-07-22 15:15:00+05:30", NO_TRADE_WINDOW),
        ("2026-07-22 15:30:01+05:30", MARKET_CLOSED),
    ],
)
def test_window_boundaries(settings, clock, blocked_by):
    gate, _ = gate_for(settings, {WEDNESDAY})
    with freeze_time(clock):
        verdict = gate.evaluate(datetime.now(tz=IST))
    assert verdict.allowed is (blocked_by is None)
    assert verdict.blocked_by == blocked_by


def test_holiday_is_market_closed(settings):
    gate, _ = gate_for(settings, set())
    with freeze_time("2026-07-22 11:00:00+05:30"):
        verdict = gate.evaluate(datetime.now(tz=IST))
    assert verdict.blocked_by == MARKET_CLOSED


def test_kill_switch_wins_over_every_other_gate(settings):
    gate, settings = gate_for(settings, {WEDNESDAY})
    settings.kill_switch_path.write_text("2026-07-22T05:30:00+00:00 manual test")
    with freeze_time("2026-07-22 11:00:00+05:30"):
        verdict = gate.evaluate(datetime.now(tz=IST))
    assert verdict.blocked_by == KILL_SWITCH
    assert "manual test" in verdict.detail


def test_expiry_cutoff_blocks_after_the_configured_time(settings):
    gate, settings = gate_for(settings, {TUESDAY})
    settings.expiry_weekday = TUESDAY.weekday()
    with freeze_time("2026-07-21 13:59:00+05:30"):
        assert gate.evaluate(datetime.now(tz=IST)).allowed is True
    with freeze_time("2026-07-21 14:00:00+05:30"):
        assert gate.evaluate(datetime.now(tz=IST)).blocked_by == EXPIRY_CUTOFF


def test_expiry_walks_back_over_a_holiday(settings):
    """When the expiry weekday is a holiday, expiry is the previous trading day."""
    gate, settings = gate_for(settings, {MONDAY})  # Tuesday closed
    settings.expiry_weekday = TUESDAY.weekday()
    assert gate.expiry_date_for(MONDAY) == MONDAY


def test_calendar_failure_blocks_rather_than_trades(settings):
    gate, _ = gate_for(settings, {WEDNESDAY}, fail=True)
    with freeze_time("2026-07-22 11:00:00+05:30"):
        verdict = gate.evaluate(datetime.now(tz=IST))
    assert verdict.allowed is False
    assert verdict.blocked_by == CALENDAR_UNAVAILABLE


def test_timings_are_cached_per_day(settings):
    gate, _ = gate_for(settings, {WEDNESDAY})
    client = gate._client  # noqa: SLF001
    with freeze_time("2026-07-22 11:00:00+05:30"):
        gate.evaluate(datetime.now(tz=IST))
        first = len(client.calls)
        gate.evaluate(datetime.now(tz=IST))
    assert len(client.calls) == first
```

### `strike_desk/tests/test_journal.py`

The journal is the audit posture made real, so its tests assert against SQLite's own refusals.

```python
"""Journal: append-only enforcement, wrapping, and queries."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from strike_desk.errors import JournalWriteError
from strike_desk.journal import SCHEMA_VERSION


def make_row(tick_id: str = "tick-1", **overrides):
    row = {
        "tick_id": tick_id,
        "trace_id": "0" * 32,
        "created_at_utc": datetime.now(tz=UTC),
        "trading_day": "2026-07-22",
        "index_symbol": "NIFTY",
        "trigger": "schedule",
        "outcome": "decline",
        "reason_code": "specialist-unavailable",
        "reason_text": "Declined: no usable 'regime' specialist.",
        "regime_label": None,
        "regime_confidence": None,
        "book_state_json": "{}",
        "prompt_set_version": "ps-000000000000",
        "model_version": "none",
        "token_cost_micros": 0,
        "latency_ms": 12,
        "trace_complete": True,
        "schema_version": SCHEMA_VERSION,
    }
    row.update(overrides)
    return row


def test_decision_round_trips(journal):
    journal.record_decision(**make_row())
    rows = journal.list_decisions("2026-07-22")
    assert len(rows) == 1
    assert rows[0].reason_code == "specialist-unavailable"
    assert journal.count_decisions("2026-07-22") == 1
    assert journal.count_decisions("2026-07-23") == 0


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE decisions SET outcome='enter'",
        "DELETE FROM decisions",
        "UPDATE traces SET name='tampered'",
        "DELETE FROM traces",
    ],
)
def test_tables_are_append_only(journal, statement):
    journal.record_decision(**make_row())
    journal.record_span(
        trace_id="0" * 32,
        span_id="1" * 16,
        parent_span_id=None,
        name="strike_desk.tick",
        started_at_utc=datetime.now(tz=UTC),
        ended_at_utc=datetime.now(tz=UTC),
        duration_ms=5,
        status="UNSET",
        attributes_json="{}",
    )
    with pytest.raises(Exception, match="append-only"):
        with journal.session_scope() as session:
            session.execute(text(statement))


def test_duplicate_tick_id_raises_journal_write_error(journal):
    journal.record_decision(**make_row("tick-dup"))
    with pytest.raises(JournalWriteError):
        journal.record_decision(**make_row("tick-dup"))


def test_spans_for_trace_are_ordered(journal):
    base = datetime.now(tz=UTC)
    for index, name in enumerate(["strike_desk.tick", "tick.plan", "tick.decide"]):
        journal.record_span(
            trace_id="a" * 32,
            span_id=f"{index:016d}",
            parent_span_id=None if index == 0 else "0" * 16,
            name=name,
            started_at_utc=base.replace(microsecond=index * 1000),
            ended_at_utc=base.replace(microsecond=(index + 1) * 1000),
            duration_ms=1,
            status="UNSET",
            attributes_json="{}",
        )
    assert [span.name for span in journal.spans_for_trace("a" * 32)] == [
        "strike_desk.tick",
        "tick.plan",
        "tick.decide",
    ]
```

### `strike_desk/tests/test_book_state.py`

The book reader is where OpenAlgo's string-formatted numbers become the facts a decision rests on, so parsing failures must be loud.

```python
"""Book state: filtering, parsing, and refusing to guess."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from strike_desk.book_state import read_book_state
from strike_desk.errors import BookStateUnavailable
from tests.conftest import FUNDS, position


def read(client, journal, settings):
    return read_book_state(client, journal, settings, "2026-07-22", datetime.now(tz=UTC))


def test_flat_book(client, journal, settings, openalgo):
    book = read(client, journal, settings)
    assert book.flat is True
    assert book.open_positions == ()
    assert book.available_cash == pytest.approx(482310.55)
    assert book.utilised_margin == pytest.approx(17689.45)


def test_open_index_position_is_detected(client, journal, settings, openalgo):
    openalgo.post("/api/v1/positionbook").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": [position()]})
    )
    book = read(client, journal, settings)
    assert book.flat is False
    assert book.open_positions[0].quantity == 75


@pytest.mark.parametrize(
    "row",
    [
        position(quantity=0),                                    # squared off
        {**position(), "exchange": "NSE"},                       # wrong exchange
        {**position(symbol="BANKNIFTY28JUL2653000CE")},          # different index
    ],
)
def test_irrelevant_rows_are_filtered_out(client, journal, settings, openalgo, row):
    openalgo.post("/api/v1/positionbook").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": [row]})
    )
    assert read(client, journal, settings).flat is True


def test_unparseable_funds_raise_rather_than_default_to_zero(client, journal, settings, openalgo):
    openalgo.post("/api/v1/funds").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {**FUNDS, "availablecash": "n/a"}}
        )
    )
    with pytest.raises(BookStateUnavailable, match="availablecash"):
        read(client, journal, settings)


def test_openalgo_error_becomes_book_state_unavailable(client, journal, settings, openalgo):
    openalgo.post("/api/v1/positionbook").mock(return_value=httpx.Response(503))
    with pytest.raises(BookStateUnavailable):
        read(client, journal, settings)


def test_decisions_today_comes_from_the_journal(client, journal, settings, openalgo):
    from tests.test_journal import make_row

    journal.record_decision(**make_row("tick-a"))
    journal.record_decision(**make_row("tick-b"))
    assert read(client, journal, settings).decisions_today == 2
```

### `strike_desk/tests/test_specialists.py`

The delegation port must fail in exactly three ways: unregistered, slow, and broken.

```python
"""Specialist registry: registration, timeout, and malformed answers."""

from __future__ import annotations

import time

import pytest

from strike_desk.errors import SpecialistTimeout, SpecialistUnavailable
from strike_desk.specialists import SpecialistRequest, SpecialistResult
from tests.conftest import StubSpecialist


def a_request() -> SpecialistRequest:
    from datetime import UTC, datetime

    return SpecialistRequest(
        tick_id="tick-1", index_symbol="NIFTY", as_of=datetime.now(tz=UTC), book={}
    )


def test_unregistered_role_is_unavailable(registry):
    with pytest.raises(SpecialistUnavailable) as info:
        registry.consult("regime", a_request(), 1.0)
    assert info.value.role == "regime"


def test_registered_specialist_answers(registry):
    registry.register(StubSpecialist(payload={"label": "trending", "confidence": 0.8}))
    result = registry.consult("regime", a_request(), 1.0)
    assert result.payload["label"] == "trending"
    assert registry.registered_roles() == ("regime",)


def test_slow_specialist_times_out(registry):
    class Slow:
        role = "regime"

        def run(self, request):
            time.sleep(2.0)
            return SpecialistResult(role="regime")

    registry.register(Slow())
    with pytest.raises(SpecialistTimeout):
        registry.consult("regime", a_request(), 0.2)


def test_raising_specialist_is_unavailable(registry):
    class Broken:
        role = "regime"

        def run(self, request):
            raise RuntimeError("boom")

    registry.register(Broken())
    with pytest.raises(SpecialistUnavailable, match="RuntimeError"):
        registry.consult("regime", a_request(), 1.0)


def test_wrong_role_in_result_is_unavailable(registry):
    registry.register(StubSpecialist(role="regime"))
    registry._specialists["regime"] = StubSpecialist(role="something-else")  # noqa: SLF001
    with pytest.raises(SpecialistUnavailable, match="malformed"):
        registry.consult("regime", a_request(), 1.0)


def test_a_specialist_without_a_role_cannot_register(registry):
    class Nameless:
        role = ""

        def run(self, request):
            return SpecialistResult(role="")

    with pytest.raises(ValueError, match="non-empty role"):
        registry.register(Nameless())
```

## 4. Integration tests: the tick end to end

### `strike_desk/tests/test_tick.py`

Every case here drives the real runner, the real graph and the real journal, and asserts on the rows and spans that came out the other side.

```python
"""The decision tick, end to end."""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from strike_desk.errors import JournalWriteError
from tests.conftest import StubSpecialist, position


def only_decision(journal, today):
    rows = journal.list_decisions(today)
    assert len(rows) == 1, f"expected exactly one decision, got {len(rows)}"
    return rows[0]


def span_names(journal, trace_id):
    return {span.name for span in journal.spans_for_trace(trace_id)}


def test_flat_book_with_no_specialists_declines(runner, journal, today):
    tick_id = runner.run_tick("schedule")
    assert tick_id is not None
    row = only_decision(journal, today)
    assert (row.outcome, row.reason_code) == ("decline", "specialist-unavailable")
    assert "regime" in row.reason_text
    assert row.trigger == "schedule"
    assert row.model_version == "none"
    assert row.token_cost_micros == 0
    assert row.prompt_set_version.startswith("ps-")
    assert row.trace_complete is True
    assert span_names(journal, row.trace_id) == {
        "strike_desk.tick",
        "tick.plan",
        "tick.consult",
        "tick.decide",
        "tick.persist",
    }


def test_open_position_holds_without_consulting(runner, journal, openalgo, today):
    openalgo.post("/api/v1/positionbook").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": [position()]})
    )
    runner.run_tick("manual")
    row = only_decision(journal, today)
    assert (row.outcome, row.reason_code) == ("hold", "position-open")
    assert "NIFTY28JUL2624500CE" in row.reason_text
    assert "tick.consult" not in span_names(journal, row.trace_id)


def test_unreadable_book_declines_on_data_quality(runner, journal, openalgo, today):
    openalgo.post("/api/v1/positionbook").mock(return_value=httpx.Response(503))
    runner.run_tick("schedule")
    row = only_decision(journal, today)
    assert (row.outcome, row.reason_code) == ("decline", "data-quality")


@pytest.mark.parametrize(
    ("payload", "expected_reason"),
    [
        ({"label": "trending", "confidence": 0.80}, "specialist-unavailable"),
        ({"label": "range-bound", "confidence": 0.95}, "specialist-unavailable"),
        ({"label": "event-driven", "confidence": 0.90}, "regime-not-tradeable"),
        ({"label": "unknown", "confidence": 0.10}, "regime-not-tradeable"),
        ({"label": "high-volatility", "confidence": 0.70}, "regime-not-tradeable"),
        ({"label": "trending", "confidence": 0.30}, "regime-low-confidence"),
        ({"label": "trending"}, "specialist-unavailable"),
    ],
)
def test_regime_verdicts_never_produce_an_entry(
    runner, deps, journal, today, payload, expected_reason
):
    deps.registry.register(StubSpecialist(payload=payload, model_version="stub-1"))
    runner.run_tick("schedule")
    row = only_decision(journal, today)
    assert row.outcome == "decline"
    assert row.reason_code == expected_reason


def test_answered_regime_is_journalled(runner, deps, journal, today):
    deps.registry.register(
        StubSpecialist(
            payload={"label": "trending", "confidence": 0.72},
            model_version="claude-haiku-4-5",
            token_cost_micros=1450,
        )
    )
    runner.run_tick("schedule")
    row = only_decision(journal, today)
    assert row.regime_label == "trending"
    assert row.regime_confidence == pytest.approx(0.72)
    assert row.model_version == "claude-haiku-4-5"
    assert row.token_cost_micros == 1450
    assert "strategist" in row.reason_text


def test_slow_specialist_declines_on_timeout(runner, deps, journal, today):
    class Slow:
        role = "regime"

        def run(self, request):
            time.sleep(2.0)
            raise AssertionError("should never be reached")

    deps.registry.register(Slow())
    runner.run_tick("schedule")
    row = only_decision(journal, today)
    assert (row.outcome, row.reason_code) == ("decline", "specialist-timeout")


def all_spans(journal):
    from sqlalchemy import select

    from strike_desk.journal import TraceSpan

    with journal.session_scope() as session:
        return list(session.execute(select(TraceSpan)).scalars())


def test_kill_switch_blocks_the_tick(runner, settings, journal, today):
    settings.kill_switch_path.write_text("2026-07-22T05:30:00+00:00 stopped by test")
    assert runner.run_tick("schedule") is None
    assert journal.count_decisions(today) == 0

    spans = all_spans(journal)
    assert len(spans) == 1  # a blocked tick emits the root span and nothing else
    attributes = json.loads(spans[0].attributes_json)
    assert attributes["tick.skipped"] is True
    assert attributes["gate.blocked_by"] == "kill-switch"


def test_overlapping_trigger_is_skipped_not_queued(runner, journal, openalgo, today):
    release = threading.Event()

    def slow_positions(request: httpx.Request) -> httpx.Response:
        release.wait(timeout=5)
        return httpx.Response(200, json={"status": "success", "data": []})

    openalgo.post("/api/v1/positionbook").mock(side_effect=slow_positions)
    worker = threading.Thread(target=runner.run_tick, args=("schedule",), daemon=True)
    worker.start()
    time.sleep(0.3)

    assert runner.run_tick("manual") is None  # skipped, not queued

    release.set()
    worker.join(timeout=10)
    assert journal.count_decisions(today) == 1

    roots = [
        json.loads(span.attributes_json)
        for span in all_spans(journal)
        if span.name == "strike_desk.tick"
    ]
    assert any(attributes.get("gate.blocked_by") == "overlap" for attributes in roots)


def test_journal_failure_fails_the_tick_closed(runner, deps, monkeypatch, journal, today):
    def explode(**_fields):
        raise JournalWriteError("disk is read-only")

    monkeypatch.setattr(deps.journal, "record_decision", explode)
    with pytest.raises(JournalWriteError):
        runner.run_tick("schedule")
    assert journal.count_decisions(today) == 0


def test_span_write_failure_marks_the_trace_incomplete(runner, deps, journal, today, monkeypatch):
    original = deps.journal.record_span
    calls = {"n": 0}

    def flaky(**fields):
        calls["n"] += 1
        if calls["n"] == 1:
            raise JournalWriteError("span sink unavailable")
        return original(**fields)

    monkeypatch.setattr(deps.journal, "record_span", flaky)
    runner.run_tick("schedule")
    assert only_decision(journal, today).trace_complete is False


def test_unexpected_error_is_journalled_as_internal_error(runner, deps, journal, today, monkeypatch):
    import strike_desk.graph as graph_module

    def explode(*_args, **_kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(graph_module, "read_book_state", explode)
    runner.run_tick("schedule")
    row = only_decision(journal, today)
    assert (row.outcome, row.reason_code) == ("decline", "internal-error")
    assert row.reason_text.strip()


def test_latency_is_recorded(runner, journal, today):
    runner.run_tick("schedule")
    assert only_decision(journal, today).latency_ms >= 0
```

Because several of these files import helpers from `tests.conftest`, make `tests` and `tests/regression` importable packages — `touch tests/__init__.py tests/regression/__init__.py` — which also puts `strike_desk/` on `sys.path` for the test run.

## 5. Guardrail tests

### `strike_desk/tests/test_guardrails.py`

These assert the properties that must survive every future change. They run as their own CI step, first, so a breach is unmissable.

```python
"""Guardrails: no order path, no entry, no secrets in the record."""

from __future__ import annotations

import itertools
import json

import pytest

from strike_desk.errors import ReadOnlyViolation
from strike_desk.graph import (
    OUTCOME_DECLINE,
    OUTCOME_HOLD,
    TRADEABLE_REGIMES,
    _decide_outcome,
)
from strike_desk.openalgo_client import READ_ONLY_PATHS
from tests.conftest import API_KEY, StubSpecialist


def test_read_only_whitelist_is_exactly_the_four_read_paths():
    assert READ_ONLY_PATHS == {
        "/api/v1/ping",
        "/api/v1/funds",
        "/api/v1/positionbook",
        "/api/v1/market/timings",
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/placeorder",
        "/api/v1/placesmartorder",
        "/api/v1/optionsorder",
        "/api/v1/basketorder",
        "/api/v1/closeposition",
        "/api/v1/modifyorder",
    ],
)
def test_order_paths_are_unreachable(client, path):
    with pytest.raises(ReadOnlyViolation):
        client._post(path)  # noqa: SLF001


def test_no_state_can_produce_an_entry(settings):
    """Exhaustive sweep of the decision table: `enter` is unreachable in this slice."""
    labels = [*TRADEABLE_REGIMES, "event-driven", "high-volatility", "unknown", "nonsense"]
    books = [None, {"open_positions": []}, {"open_positions": [{"symbol": "NIFTY...CE"}]}]
    errors = [
        None,
        {"role": "regime", "kind": "timeout", "detail": "x"},
        {"role": "regime", "kind": "unavailable", "detail": "x"},
    ]
    for label, book, error, confidence, budget, book_error in itertools.product(
        labels, books, errors, [0.0, 0.54, 0.55, 1.0], [False, True], [None, "boom"]
    ):
        state = {
            "budget_exceeded": budget,
            "book_error": book_error,
            "book": book,
            "specialist_error": error,
            "regime_label": label,
            "regime_confidence": confidence,
        }
        outcome, reason_code, reason_text = _decide_outcome(state, settings)
        assert outcome in {OUTCOME_DECLINE, OUTCOME_HOLD}, (state, outcome)
        assert reason_code and reason_text


def test_api_key_never_reaches_the_journal_or_traces(runner, journal, today):
    runner.run_tick("schedule")
    with journal.session_scope() as session:
        from sqlalchemy import select

        from strike_desk.journal import Decision, TraceSpan

        blob = json.dumps(
            [
                [row.book_state_json, row.reason_text]
                for row in session.execute(select(Decision)).scalars()
            ]
            + [span.attributes_json for span in session.execute(select(TraceSpan)).scalars()]
        )
    assert API_KEY not in blob


def test_redactor_removes_exact_secrets_and_sensitive_keys():
    from strike_desk.observability import REDACTED, Redactor

    redactor = Redactor([API_KEY])
    payload = {"apikey": API_KEY, "note": f"called with {API_KEY}", "symbol": "NIFTY28JUL2624500CE"}
    cleaned = redactor(payload)
    assert cleaned["apikey"] == REDACTED
    assert API_KEY not in cleaned["note"]
    assert cleaned["symbol"] == "NIFTY28JUL2624500CE"  # no false positives


def test_a_registered_regime_specialist_still_cannot_cause_an_entry(runner, deps, journal, today):
    deps.registry.register(
        StubSpecialist(payload={"label": "trending", "confidence": 1.0}, model_version="stub")
    )
    runner.run_tick("schedule")
    assert journal.list_decisions(today)[0].outcome == "decline"
```

## 6. The regression gate

No model runs in this slice, so there is no output-quality score to hold a pass-bar against. What there *is* — and what must not silently drift — is the decision table: the mapping from a tick's observed state to what the desk does about it. Freeze it in data, assert it in code, and any edit that changes a known verdict turns CI red and forces the author to justify the change by updating the golden file.

### `strike_desk/tests/regression/tick_scenarios.json`

```json
{
  "pass_bar": "every scenario must match exactly; no scenario may yield 'enter'",
  "min_regime_confidence": 0.55,
  "scenarios": [
    {
      "id": "REG-01",
      "note": "flat book, nothing registered — the desk's default posture",
      "state": {"book": {"open_positions": []}},
      "expect": {"outcome": "decline", "reason_code": "specialist-unavailable"}
    },
    {
      "id": "REG-02",
      "note": "one open index position — manage, never add",
      "state": {"book": {"open_positions": [{"symbol": "NIFTY28JUL2624500CE"}]}},
      "expect": {"outcome": "hold", "reason_code": "position-open"}
    },
    {
      "id": "REG-03",
      "note": "book unreadable — never assumed flat",
      "state": {"book_error": "positionbook: HTTP 503"},
      "expect": {"outcome": "decline", "reason_code": "data-quality"}
    },
    {
      "id": "REG-04",
      "note": "specialist hung",
      "state": {
        "book": {"open_positions": []},
        "specialist_error": {"role": "regime", "kind": "timeout", "detail": "exceeded 12.0s"}
      },
      "expect": {"outcome": "decline", "reason_code": "specialist-timeout"}
    },
    {
      "id": "REG-05",
      "note": "event-driven regime is not tradeable at MVP",
      "state": {
        "book": {"open_positions": []},
        "regime_label": "event-driven",
        "regime_confidence": 0.95
      },
      "expect": {"outcome": "decline", "reason_code": "regime-not-tradeable"}
    },
    {
      "id": "REG-06",
      "note": "unknown is always reachable and always safe",
      "state": {
        "book": {"open_positions": []},
        "regime_label": "unknown",
        "regime_confidence": 0.20
      },
      "expect": {"outcome": "decline", "reason_code": "regime-not-tradeable"}
    },
    {
      "id": "REG-07",
      "note": "tradeable but under the confidence floor",
      "state": {
        "book": {"open_positions": []},
        "regime_label": "trending",
        "regime_confidence": 0.54
      },
      "expect": {"outcome": "decline", "reason_code": "regime-low-confidence"}
    },
    {
      "id": "REG-08",
      "note": "exactly on the confidence floor is not a breach — the floor is inclusive",
      "state": {
        "book": {"open_positions": []},
        "regime_label": "trending",
        "regime_confidence": 0.55
      },
      "expect": {"outcome": "decline", "reason_code": "specialist-unavailable"}
    },
    {
      "id": "REG-09",
      "note": "tradeable and confident still needs a contract proposal",
      "state": {
        "book": {"open_positions": []},
        "regime_label": "range-bound",
        "regime_confidence": 0.88
      },
      "expect": {"outcome": "decline", "reason_code": "specialist-unavailable"}
    },
    {
      "id": "REG-10",
      "note": "budget overrun beats every other consideration",
      "state": {
        "budget_exceeded": true,
        "book": {"open_positions": []},
        "regime_label": "trending",
        "regime_confidence": 0.99
      },
      "expect": {"outcome": "decline", "reason_code": "tick-timeout"}
    }
  ]
}
```

### `strike_desk/tests/regression/test_tick_scenarios.py`

```python
"""Frozen decision-table regression gate. A changed verdict must change this file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from strike_desk.graph import _decide_outcome

SUITE = json.loads((Path(__file__).parent / "tick_scenarios.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("scenario", SUITE["scenarios"], ids=lambda s: s["id"])
def test_frozen_scenario(settings, scenario):
    settings.min_regime_confidence = SUITE["min_regime_confidence"]
    outcome, reason_code, reason_text = _decide_outcome(scenario["state"], settings)
    assert outcome == scenario["expect"]["outcome"], scenario["note"]
    assert reason_code == scenario["expect"]["reason_code"], scenario["note"]
    assert reason_text.strip(), "every verdict must carry a human-readable sentence"


def test_no_scenario_permits_an_entry(settings):
    for scenario in SUITE["scenarios"]:
        assert scenario["expect"]["outcome"] != "enter"


def test_every_reason_code_is_covered_by_the_suite():
    """The frozen suite must exercise every reason code the table can produce."""
    from strike_desk import graph

    reachable = {
        value
        for name, value in vars(graph).items()
        if name.startswith("REASON_") and name != "REASON_INTERNAL_ERROR"
    }
    covered = {scenario["expect"]["reason_code"] for scenario in SUITE["scenarios"]}
    assert reachable <= covered, f"uncovered reason codes: {sorted(reachable - covered)}"
```

That last test is the one that keeps the gate honest over time: add a branch to the decision table without adding a frozen scenario for it and CI fails, so the suite cannot rot behind the code.

## 7. Running it

Locally, from `strike_desk/`:

```bash
uv sync --group dev
uv run ruff check .
uv run pytest tests/test_guardrails.py tests/regression -q     # the gate, first
uv run pytest --cov=strike_desk --cov-report=term-missing      # everything
```

The coverage floor is 80%, which the modules that matter clear comfortably — `graph.py`, `session.py`, `journal.py`, `book_state.py`, `specialists.py` and `openalgo_client.py` should each sit above 90%, while `service.py` and `__main__.py` are thin composition and CLI plumbing covered by the manual pass instead. Coverage is a smoke alarm here, not a target: the guardrail and regression files are what actually protect the slice.

### `.github/workflows/strike-desk-ci.yml`

Place this at the repository root, scoped by path so it only runs when Strike Desk changes.

```yaml
name: strike-desk

on:
  push:
    paths: ["strike_desk/**", ".github/workflows/strike-desk-ci.yml"]
  pull_request:
    paths: ["strike_desk/**", ".github/workflows/strike-desk-ci.yml"]

jobs:
  verify:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: strike_desk
    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Install Python 3.12 and dependencies
        run: |
          uv python install 3.12
          uv sync --group dev

      - name: Lint
        run: uv run ruff check .

      - name: Guardrail and regression gate
        run: uv run pytest tests/test_guardrails.py tests/regression -q

      - name: Full suite with coverage
        run: uv run pytest --cov=strike_desk --cov-report=term-missing --cov-fail-under=80

      - name: Security scan
        run: |
          uv run bandit -q -r src
          uv run pip-audit
```

`pip-audit` is not decorative: this slice pins `langgraph-checkpoint-sqlite` at a version chosen specifically to carry the checkpointer SQL-injection fix, and the job is what tells you when the next such advisory lands.

## 8. Traceability

| Automated test | Acceptance criterion | Manual case it backstops |
| --- | --- | --- |
| `test_tick.py::test_flat_book_with_no_specialists_declines` | AC-1, AC-4, AC-11 | MT-01, MT-02 |
| `test_tick.py::test_open_position_holds_without_consulting` | AC-3 | MT-06 |
| `test_tick.py::test_unreadable_book_declines_on_data_quality` | AC-13 | MT-09, MT-10 |
| `test_tick.py::test_regime_verdicts_never_produce_an_entry` | AC-4, AC-5 | MT-18 |
| `test_tick.py::test_answered_regime_is_journalled` | AC-1, AC-11 | MT-18 |
| `test_tick.py::test_slow_specialist_declines_on_timeout` | AC-4 | MT-18 |
| `test_tick.py::test_kill_switch_blocks_the_tick` | AC-2, AC-7 | MT-07 |
| `test_tick.py::test_overlapping_trigger_is_skipped_not_queued` | AC-6 | MT-08 |
| `test_tick.py::test_journal_failure_fails_the_tick_closed` | AC-8 | MT-17 |
| `test_tick.py::test_span_write_failure_marks_the_trace_incomplete` | AC-10 | MT-12 |
| `test_tick.py::test_unexpected_error_is_journalled_as_internal_error` | AC-1 | MT-10 |
| `test_tick.py::test_latency_is_recorded` | AC-12 | MT-14 |
| `test_session.py::test_window_boundaries` | AC-2 | MT-03, MT-04 |
| `test_session.py::test_holiday_is_market_closed` | AC-2 | MT-03 |
| `test_session.py::test_kill_switch_wins_over_every_other_gate` | AC-7 | MT-07 |
| `test_session.py::test_expiry_cutoff_blocks_after_the_configured_time` | AC-2 | MT-05 |
| `test_session.py::test_expiry_walks_back_over_a_holiday` | AC-2 | MT-05 |
| `test_session.py::test_calendar_failure_blocks_rather_than_trades` | AC-13 | MT-10 |
| `test_journal.py::test_tables_are_append_only` | AC-9 | MT-11 |
| `test_journal.py::test_duplicate_tick_id_raises_journal_write_error` | AC-8 | MT-17 |
| `test_journal.py::test_spans_for_trace_are_ordered` | AC-10 | MT-12 |
| `test_book_state.py::*` | AC-3, AC-13 | MT-06, MT-09 |
| `test_specialists.py::*` | AC-4 | MT-18 |
| `test_guardrails.py::test_read_only_whitelist_is_exactly_the_four_read_paths` | AC-5 | MT-15 |
| `test_guardrails.py::test_order_paths_are_unreachable` | AC-5 | MT-15 |
| `test_guardrails.py::test_no_state_can_produce_an_entry` | AC-5 | MT-15 |
| `test_guardrails.py::test_api_key_never_reaches_the_journal_or_traces` | AC-14 | MT-13 |
| `test_guardrails.py::test_redactor_removes_exact_secrets_and_sensitive_keys` | AC-14 | MT-13 |
| `regression/test_tick_scenarios.py::test_frozen_scenario` | AC-1, AC-3, AC-4, AC-12 | MT-01, MT-06, MT-14, MT-18 |
| `regression/test_tick_scenarios.py::test_no_scenario_permits_an_entry` | AC-5 | MT-15 |
| `regression/test_tick_scenarios.py::test_every_reason_code_is_covered_by_the_suite` | AC-1 | — |

Two things are deliberately verified by hand rather than in CI. The half of AC-11 that concerns a prompt file *arriving* — the set version changing when `prompts/` gains a file between service starts — is MT-16's job, because it depends on disk state across process restarts. And AC-7's *timing* guarantee, that the kill switch binds within one cadence interval, is a scheduler property MT-07 observes on the wall clock. Everything else above is gated automatically.
