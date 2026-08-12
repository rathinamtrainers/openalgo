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


def timings_for(
    day: date, exchange: str = "NFO", start: time = time(0, 0), end: time = time(23, 59)
):
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
