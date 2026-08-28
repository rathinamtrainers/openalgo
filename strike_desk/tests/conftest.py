"""Shared fixtures: an isolated Strike Desk per test."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from opentelemetry import trace as otel_trace
from pydantic import Field

import strike_desk
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
MODEL_KEY = "test-anthropic-key-0123456789abcdef"
PACKAGED_PROMPTS = Path(strike_desk.__file__).parent / "prompts"


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
        anthropic_api_key=MODEL_KEY,
        openalgo_retries=0,
        openalgo_timeout_seconds=2.0,
        state_dir=state_dir,
        prompts_dir=PACKAGED_PROMPTS,
        tick_interval_seconds=60,
        tick_budget_seconds=20.0,
        specialist_timeout_seconds=0.5,
        strategist_timeout_seconds=2.0,
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
    provider, processor = configure_tracing(settings, journal, Redactor([API_KEY, MODEL_KEY]))
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
def prompts(settings: Settings) -> PromptRegistry:
    return PromptRegistry.load(settings.prompts_dir)


@pytest.fixture
def deps(settings, client, journal, registry, tracing, prompts) -> TickDeps:
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
        prompts=prompts,
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
        self._payload = {"status": "ok", **(payload or {})}
        self._kwargs = kwargs

    def run(self, request):
        from strike_desk.specialists import SpecialistResult

        return SpecialistResult(role=self.role, payload=self._payload, **self._kwargs)


# --- doubles for the reasoning plane ---------------------------------------


class CannedTool(BaseTool):
    """A market tool that returns a fixed string and remembers how it was called."""

    name: str
    description: str = "canned market tool"
    output: str = "{}"
    raises: bool = False
    record: list[dict[str, Any]] = Field(default_factory=list)

    def _run(self, **kwargs: Any) -> str:
        self.record.append(dict(kwargs))
        if self.raises:
            raise RuntimeError("tool exploded")
        return self.output

    async def _arun(self, **kwargs: Any) -> str:
        return self._run(**kwargs)


def ai_message(
    tool_calls: list[dict[str, Any]] | None = None,
    content: str = "",
    input_tokens: int = 900,
    output_tokens: int = 120,
    stop_reason: str = "tool_use",
) -> AIMessage:
    """One scripted assistant turn, shaped the way ChatAnthropic returns them."""
    calls = [
        {
            "name": call["name"],
            "args": call["args"],
            "id": call.get("id", f"call-{index}"),
            "type": "tool_call",
        }
        for index, call in enumerate(tool_calls or [])
    ]
    return AIMessage(
        content=content,
        tool_calls=calls,
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        response_metadata={"stop_reason": stop_reason},
    )


class ScriptedModel:
    """Stands in for ChatAnthropic and replays a fixed sequence of answers."""

    def __init__(self, answers: list[AIMessage], delay: float = 0.0) -> None:
        self.answers = list(answers)
        self.delay = delay
        self.bindings: list[tuple[list[str], Any]] = []
        self.rounds = 0

    def bind_tools(self, tools, tool_choice=None):
        self.bindings.append(([tool.name for tool in tools], tool_choice))
        return self

    async def ainvoke(self, messages):
        self.rounds += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if not self.answers:
            raise AssertionError("the analyst asked for more rounds than the script provides")
        return self.answers.pop(0)


class LocalToolSource:
    """Runs the analyst's coroutine in the calling thread, with canned tools."""

    def __init__(self, tools: list[BaseTool] | None = None) -> None:
        self._tools = tools or []
        self.starts = 0
        self.fail_to_start: Exception | None = None

    def ensure_started(self) -> None:
        self.starts += 1
        if self.fail_to_start is not None:
            raise self.fail_to_start

    def tools(self, role: str = "") -> list[BaseTool]:
        return list(self._tools)

    def submit(self, factory, timeout: float):
        async def _run():
            return await asyncio.wait_for(factory(), timeout)

        try:
            return asyncio.run(_run())
        except TimeoutError as exc:  # asyncio.TimeoutError is TimeoutError on 3.12
            raise TimeoutError(f"exceeded the {timeout:.1f}s analyst deadline") from exc


@pytest.fixture
def fake_tools() -> list[CannedTool]:
    """The union whitelist, each tool returning the recorded chain (or empty JSON)."""
    from strike_desk.mcp_toolbox import REQUIRED_TOOLS
    from tests.chain_fixtures import (
        expiry_dates,
        momentum_up,
        option_chain,
        option_greeks,
        option_symbol,
        trend_up,
    )

    outputs = {
        "get_expiry_dates": expiry_dates(),
        "get_option_chain": option_chain(),
        "get_option_symbol": option_symbol(),
        "get_option_greeks": option_greeks(),
        "get_trend_snapshot": trend_up(),
        "get_momentum_snapshot": momentum_up(),
        "get_quote": '{"ltp": 24812.35}',
        "get_historical_data": "{}",
        "get_volatility_snapshot": "{}",
    }
    return [CannedTool(name=name, output=outputs.get(name, "{}")) for name in REQUIRED_TOOLS]


@pytest.fixture
def strategist_harness(settings, prompts, fake_tools, tracing, journal):
    """A real OptionsStrategist over canned tools and a scripted model, clock frozen."""

    from freezegun import freeze_time

    from strike_desk.observability import get_tracer
    from strike_desk.options_strategist import OptionsStrategist
    from strike_desk.specialists import SpecialistRequest

    class Harness:
        def __init__(self) -> None:
            self._log: list[str] = []
            tools = list(fake_tools)
            for tool in tools:
                original = tool._run

                def _run(*, _original=original, _name=tool.name, **kwargs: Any) -> str:
                    self._log.append(_name)
                    return _original(**kwargs)

                tool._run = _run  # type: ignore[method-assign]
            self.source = LocalToolSource(tools)
            self.model = ScriptedModel([])
            self.strategist = OptionsStrategist(settings, prompts, self.source, self.model)
            self.trace_id = "0" * 32

        def executed_tools(self) -> list[str]:
            return list(self._log)

        def run(self, rounds, all_tools_fail: bool = False):
            if all_tools_fail:
                for tool in fake_tools:
                    tool.raises = True
            answers = [ai_message(calls) for calls in rounds]
            remaining = settings.strategist_max_rounds - len(answers)
            if remaining > 0:
                answers.extend(ai_message(content=".") for _ in range(remaining))
            self.model.answers = answers
            request = SpecialistRequest(
                tick_id="tick-1",
                index_symbol=settings.index_symbol,
                as_of=datetime.now(tz=UTC),
                book={
                    "open_positions": [],
                    "regime": {
                        "label": "trending",
                        "confidence": 0.78,
                        "rationale": "ADX at 27.4 confirms the move.",
                    },
                },
            )
            with freeze_time("2026-08-25T06:00:00+00:00"):
                with get_tracer().start_as_current_span("test.proposal") as span:
                    self.trace_id = format(span.get_span_context().trace_id, "032x")
                    return self.strategist.run(request)

    return Harness()


@pytest.fixture
def tick_state() -> dict[str, Any]:
    return {
        "tick_id": "tick-1",
        "trace_id": "0" * 32,
        "trigger": "schedule",
        "trading_day": "2026-08-25",
        "book": {"open_positions": []},
        "regime_label": "trending",
        "regime_confidence": 0.8,
        "regime_rationale": "ADX at 27.4.",
    }


@pytest.fixture
def tick_harness(runner, deps, journal, today, openalgo):
    """A real graph with stubbed regime and strategist specialists."""
    import uuid

    import httpx

    from strike_desk.errors import JournalWriteError
    from strike_desk.options_strategist import STATUS_PROPOSED
    from strike_desk.specialists import ROLE_REGIME, ROLE_STRATEGIST, SpecialistResult
    from tests.chain_fixtures import LOT_SIZE, RATIONALE, proposal_args
    from tests.risk_fixtures import book_with

    class Harness:
        def __init__(self) -> None:
            self.runner = runner
            self.deps = deps
            self.journal = journal
            self.today = today
            self.openalgo = openalgo
            self.analyst_calls = 0
            self.strategist_calls = 0
            self._regime = {
                "label": "trending",
                "confidence": 0.8,
                "rationale": "ADX at 27.4 confirms the move.",
            }
            self._proposal_status = STATUS_PROPOSED
            self._proposal = proposal_args()
            self._journal_fails = False
            self._register()

        def set_regime(self, label: str, confidence: float) -> None:
            self._regime = {
                "label": label,
                "confidence": confidence,
                "rationale": "ADX at 27.4 confirms the move.",
            }
            self._register()

        def set_proposal(
            self, status: str, journal_fails: bool = False, lots: int | None = None
        ) -> None:
            self._proposal_status = status
            self._journal_fails = journal_fails
            args = proposal_args()
            if lots is not None:
                args["lots"] = lots
                args["quantity"] = lots * LOT_SIZE
            self._proposal = args
            self._register()

        def set_book(self, **kwargs: Any) -> None:
            snapshot = book_with(**kwargs)
            funds = {
                "availablecash": str(snapshot["available_cash"]),
                "collateral": "0.00",
                "m2munrealized": str(snapshot["unrealised_pnl"]),
                "m2mrealized": str(snapshot["realised_pnl"]),
                "utiliseddebits": str(snapshot["utilised_margin"]),
            }
            self.openalgo.post("/api/v1/funds").mock(
                return_value=httpx.Response(200, json={"status": "success", "data": funds})
            )
            self.openalgo.post("/api/v1/positionbook").mock(
                return_value=httpx.Response(
                    200, json={"status": "success", "data": snapshot["open_positions"]}
                )
            )

        def _register(self) -> None:
            harness = self

            class CountingAnalyst:
                role = ROLE_REGIME

                def run(self, request):
                    harness.analyst_calls += 1
                    return StubSpecialist(
                        payload={
                            "label": harness._regime["label"],
                            "confidence": harness._regime["confidence"],
                            "rationale": harness._regime["rationale"],
                        },
                        model_version="stub-haiku",
                    ).run(request)

            self.deps.registry.register(CountingAnalyst())

            class CountingStrategist:
                role = ROLE_STRATEGIST

                def run(self, request):
                    harness.strategist_calls += 1
                    return SpecialistResult(
                        role=ROLE_STRATEGIST,
                        payload={
                            "proposal_id": str(uuid.uuid4()),
                            "status": harness._proposal_status,
                            "proposal": harness._proposal,
                            "rationale": RATIONALE,
                            "evidence": [],
                            "calls": [],
                            "playbook_artifact": "pb-1+testdigest",
                            "playbook_verdict": "pass",
                            "violations": [],
                            "defect": None,
                            "tool_call_count": 6,
                            "tool_error_count": 0,
                            "rejected_tool_count": 0,
                            "model_calls": 2,
                            "input_tokens": 100,
                            "output_tokens": 40,
                            "latency_ms": 12,
                            "prompt_name": "options_strategist",
                            "prompt_version": "v1",
                            "prompt_digest": "d" * 16,
                        },
                        model_version="stub-sonnet",
                        token_cost_micros=100,
                    )

            self.deps.registry.register(CountingStrategist())

        def proposal_rows(self):
            return self.journal.list_proposals(self.today)

        def decision_rows(self):
            return self.journal.list_decisions(self.today)

        def verdict_rows(self):
            return self.journal.list_risk_verdicts(self.today)

        def requested_paths(self) -> set[str]:
            return {str(call.request.url.path) for call in self.openalgo.calls}

        def run_tick(self):
            from freezegun import freeze_time

            if self._journal_fails:

                def explode(**_fields):
                    raise JournalWriteError("disk is read-only")

                self.deps.journal.record_proposal = explode  # type: ignore[method-assign]
            self.analyst_calls = 0
            self.strategist_calls = 0
            # Adjudication re-checks a reduced proposal against the playbook clock.
            # Freeze to the session's morning so a 14:45 time-stop stays in the future.
            frozen = datetime.now(tz=IST).replace(hour=11, minute=30, second=0, microsecond=0)
            with freeze_time(frozen):
                self.runner.run_tick("schedule")
            rows = self.journal.list_decisions(self.today)
            return rows[0] if rows else None

    return Harness()
