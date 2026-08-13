# Iteration 02 — Test Automation

## 1. Framework, and what is real

The suite stays on **pytest 9.1.1** with the layout iteration 01 established, and it grows in two directions. The deterministic half replaces the model and the MCP session with doubles: a `ScriptedModel` that replays a fixed sequence of assistant messages, and a `LocalToolSource` that runs the analyst's coroutine in the calling thread with canned tool outputs. Everything about the loop — round counting, forced submission, truncation, evidence capture, validation, statuses, spans and journal rows — is exercised there, deterministically, for free, on every push.

The second half cannot be faked without defeating its own purpose. A prompt is only good if a real model reads it, so the **evaluation suite** runs the shipped prompt against a live `claude-haiku-4-5` over frozen market snapshots and scores the answers. Those tests carry the `evals` marker and are excluded from the default run by `addopts`, so they never surprise you with a bill; CI runs them on any change to a prompt, the analyst or the model configuration.

Nothing in the suite calls the real Anthropic API in the default run, spawns the MCP subprocess, or touches OpenAlgo: HTTP to OpenAlgo remains mocked with `respx` as before, and the MCP toolbox is unit-tested at its seams — tool selection, connection construction, lifecycle guards — rather than by launching a server. Spawning a real MCP server is what `03_manual_test_cases.md` MT-01 and MT-04 are for.

## 2. Shared fixtures

### `strike_desk/tests/conftest.py`

Two changes to what iteration 01 wrote, and three additions. The `settings` fixture now carries an Anthropic key and points `prompts_dir` at the packaged prompt directory, so tests load the artifact you actually ship. `StubSpecialist` defaults its payload's `status` to `ok`, which keeps every iteration-01 test valid under the richer payload contract. The additions are the model double, the tool double and a canned tool.

```python
"""Shared fixtures: an isolated Strike Desk per test."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time
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
        {"name": call["name"], "args": call["args"], "id": call.get("id", f"call-{index}"),
         "type": "tool_call"}
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

    def tools(self) -> list[BaseTool]:
        return list(self._tools)

    def submit(self, factory, timeout: float):
        async def _run():
            return await asyncio.wait_for(factory(), timeout)

        try:
            return asyncio.run(_run())
        except TimeoutError as exc:  # asyncio.TimeoutError is TimeoutError on 3.12
            raise TimeoutError(f"exceeded the {timeout:.1f}s analyst deadline") from exc
```

The three doubles are deliberately small. `ScriptedModel` records what was bound to it, which is how the whitelist and the forced final round are asserted; `LocalToolSource` uses `asyncio.wait_for` so the analyst's deadline behaves the way the real toolbox's `run_coroutine_threadsafe` cancellation does; `CannedTool` records its arguments so a test can prove the agent passed what the prompt told it to.

## 3. The market fixtures the loop reads

### `strike_desk/tests/market_fixtures.py`

Both the unit tests and the evaluation suite need believable tool output. Keeping it in one module means a change to the shape of OpenAlgo's payloads is a one-file fix.

```python
"""Believable OpenAlgo tool payloads, small enough to read in a diff."""

from __future__ import annotations

import json
from typing import Any


def quote(ltp: float, oi: int = 0, volume: int = 0) -> str:
    return json.dumps(
        {
            "status": "success",
            "data": {
                "ltp": ltp,
                "open": round(ltp * 0.996, 2),
                "high": round(ltp * 1.004, 2),
                "low": round(ltp * 0.994, 2),
                "prev_close": round(ltp * 0.998, 2),
                "volume": volume,
                "oi": oi,
            },
        },
        indent=2,
    )


def trend(last_close: float, sma_20: float, sma_50: float, adx: float, direction: int) -> str:
    return json.dumps(
        {
            "symbol": "NIFTY",
            "exchange": "NSE_INDEX",
            "interval": "15m",
            "bars_loaded": 252,
            "last_close": last_close,
            "indicators": {
                "sma_20": sma_20,
                "sma_50": sma_50,
                "ema_20": round((sma_20 + last_close) / 2, 2),
                "supertrend": [round(last_close * 0.995, 2), direction],
                "adx_di": [22.4, 18.1, adx],
            },
            "legend": {"adx_di": "[+DI, -DI, ADX]"},
        },
        indent=2,
    )


def momentum(rsi: float, macd_hist: float) -> str:
    return json.dumps(
        {
            "symbol": "NIFTY",
            "interval": "15m",
            "indicators": {
                "rsi_14": rsi,
                "macd": [12.4, 9.8, macd_hist],
                "stochastic": [61.2, 58.7],
                "cci_20": 74.3,
            },
        },
        indent=2,
    )


def volatility(atr: float, bb_width: float, hv: float) -> str:
    return json.dumps(
        {
            "symbol": "NIFTY",
            "interval": "15m",
            "indicators": {
                "atr_14": atr,
                "natr_14": round(atr / 245.0, 3),
                "bb_width": bb_width,
                "historical_volatility": hv,
            },
        },
        indent=2,
    )


def expiries(dates: list[str]) -> str:
    return json.dumps({"status": "success", "data": dates}, indent=2)


TREND_SNAPSHOT: dict[str, Any] = {
    "get_quote": quote(24512.35),
    "get_trend_snapshot": trend(24512.35, 24380.10, 24105.60, 31.7, 1),
    "get_momentum_snapshot": momentum(63.8, 4.6),
    "get_volatility_snapshot": volatility(88.4, 2.1, 11.9),
    "get_expiry_dates": expiries(["26AUG26", "24SEP26"]),
    "get_historical_data": json.dumps({"count": 60, "returned": 5, "data": []}),
}
```

## 4. Unit tests

### `strike_desk/tests/test_events.py`

```python
"""The event calendar: absent, valid, active, and malformed."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from strike_desk.config import IST
from strike_desk.errors import EventCalendarInvalid
from strike_desk.events import active_window, load_event_windows


def write(path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_absent_calendar_is_no_windows(tmp_path):
    assert load_event_windows(tmp_path / "events.json") == ()


def test_windows_are_parsed_and_ordered(tmp_path):
    path = tmp_path / "events.json"
    write(
        path,
        [
            {"name": "US CPI", "start": "2026-08-19T17:45:00", "end": "2026-08-19T18:30:00"},
            {"name": "RBI policy", "start": "2026-08-14T09:45:00", "end": "2026-08-14T11:00:00"},
        ],
    )
    windows = load_event_windows(path)
    assert [window.name for window in windows] == ["RBI policy", "US CPI"]
    assert windows[0].start.tzinfo is not None


def test_active_window_is_half_open(tmp_path):
    path = tmp_path / "events.json"
    start = datetime(2026, 8, 14, 9, 45, tzinfo=IST)
    end = datetime(2026, 8, 14, 11, 0, tzinfo=IST)
    write(path, [{"name": "RBI policy", "start": start.isoformat(), "end": end.isoformat()}])
    windows = load_event_windows(path)

    assert active_window(windows, start) is not None
    assert active_window(windows, start + timedelta(minutes=30)) is not None
    assert active_window(windows, end) is None
    assert active_window(windows, start - timedelta(seconds=1)) is None


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        json.dumps({"name": "x"}),
        json.dumps([{"start": "2026-08-14T09:45:00", "end": "2026-08-14T11:00:00"}]),
        json.dumps([{"name": "x", "start": "yesterday", "end": "2026-08-14T11:00:00"}]),
        json.dumps([{"name": "x", "start": "2026-08-14T11:00:00", "end": "2026-08-14T09:45:00"}]),
    ],
)
def test_malformed_calendar_raises(tmp_path, payload):
    path = tmp_path / "events.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(EventCalendarInvalid):
        load_event_windows(path)
```

### `strike_desk/tests/test_grounding.py`

The validator is the guardrail with the most surface, so it gets the most cases: rounding must pass, invention must fail, and a tool that was never called must never be citable.

```python
"""Grounding: the ledger, the tolerance, and the refusal."""

from __future__ import annotations

import pytest

from strike_desk.grounding import (
    EvidenceLedger,
    RegimeSubmission,
    ToolObservation,
    extract_numbers,
    validate_submission,
)


def observation(tool: str, output: str, ok: bool = True, args=None) -> ToolObservation:
    return ToolObservation(
        call_id="c1",
        tool=tool,
        args=args or {"symbol": "NIFTY"},
        output=output,
        ok=ok,
        latency_ms=12,
        truncated=False,
    )


def ledger_with(*observations) -> EvidenceLedger:
    ledger = EvidenceLedger()
    for item in observations:
        ledger.record(item)
    return ledger


def submission(rationale: str, evidence=None, label: str = "trending") -> RegimeSubmission:
    return RegimeSubmission(
        label=label,
        confidence=0.72,
        rationale=rationale,
        evidence=evidence or [{"tool": "get_trend_snapshot", "field": "adx_di", "value": "31.7"}],
    )


def test_extract_numbers_handles_separators_and_decimals():
    assert extract_numbers('{"ltp": 24,512.35, "oi": 1200}') == {24512.35, 1200.0}


def test_grounded_rationale_passes():
    ledger = ledger_with(observation("get_trend_snapshot", '{"adx_di": [22.4, 18.1, 31.7]}'))
    assert validate_submission(submission("ADX at 31.7 confirms the move."), ledger, 320) is None


def test_rounding_at_the_cited_precision_passes():
    ledger = ledger_with(observation("get_trend_snapshot", '{"adx_di": [22.4, 18.1, 31.74]}'))
    assert validate_submission(submission("ADX reads 31.7."), ledger, 320) is None


def test_invented_number_fails():
    ledger = ledger_with(observation("get_trend_snapshot", '{"adx_di": [22.4, 18.1, 31.7]}'))
    defect = validate_submission(submission("ADX at 38.2 confirms the move."), ledger, 320)
    assert defect is not None and "38.2" in defect


def test_number_from_a_failed_call_is_not_grounded():
    ledger = ledger_with(observation("get_quote", '{"ltp": 24512.35}', ok=False))
    defect = validate_submission(submission("Spot at 24512.35."), ledger, 320)
    assert defect is not None


def test_evidence_from_an_uncalled_tool_fails():
    ledger = ledger_with(observation("get_quote", '{"ltp": 24512.35}'))
    defect = validate_submission(
        submission(
            "Spot at 24512.35.",
            evidence=[{"tool": "get_option_chain", "field": "pcr", "value": "1.2"}],
        ),
        ledger,
        320,
    )
    assert defect is not None and "get_option_chain" in defect


def test_ungrounded_evidence_value_fails():
    ledger = ledger_with(observation("get_quote", '{"ltp": 24512.35}'))
    defect = validate_submission(
        submission(
            "Spot is holding above its open.",
            evidence=[{"tool": "get_quote", "field": "ltp", "value": "24999.00"}],
        ),
        ledger,
        320,
    )
    assert defect is not None and "get_quote.ltp" in defect


def test_overlong_rationale_fails():
    ledger = ledger_with(observation("get_quote", '{"ltp": 24512.35}'))
    defect = validate_submission(submission("word " * 200), ledger, 320)
    assert defect is not None and "cap" in defect


@pytest.mark.parametrize("label", ["trend", "TRENDING", "bullish", ""])
def test_labels_outside_the_fixed_set_are_rejected_by_the_schema(label):
    with pytest.raises(ValueError):
        RegimeSubmission(label=label, confidence=0.5, rationale="a" * 20, evidence=[])


def test_confidence_outside_zero_to_one_is_rejected():
    with pytest.raises(ValueError):
        RegimeSubmission(
            label="trending",
            confidence=1.4,
            rationale="a" * 20,
            evidence=[{"tool": "t", "field": "f", "value": "1"}],
        )
```

### `strike_desk/tests/test_mcp_toolbox.py`

```python
"""The toolbox at its seams: selection, connection, and lifecycle guards."""

from __future__ import annotations

import pytest

from strike_desk.errors import McpUnavailable
from strike_desk.mcp_toolbox import REGIME_TOOLS, McpToolbox, select_tools
from tests.conftest import API_KEY, CannedTool


def tools_named(*names: str) -> list[CannedTool]:
    return [CannedTool(name=name) for name in names]


def test_whitelist_is_exactly_six_read_only_market_tools():
    assert REGIME_TOOLS == (
        "get_quote",
        "get_historical_data",
        "get_trend_snapshot",
        "get_momentum_snapshot",
        "get_volatility_snapshot",
        "get_expiry_dates",
    )


def test_selection_keeps_the_whitelist_and_drops_everything_else():
    loaded = tools_named(*REGIME_TOOLS, "place_order", "close_all_positions", "send_telegram_alert")
    selected = select_tools(loaded)
    assert [tool.name for tool in selected] == list(REGIME_TOOLS)


@pytest.mark.parametrize("missing", REGIME_TOOLS)
def test_a_missing_tool_fails_the_session_closed(missing):
    loaded = tools_named(*[name for name in REGIME_TOOLS if name != missing])
    with pytest.raises(McpUnavailable, match=missing):
        select_tools(loaded)


def test_connection_names_the_interpreter_the_script_and_the_key(settings, tmp_path):
    python = tmp_path / "python"
    script = tmp_path / "mcpserver.py"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    script.write_text("print('hi')\n", encoding="utf-8")
    box = McpToolbox(settings.model_copy(update={"mcp_python": python, "mcp_server_script": script}))

    connection = box._connection()  # noqa: SLF001
    assert connection["transport"] == "stdio"
    assert connection["command"] == str(python)
    assert connection["args"] == [str(script), API_KEY, settings.openalgo_base_url]
    assert connection["cwd"] == str(script.parent)  # keeps the installed mcp package resolvable
    assert "TZ" in connection["env"]


@pytest.mark.parametrize("field", ["mcp_python", "mcp_server_script"])
def test_missing_binaries_raise_before_anything_is_spawned(settings, tmp_path, field):
    present = tmp_path / "present"
    present.write_text("x", encoding="utf-8")
    update = {"mcp_python": present, "mcp_server_script": present, field: tmp_path / "absent"}
    with pytest.raises(McpUnavailable):
        McpToolbox(settings.model_copy(update=update))._connection()  # noqa: SLF001


def test_using_a_stopped_toolbox_raises_rather_than_hanging(settings):
    box = McpToolbox(settings)
    with pytest.raises(McpUnavailable):
        box.tools()
    with pytest.raises(McpUnavailable):
        box.submit(lambda: None, timeout=1.0)
    box.close()  # idempotent, and safe on a toolbox that never started
```

### `strike_desk/tests/test_regime_analyst.py`

This is the heart of the suite: the loop, with the model and the tools replaced, asserted on statuses, spans, costs and journal-ready payloads.

```python
"""The Regime Analyst loop, with the model and the tools scripted."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from strike_desk.config import IST
from strike_desk.errors import ModelCallFailed, SpecialistTimeout
from strike_desk.regime_analyst import (
    STATUS_DEGRADED,
    STATUS_OK,
    STATUS_UNGROUNDED,
    SUBMIT_TOOL_NAME,
    RegimeAnalyst,
)
from strike_desk.specialists import SpecialistRequest
from tests.conftest import CannedTool, LocalToolSource, ScriptedModel, ai_message
from tests.market_fixtures import TREND_SNAPSHOT


def a_request(tick_id: str = "tick-1") -> SpecialistRequest:
    return SpecialistRequest(
        tick_id=tick_id,
        index_symbol="NIFTY",
        as_of=datetime.now(tz=UTC),
        book={"open_positions": [], "decisions_today": 2},
    )


def canned_tools(**overrides) -> list[CannedTool]:
    payloads = {**TREND_SNAPSHOT, **overrides}
    return [CannedTool(name=name, output=output) for name, output in payloads.items()]


def submit(label="trending", confidence=0.74, rationale=None, evidence=None) -> dict:
    return {
        "name": SUBMIT_TOOL_NAME,
        "args": {
            "label": label,
            "confidence": confidence,
            "rationale": rationale
            or "SMA20 at 24380.1 above SMA50 at 24105.6 with ADX at 31.7 confirms the move.",
            "evidence": evidence
            or [
                {"tool": "get_trend_snapshot", "field": "sma_20", "value": "24380.10"},
                {"tool": "get_trend_snapshot", "field": "adx_di", "value": "31.7"},
            ],
        },
    }


def build(settings, prompts, model, tools=None) -> tuple[RegimeAnalyst, LocalToolSource]:
    source = LocalToolSource(tools if tools is not None else canned_tools())
    return RegimeAnalyst(settings, prompts, source, model), source


def read_then_submit(**submission) -> list:
    """The normal two-round script: fetch something, then classify it.

    A submission with no successful tool call behind it degrades by design, so every
    script that expects a label must fetch first.
    """
    return [
        ai_message([{"name": "get_trend_snapshot", "args": {"symbol": "NIFTY"}}]),
        ai_message([submit(**submission)]),
    ]


def test_a_grounded_read_is_ok(settings, prompts, tracing):
    model = ScriptedModel(
        [
            ai_message([{"name": "get_trend_snapshot", "args": {"symbol": "NIFTY"}}]),
            ai_message([submit()], stop_reason="tool_use"),
        ]
    )
    analyst, source = build(settings, prompts, model)

    result = analyst.run(a_request())
    payload = result.payload

    assert payload["status"] == STATUS_OK
    assert payload["label"] == "trending"
    assert payload["confidence"] == 0.74
    assert payload["tool_call_count"] == 1
    assert payload["tool_error_count"] == 0
    assert payload["model_calls"] == 2
    assert len(payload["evidence"]) == 2
    assert result.model_version == settings.regime_model
    # 2 rounds x (900 in, 120 out) at $1/$5 per MTok
    assert result.token_cost_micros == 2 * (900 * 1 + 120 * 5)
    assert source.starts == 1


def test_the_prompt_artifact_is_stamped_on_the_payload(settings, prompts, tracing):
    model = ScriptedModel(read_then_submit())
    analyst, _ = build(settings, prompts, model)
    payload = analyst.run(a_request()).payload
    artifact = prompts.get("regime_analyst")
    assert payload["prompt_name"] == "regime_analyst"
    assert payload["prompt_version"] == artifact.version
    assert payload["prompt_digest"] == artifact.digest


def test_an_ungrounded_rationale_is_rejected(settings, prompts, tracing):
    model = ScriptedModel(
        [
            ai_message([{"name": "get_trend_snapshot", "args": {"symbol": "NIFTY"}}]),
            ai_message([submit(rationale="ADX at 44.9 and RSI at 81.2 confirm the trend.")]),
        ]
    )
    analyst, _ = build(settings, prompts, model)
    payload = analyst.run(a_request()).payload
    assert payload["status"] == STATUS_UNGROUNDED
    assert "44.9" in payload["defect"]


def test_no_successful_tool_call_degrades(settings, prompts, tracing):
    failing = [CannedTool(name=name, raises=True) for name in TREND_SNAPSHOT]
    model = ScriptedModel(
        [
            ai_message([{"name": "get_quote", "args": {"symbol": "NIFTY"}}]),
            ai_message([submit()]),
        ]
    )
    analyst, _ = build(settings, prompts, model, tools=failing)
    payload = analyst.run(a_request()).payload
    assert payload["status"] == STATUS_DEGRADED
    assert payload["label"] == "unknown"
    assert payload["tool_error_count"] == payload["tool_call_count"] == 1


def test_an_error_string_from_a_tool_counts_as_a_failure(settings, prompts, tracing):
    tools = [CannedTool(name=name, output="Error getting quote: broker session expired")
             for name in TREND_SNAPSHOT]
    model = ScriptedModel(
        [
            ai_message([{"name": "get_quote", "args": {"symbol": "NIFTY"}}]),
            ai_message([submit()]),
        ]
    )
    analyst, _ = build(settings, prompts, model, tools=tools)
    payload = analyst.run(a_request()).payload
    assert payload["status"] == STATUS_DEGRADED
    assert payload["tool_error_count"] == 1


def test_no_submission_within_the_round_budget_degrades(settings, prompts, tracing):
    settings = settings.model_copy(update={"regime_max_rounds": 2})
    model = ScriptedModel(
        [
            ai_message([{"name": "get_quote", "args": {"symbol": "NIFTY"}}]),
            ai_message(content="I think it is trending."),  # prose, no submission
        ]
    )
    analyst, _ = build(settings, prompts, model)
    payload = analyst.run(a_request()).payload
    assert payload["status"] == STATUS_DEGRADED
    assert "no submission" in payload["defect"]
    assert payload["model_calls"] == 2


def test_the_final_round_binds_only_the_submission_tool(settings, prompts, tracing):
    settings = settings.model_copy(update={"regime_max_rounds": 2})
    model = ScriptedModel(
        [
            ai_message([{"name": "get_quote", "args": {"symbol": "NIFTY"}}]),
            ai_message([submit()]),
        ]
    )
    analyst, _ = build(settings, prompts, model)
    analyst.run(a_request())

    first_names, first_choice = model.bindings[0]
    last_names, last_choice = model.bindings[-1]
    assert SUBMIT_TOOL_NAME in first_names and "get_quote" in first_names
    assert first_choice == "auto"
    assert last_names == [SUBMIT_TOOL_NAME]
    assert last_choice == {"type": "tool", "name": SUBMIT_TOOL_NAME}


def test_a_tool_outside_the_bound_set_is_refused(settings, prompts, tracing, journal):
    model = ScriptedModel(
        [
            ai_message([{"name": "place_order", "args": {"symbol": "NIFTY", "quantity": 75}}]),
            ai_message([{"name": "get_trend_snapshot", "args": {"symbol": "NIFTY"}}]),
            ai_message([submit()]),
        ]
    )
    analyst, _ = build(settings, prompts, model)
    payload = analyst.run(a_request()).payload

    assert payload["status"] == STATUS_OK  # the good round still counts
    rejected = [call for call in payload["calls"] if call["tool"] == "place_order"]
    assert rejected and rejected[0]["ok"] is False


def test_tool_output_is_truncated_at_the_cap(settings, prompts, tracing):
    settings = settings.model_copy(update={"regime_tool_output_chars": 50})
    tools = [CannedTool(name=name, output="x" * 500) for name in TREND_SNAPSHOT]
    model = ScriptedModel(
        [
            ai_message([{"name": "get_quote", "args": {"symbol": "NIFTY"}}]),
            ai_message([submit(rationale="Nothing numeric here at all.", evidence=[
                {"tool": "get_quote", "field": "ltp", "value": "unreadable"}])]),
        ]
    )
    analyst, _ = build(settings, prompts, model, tools=tools)
    payload = analyst.run(a_request()).payload
    call = payload["calls"][0]
    assert call["truncated"] is True
    assert call["chars"] <= 50 + len("\n…[truncated]")


def test_an_active_event_window_skips_the_model_entirely(settings, prompts, tracing):
    now = datetime.now(tz=IST)
    settings.events_path.write_text(
        json.dumps(
            [
                {
                    "name": "RBI policy",
                    "start": (now - timedelta(minutes=5)).isoformat(),
                    "end": (now + timedelta(minutes=55)).isoformat(),
                }
            ]
        ),
        encoding="utf-8",
    )
    model = ScriptedModel([])  # any call would raise
    analyst, source = build(settings, prompts, model)

    result = analyst.run(a_request())
    payload = result.payload
    assert (payload["status"], payload["label"], payload["confidence"]) == (STATUS_OK,
                                                                           "event-driven", 1.0)
    assert payload["evidence"][0]["tool"] == "event-calendar"
    assert payload["model_calls"] == 0
    assert result.model_version is None and result.token_cost_micros == 0
    assert model.rounds == 0 and source.starts == 0


def test_a_broken_calendar_degrades_rather_than_guessing(settings, prompts, tracing):
    settings.events_path.write_text("{oops", encoding="utf-8")
    analyst, _ = build(settings, prompts, ScriptedModel([]))
    payload = analyst.run(a_request()).payload
    assert payload["status"] == STATUS_DEGRADED
    assert payload["label"] == "unknown"


def test_the_analyst_enforces_its_own_deadline(settings, prompts, tracing):
    model = ScriptedModel([ai_message([submit()])], delay=2.0)
    analyst, _ = build(settings, prompts, model)
    with pytest.raises(SpecialistTimeout):
        analyst.run(a_request())


def test_a_provider_failure_raises_model_call_failed(settings, prompts, tracing):
    class Exploding(ScriptedModel):
        async def ainvoke(self, messages):
            raise RuntimeError("529 overloaded")

    analyst, _ = build(settings, prompts, Exploding([]))
    with pytest.raises(ModelCallFailed):
        analyst.run(a_request())


def test_every_step_of_the_read_is_traced(settings, prompts, tracing, journal):
    model = ScriptedModel(
        [
            ai_message([{"name": "get_trend_snapshot", "args": {"symbol": "NIFTY"}}]),
            ai_message([submit()]),
        ]
    )
    analyst, _ = build(settings, prompts, model)
    analyst.run(a_request())

    from sqlalchemy import select

    from strike_desk.journal import TraceSpan

    with journal.session_scope() as session:
        spans = list(session.execute(select(TraceSpan)).scalars())
    names = [span.name for span in spans]
    assert names.count("regime.read") == 1
    assert names.count("regime.model_call") == 2
    assert names.count("regime.tool_call") == 1

    tool_span = next(span for span in spans if span.name == "regime.tool_call")
    attributes = json.loads(tool_span.attributes_json)
    assert attributes["tool.name"] == "get_trend_snapshot"
    assert attributes["tool.ok"] is True
    assert "adx_di" in attributes["tool.output"]  # captured for replay

    model_span = next(span for span in spans if span.name == "regime.model_call")
    assert json.loads(model_span.attributes_json)["model.input_tokens"] == 900
```

### `strike_desk/tests/test_tick_regime.py`

The integration layer: a real analyst, behind the real registry, inside the real graph, writing real rows.

```python
"""The tick with a registered Regime Analyst."""

from __future__ import annotations

import pytest

from strike_desk.errors import JournalWriteError, McpUnavailable
from strike_desk.regime_analyst import RegimeAnalyst
from tests.conftest import CannedTool, LocalToolSource, ScriptedModel, ai_message
from tests.market_fixtures import TREND_SNAPSHOT
from tests.test_regime_analyst import read_then_submit, submit


def register_analyst(deps, answers, tools=None) -> LocalToolSource:
    source = LocalToolSource(
        tools
        if tools is not None
        else [CannedTool(name=name, output=output) for name, output in TREND_SNAPSHOT.items()]
    )
    deps.registry.register(
        RegimeAnalyst(deps.settings, deps.prompts, source, ScriptedModel(answers))
    )
    return source


def only_read(journal, today):
    rows = journal.list_regime_reads(today)
    assert len(rows) == 1
    return rows[0]


def test_a_confident_tradeable_read_still_declines_for_the_missing_strategist(
    runner, deps, journal, today
):
    register_analyst(deps, read_then_submit(label="trending", confidence=0.81))
    runner.run_tick("schedule")

    decision = journal.list_decisions(today)[0]
    assert (decision.outcome, decision.reason_code) == ("decline", "specialist-unavailable")
    assert "strategist" in decision.reason_text
    assert "Analyst:" in decision.reason_text
    assert decision.regime_label == "trending"
    assert decision.regime_confidence == pytest.approx(0.81)
    assert decision.model_version == deps.settings.regime_model
    assert decision.token_cost_micros > 0

    read = only_read(journal, today)
    assert read.tick_id == decision.tick_id
    assert read.trace_id == decision.trace_id
    assert read.status == "ok"
    assert read.source == "tick"
    assert read.token_cost_micros == decision.token_cost_micros
    assert read.prompt_set_version == decision.prompt_set_version


@pytest.mark.parametrize(
    ("label", "confidence", "expected"),
    [
        ("high-volatility", 0.90, "regime-not-tradeable"),
        ("unknown", 0.20, "regime-not-tradeable"),
        ("event-driven", 0.99, "regime-not-tradeable"),
        ("range-bound", 0.30, "regime-low-confidence"),
    ],
)
def test_labels_are_scored_by_the_decision_table(
    runner, deps, journal, today, label, confidence, expected
):
    register_analyst(deps, read_then_submit(label=label, confidence=confidence))
    runner.run_tick("schedule")
    decision = journal.list_decisions(today)[0]
    assert (decision.outcome, decision.reason_code) == ("decline", expected)
    assert only_read(journal, today).label == label


def test_an_ungrounded_read_declines_with_its_own_reason_code(runner, deps, journal, today):
    register_analyst(
        deps,
        [
            ai_message([{"name": "get_quote", "args": {"symbol": "NIFTY"}}]),
            ai_message([submit(rationale="RSI printed 91.4, an exhausted trend.")]),
        ],
    )
    runner.run_tick("schedule")
    decision = journal.list_decisions(today)[0]
    assert (decision.outcome, decision.reason_code) == ("decline", "regime-ungrounded")
    read = only_read(journal, today)
    assert read.status == "ungrounded"
    assert decision.regime_label is None  # a rejected read never labels the decision
    assert read.label == "trending"  # but the record keeps what was submitted


def test_a_degraded_read_declines_on_data_quality(runner, deps, journal, today):
    register_analyst(
        deps,
        [
            ai_message([{"name": "get_quote", "args": {"symbol": "NIFTY"}}]),
            ai_message([submit()]),
        ],
        tools=[CannedTool(name=name, raises=True) for name in TREND_SNAPSHOT],
    )
    runner.run_tick("schedule")
    decision = journal.list_decisions(today)[0]
    assert (decision.outcome, decision.reason_code) == ("decline", "data-quality")
    assert only_read(journal, today).status == "degraded"


def test_an_unavailable_toolbox_declines_as_specialist_unavailable(runner, deps, journal, today):
    source = register_analyst(deps, [])
    source.fail_to_start = McpUnavailable("no MCP interpreter at /nowhere")
    runner.run_tick("schedule")
    decision = journal.list_decisions(today)[0]
    assert (decision.outcome, decision.reason_code) == ("decline", "specialist-unavailable")
    assert journal.list_regime_reads(today) == []


def test_a_failing_read_write_fails_the_tick_closed(runner, deps, journal, today, monkeypatch):
    register_analyst(deps, read_then_submit())

    def explode(**_fields):
        raise JournalWriteError("disk is read-only")

    monkeypatch.setattr(deps.journal, "record_regime_read", explode)
    with pytest.raises(JournalWriteError):
        runner.run_tick("schedule")
    assert journal.count_decisions(today) == 0


def test_day_cost_aggregates_across_reads(runner, deps, journal, today):
    register_analyst(deps, read_then_submit())
    runner.run_tick("schedule")
    assert journal.token_cost_micros(today) == journal.list_decisions(today)[0].token_cost_micros
```

## 5. Guardrail tests

### `strike_desk/tests/test_guardrails_regime.py`

Iteration 01's exhaustive decision-table sweep proved `enter` was unreachable across every state it could then produce. The state space is bigger now — two new keys and one new error kind — so the sweep is re-run over the wider space, alongside the tool-scoping and secret checks the reasoning plane introduces.

```python
"""Guardrails for the reasoning plane: no entry, no order tool, no secrets."""

from __future__ import annotations

import itertools
import json

from strike_desk.graph import OUTCOME_DECLINE, OUTCOME_HOLD, _decide_outcome
from strike_desk.mcp_toolbox import REGIME_TOOLS
from strike_desk.regime_analyst import RegimeAnalyst
from tests.conftest import API_KEY, MODEL_KEY, CannedTool, LocalToolSource, ScriptedModel
from tests.market_fixtures import TREND_SNAPSHOT
from tests.test_regime_analyst import a_request, read_then_submit
from tests.test_tick_regime import register_analyst

FORBIDDEN = (
    "place_order",
    "place_smart_order",
    "place_options_order",
    "modify_order",
    "cancel_order",
    "cancel_all_orders",
    "close_all_positions",
    "send_telegram_alert",
    "analyzer_toggle",
)


def test_no_order_or_alert_tool_is_in_the_whitelist():
    assert not set(REGIME_TOOLS) & set(FORBIDDEN)


def test_no_state_including_the_new_ones_can_produce_an_entry(settings):
    labels = ["trending", "range-bound", "event-driven", "high-volatility", "unknown", "nonsense"]
    errors = [
        None,
        {"role": "regime", "kind": "timeout", "detail": "x"},
        {"role": "regime", "kind": "unavailable", "detail": "x"},
        {"role": "regime", "kind": "ungrounded", "detail": "cited 44.9"},
    ]
    books = [None, {"open_positions": []}, {"open_positions": [{"symbol": "NIFTY...CE"}]}]
    for label, book, error, confidence, data_error, budget in itertools.product(
        labels, books, errors, [0.0, 0.54, 0.55, 1.0], [None, "every tool call failed"],
        [False, True]
    ):
        state = {
            "budget_exceeded": budget,
            "book": book,
            "specialist_error": error,
            "regime_label": label,
            "regime_confidence": confidence,
            "regime_data_error": data_error,
            "regime_rationale": "ADX at 31.7.",
        }
        outcome, reason_code, reason_text = _decide_outcome(state, settings)
        assert outcome in {OUTCOME_DECLINE, OUTCOME_HOLD}, (state, outcome)
        assert reason_code and reason_text


def test_the_agent_is_never_handed_a_tool_it_could_trade_with(settings, prompts, tracing):
    model = ScriptedModel(read_then_submit())
    source = LocalToolSource(
        [CannedTool(name=name, output=output) for name, output in TREND_SNAPSHOT.items()]
    )
    RegimeAnalyst(settings, prompts, source, model).run(a_request())
    for names, _choice in model.bindings:
        assert not set(names) & set(FORBIDDEN)
        assert set(names) <= set(REGIME_TOOLS) | {"submit_regime_read"}


def test_neither_key_reaches_the_journal_or_the_traces(runner, deps, journal, today):
    register_analyst(deps, read_then_submit())
    runner.run_tick("schedule")

    from sqlalchemy import select

    from strike_desk.journal import Decision, RegimeRead, TraceSpan

    with journal.session_scope() as session:
        blob = json.dumps(
            [
                [row.book_state_json, row.reason_text]
                for row in session.execute(select(Decision)).scalars()
            ]
            + [
                [row.rationale, row.evidence_json, row.defect or ""]
                for row in session.execute(select(RegimeRead)).scalars()
            ]
            + [span.attributes_json for span in session.execute(select(TraceSpan)).scalars()]
        )
    assert API_KEY not in blob
    assert MODEL_KEY not in blob
```

## 6. The evaluation gate

A regression here is not a crash — it is a prompt that starts calling a market range-bound when it is trending, or one that quietly begins citing numbers it never fetched. Only a live model can catch that, so the eval suite replays frozen market snapshots through the real analyst and scores the answers against three pass-bars: **label agreement of at least 80%** across the case set, **zero ungrounded submissions**, and **zero calls to anything outside the whitelist**. A fourth case, run three times, checks label stability — the honest form of the catalog's "same snapshot, same label".

The snapshots are canned tool outputs, so no OpenAlgo instance and no market session is needed; only the model is real. Create `strike_desk/tests/evals/__init__.py` as an empty file first, exactly as `tests/regression/` already does, so the package imports cleanly.

### `strike_desk/tests/evals/regime_cases.json`

```json
[
  {
    "id": "strong-uptrend",
    "expect": ["trending"],
    "tools": {
      "get_quote": "{\"status\":\"success\",\"data\":{\"ltp\":24512.35,\"open\":24310.00,\"high\":24528.90,\"low\":24298.40,\"prev_close\":24280.15,\"volume\":0,\"oi\":0}}",
      "get_trend_snapshot": "{\"symbol\":\"NIFTY\",\"interval\":\"15m\",\"bars_loaded\":252,\"last_close\":24512.35,\"indicators\":{\"sma_20\":24380.10,\"sma_50\":24105.60,\"sma_200\":23540.20,\"ema_20\":24448.70,\"supertrend\":[24361.05,1],\"adx_di\":[31.20,12.40,34.60]},\"legend\":{\"adx_di\":\"[+DI, -DI, ADX]\"}}",
      "get_momentum_snapshot": "{\"symbol\":\"NIFTY\",\"interval\":\"15m\",\"indicators\":{\"rsi_14\":63.80,\"macd\":[42.10,28.60,13.50],\"stochastic\":[78.40,71.20],\"cci_20\":118.30}}",
      "get_volatility_snapshot": "{\"symbol\":\"NIFTY\",\"interval\":\"15m\",\"indicators\":{\"atr_14\":74.20,\"natr_14\":0.303,\"bb_width\":1.84,\"historical_volatility\":10.90}}",
      "get_expiry_dates": "{\"status\":\"success\",\"data\":[\"26AUG26\",\"24SEP26\"]}",
      "get_historical_data": "{\"count\":60,\"returned\":6,\"data\":[{\"close\":24310.0},{\"close\":24358.4},{\"close\":24401.7},{\"close\":24447.2},{\"close\":24489.9},{\"close\":24512.35}]}"
    }
  },
  {
    "id": "tight-range",
    "expect": ["range-bound"],
    "tools": {
      "get_quote": "{\"status\":\"success\",\"data\":{\"ltp\":24188.65,\"open\":24180.20,\"high\":24212.40,\"low\":24164.80,\"prev_close\":24191.05,\"volume\":0,\"oi\":0}}",
      "get_trend_snapshot": "{\"symbol\":\"NIFTY\",\"interval\":\"15m\",\"bars_loaded\":252,\"last_close\":24188.65,\"indicators\":{\"sma_20\":24186.40,\"sma_50\":24190.85,\"sma_200\":24150.10,\"ema_20\":24187.90,\"supertrend\":[24176.30,1],\"adx_di\":[14.20,13.80,11.40]},\"legend\":{\"adx_di\":\"[+DI, -DI, ADX]\"}}",
      "get_momentum_snapshot": "{\"symbol\":\"NIFTY\",\"interval\":\"15m\",\"indicators\":{\"rsi_14\":50.60,\"macd\":[1.20,1.40,-0.20],\"stochastic\":[48.90,51.30],\"cci_20\":-8.40}}",
      "get_volatility_snapshot": "{\"symbol\":\"NIFTY\",\"interval\":\"15m\",\"indicators\":{\"atr_14\":28.40,\"natr_14\":0.117,\"bb_width\":0.42,\"historical_volatility\":6.20}}",
      "get_expiry_dates": "{\"status\":\"success\",\"data\":[\"26AUG26\",\"24SEP26\"]}",
      "get_historical_data": "{\"count\":60,\"returned\":6,\"data\":[{\"close\":24191.0},{\"close\":24178.4},{\"close\":24196.7},{\"close\":24183.2},{\"close\":24199.9},{\"close\":24188.65}]}"
    }
  },
  {
    "id": "volatility-spike",
    "expect": ["high-volatility"],
    "tools": {
      "get_quote": "{\"status\":\"success\",\"data\":{\"ltp\":23640.10,\"open\":24120.55,\"high\":24160.30,\"low\":23570.80,\"prev_close\":24118.40,\"volume\":0,\"oi\":0}}",
      "get_trend_snapshot": "{\"symbol\":\"NIFTY\",\"interval\":\"15m\",\"bars_loaded\":252,\"last_close\":23640.10,\"indicators\":{\"sma_20\":23980.40,\"sma_50\":24090.15,\"sma_200\":23860.70,\"ema_20\":23870.20,\"supertrend\":[24010.60,-1],\"adx_di\":[9.80,38.40,29.10]},\"legend\":{\"adx_di\":\"[+DI, -DI, ADX]\"}}",
      "get_momentum_snapshot": "{\"symbol\":\"NIFTY\",\"interval\":\"15m\",\"indicators\":{\"rsi_14\":24.30,\"macd\":[-86.40,-31.20,-55.20],\"stochastic\":[8.10,14.60],\"cci_20\":-212.70}}",
      "get_volatility_snapshot": "{\"symbol\":\"NIFTY\",\"interval\":\"15m\",\"indicators\":{\"atr_14\":268.40,\"natr_14\":1.135,\"bb_width\":6.90,\"historical_volatility\":31.80}}",
      "get_expiry_dates": "{\"status\":\"success\",\"data\":[\"26AUG26\",\"24SEP26\"]}",
      "get_historical_data": "{\"count\":60,\"returned\":6,\"data\":[{\"close\":24118.4},{\"close\":23980.2},{\"close\":24060.9},{\"close\":23740.5},{\"close\":23820.7},{\"close\":23640.1}]}"
    }
  },
  {
    "id": "thin-data",
    "expect": ["unknown", "range-bound"],
    "tools": {
      "get_quote": "{\"status\":\"success\",\"data\":{\"ltp\":24010.00,\"open\":0,\"high\":0,\"low\":0,\"prev_close\":0,\"volume\":0,\"oi\":0}}",
      "get_trend_snapshot": "{\"symbol\":\"NIFTY\",\"interval\":\"15m\",\"bars_loaded\":3,\"last_close\":24010.00,\"indicators\":{\"sma_20\":null,\"sma_50\":null,\"sma_200\":null,\"ema_20\":null,\"supertrend\":[null,0],\"adx_di\":[null,null,null]},\"legend\":{\"adx_di\":\"[+DI, -DI, ADX]\"}}",
      "get_momentum_snapshot": "{\"symbol\":\"NIFTY\",\"interval\":\"15m\",\"indicators\":{\"rsi_14\":null,\"macd\":[null,null,null],\"stochastic\":[null,null],\"cci_20\":null}}",
      "get_volatility_snapshot": "{\"symbol\":\"NIFTY\",\"interval\":\"15m\",\"indicators\":{\"atr_14\":null,\"natr_14\":null,\"bb_width\":null,\"historical_volatility\":null}}",
      "get_expiry_dates": "{\"status\":\"success\",\"data\":[\"26AUG26\"]}",
      "get_historical_data": "{\"count\":3,\"returned\":3,\"data\":[{\"close\":24008.0},{\"close\":24011.5},{\"close\":24010.0}]}"
    }
  },
  {
    "id": "mixed-signals",
    "expect": ["range-bound", "unknown", "high-volatility"],
    "tools": {
      "get_quote": "{\"status\":\"success\",\"data\":{\"ltp\":24305.75,\"open\":24290.10,\"high\":24418.60,\"low\":24201.30,\"prev_close\":24298.85,\"volume\":0,\"oi\":0}}",
      "get_trend_snapshot": "{\"symbol\":\"NIFTY\",\"interval\":\"15m\",\"bars_loaded\":252,\"last_close\":24305.75,\"indicators\":{\"sma_20\":24312.60,\"sma_50\":24280.40,\"sma_200\":24010.90,\"ema_20\":24308.10,\"supertrend\":[24290.50,1],\"adx_di\":[19.60,21.10,17.80]},\"legend\":{\"adx_di\":\"[+DI, -DI, ADX]\"}}",
      "get_momentum_snapshot": "{\"symbol\":\"NIFTY\",\"interval\":\"15m\",\"indicators\":{\"rsi_14\":54.20,\"macd\":[6.40,9.10,-2.70],\"stochastic\":[62.80,44.30],\"cci_20\":31.60}}",
      "get_volatility_snapshot": "{\"symbol\":\"NIFTY\",\"interval\":\"15m\",\"indicators\":{\"atr_14\":112.30,\"natr_14\":0.462,\"bb_width\":2.60,\"historical_volatility\":17.40}}",
      "get_expiry_dates": "{\"status\":\"success\",\"data\":[\"26AUG26\",\"24SEP26\"]}",
      "get_historical_data": "{\"count\":60,\"returned\":6,\"data\":[{\"close\":24298.9},{\"close\":24380.2},{\"close\":24240.6},{\"close\":24360.4},{\"close\":24250.1},{\"close\":24305.75}]}"
    }
  }
]
```

### `strike_desk/tests/evals/test_regime_evals.py`

```python
"""LLMOps: the shipped prompt, a live model, and frozen snapshots."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from strike_desk.config import Settings
from strike_desk.mcp_toolbox import REGIME_TOOLS
from strike_desk.model_client import build_regime_model
from strike_desk.prompt_registry import PromptRegistry
from strike_desk.regime_analyst import STATUS_OK, STATUS_UNGROUNDED, RegimeAnalyst
from tests.conftest import PACKAGED_PROMPTS, CannedTool, LocalToolSource
from tests.test_regime_analyst import a_request

pytestmark = [
    pytest.mark.evals,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="live regime evals need ANTHROPIC_API_KEY",
    ),
]

CASES = json.loads((Path(__file__).parent / "regime_cases.json").read_text(encoding="utf-8"))
LABEL_AGREEMENT_FLOOR = 0.80


@pytest.fixture(scope="module")
def live_settings(tmp_path_factory):
    """The shipped configuration, with the real key and a realistic budget."""
    return Settings(
        openalgo_api_key="unused-by-the-eval-suite",
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        state_dir=tmp_path_factory.mktemp("evals"),
        prompts_dir=PACKAGED_PROMPTS,
        specialist_timeout_seconds=40.0,
        regime_deadline_margin_seconds=3.0,
    )


@pytest.fixture(scope="module")
def live_prompts(live_settings) -> PromptRegistry:
    return PromptRegistry.load(live_settings.prompts_dir)


def read_case(case, settings, prompts):
    tools = [CannedTool(name=name, output=output) for name, output in case["tools"].items()]
    analyst = RegimeAnalyst(settings, prompts, LocalToolSource(tools), build_regime_model(settings))
    return analyst.run(a_request(tick_id=f"eval:{case['id']}")).payload


@pytest.fixture(scope="module")
def outcomes(live_settings, live_prompts):
    """Run every case once, and share the answers across the assertions below."""
    return {
        case["id"]: (case, read_case(case, live_settings, live_prompts)) for case in CASES
    }


def test_label_agreement_clears_the_floor(outcomes):
    agreed = [
        case_id
        for case_id, (case, payload) in outcomes.items()
        if payload.get("label") in case["expect"]
    ]
    ratio = len(agreed) / len(outcomes)
    disagreed = {
        case_id: (payload.get("label"), case["expect"])
        for case_id, (case, payload) in outcomes.items()
        if payload.get("label") not in case["expect"]
    }
    assert ratio >= LABEL_AGREEMENT_FLOOR, f"agreement {ratio:.0%}; misses: {disagreed}"


def test_no_case_produces_an_ungrounded_submission(outcomes):
    offenders = {
        case_id: payload["defect"]
        for case_id, (_case, payload) in outcomes.items()
        if payload["status"] == STATUS_UNGROUNDED
    }
    assert not offenders, offenders


def test_every_answer_is_shaped_correctly(outcomes):
    for case_id, (_case, payload) in outcomes.items():
        assert payload["status"] in {STATUS_OK, "degraded"}, case_id
        if payload["status"] == STATUS_OK:
            assert 0.0 <= payload["confidence"] <= 1.0, case_id
            assert payload["evidence"], case_id
            assert len(payload["rationale"]) <= 320, case_id


def test_only_whitelisted_tools_are_ever_called(outcomes):
    called = {
        call["tool"] for _case, payload in outcomes.values() for call in payload["calls"]
    }
    assert called <= set(REGIME_TOOLS), called - set(REGIME_TOOLS)


def test_the_same_snapshot_yields_a_stable_label(live_settings, live_prompts):
    case = next(item for item in CASES if item["id"] == "strong-uptrend")
    labels = {read_case(case, live_settings, live_prompts).get("label") for _ in range(3)}
    assert len(labels) == 1, f"unstable labels across three replays: {labels}"


def test_a_read_stays_inside_its_cost_envelope(outcomes):
    """A prompt that starts fetching everything shows up here before it shows up on the bill."""
    for case_id, (_case, payload) in outcomes.items():
        assert payload["model_calls"] <= 4, case_id
        assert payload["input_tokens"] + payload["output_tokens"] < 60_000, case_id
```

Run them deliberately, from `strike_desk/`:

```bash
ANTHROPIC_API_KEY=sk-ant-... uv run pytest tests/evals -m evals -q
```

A full pass is eight live reads — roughly two cents at Haiku prices — and it is the only thing standing between a well-meaning prompt edit and a desk that classifies badly all day.

## 7. Running it

```bash
uv sync --group dev
uv run ruff check .
uv run pytest tests/test_guardrails.py tests/test_guardrails_regime.py tests/regression -q   # gates first
uv run pytest --cov=strike_desk --cov-report=term-missing                                     # everything
```

The coverage floor stays at 80%, and `grounding.py`, `events.py`, `regime_analyst.py` and `graph.py` should each sit above 90%. `mcp_toolbox.py` joins `service.py` and `__main__.py` in the coverage `omit` list, because its `_serve` coroutine and loop-thread lifecycle are proven by the manual pass against a real server rather than by a double — measuring a module you deliberately do not unit-test would make the gate a number nobody trusts. Coverage is a smoke alarm; the guardrail files and the eval gate are what protect the slice.

### `.github/workflows/strike-desk-ci.yml`

The `verify` job is unchanged apart from the new guardrail file. The `evals` job is new, and it is scoped: it runs only when a prompt, the analyst, the grounding rules or the model configuration changes, because those are the only edits that can move a score.

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
        run: uv run pytest tests/test_guardrails.py tests/test_guardrails_regime.py tests/regression -q

      - name: Full suite with coverage
        run: uv run pytest --cov=strike_desk --cov-report=term-missing --cov-fail-under=80

      - name: Security scan
        run: |
          uv run bandit -q -r src
          uv run pip-audit

  evals:
    needs: verify
    runs-on: ubuntu-latest
    if: github.event_name == 'pull_request'
    defaults:
      run:
        working-directory: strike_desk
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Decide whether the reasoning plane changed
        id: changed
        run: |
          git diff --name-only origin/${{ github.base_ref }}...HEAD > /tmp/changed.txt
          if grep -qE 'strike_desk/src/strike_desk/(prompts/|regime_analyst|grounding|model_client)' /tmp/changed.txt \
             || grep -q 'strike_desk/tests/evals/' /tmp/changed.txt; then
            echo "run=true" >> "$GITHUB_OUTPUT"
          else
            echo "run=false" >> "$GITHUB_OUTPUT"
          fi

      - uses: astral-sh/setup-uv@v5
        if: steps.changed.outputs.run == 'true'
        with:
          enable-cache: true

      - name: Install and run the regime evals
        if: steps.changed.outputs.run == 'true'
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          uv python install 3.12
          uv sync --group dev
          uv run pytest tests/evals -m evals -q
```

A prompt change with no `ANTHROPIC_API_KEY` secret configured skips the eval tests rather than failing them, which is the one place this suite is permissive — and it is why `03_manual_test_cases.md` MT-14 exists as the human backstop.

## 8. Traceability

| Automated test | Acceptance criterion | Manual case it backstops |
| --- | --- | --- |
| `test_regime_analyst.py::test_a_grounded_read_is_ok` | AC-1, AC-10 | MT-01 |
| `test_regime_analyst.py::test_the_prompt_artifact_is_stamped_on_the_payload` | AC-10 | MT-12 |
| `test_grounding.py::test_grounded_rationale_passes` | AC-2 | MT-02 |
| `test_grounding.py::test_rounding_at_the_cited_precision_passes` | AC-2 | MT-02 |
| `test_grounding.py::test_invented_number_fails` | AC-2 | MT-02 |
| `test_grounding.py::test_number_from_a_failed_call_is_not_grounded` | AC-2 | MT-02 |
| `test_grounding.py::test_evidence_from_an_uncalled_tool_fails` | AC-2 | MT-02 |
| `test_grounding.py::test_labels_outside_the_fixed_set_are_rejected_by_the_schema` | AC-1 | MT-01 |
| `test_regime_analyst.py::test_an_ungrounded_rationale_is_rejected` | AC-2 | MT-02 |
| `test_mcp_toolbox.py::test_whitelist_is_exactly_six_read_only_market_tools` | AC-3 | MT-03 |
| `test_mcp_toolbox.py::test_selection_keeps_the_whitelist_and_drops_everything_else` | AC-3 | MT-03 |
| `test_mcp_toolbox.py::test_a_missing_tool_fails_the_session_closed` | AC-3 | MT-04 |
| `test_regime_analyst.py::test_a_tool_outside_the_bound_set_is_refused` | AC-3 | MT-03 |
| `test_guardrails_regime.py::test_no_order_or_alert_tool_is_in_the_whitelist` | AC-3 | MT-03 |
| `test_guardrails_regime.py::test_the_agent_is_never_handed_a_tool_it_could_trade_with` | AC-3 | MT-03 |
| `test_tick_regime.py::test_a_confident_tradeable_read_still_declines_for_the_missing_strategist` | AC-4, AC-5, AC-10 | MT-06 |
| `test_tick_regime.py::test_labels_are_scored_by_the_decision_table` | AC-5 | MT-06, MT-07 |
| `test_tick_regime.py::test_an_ungrounded_read_declines_with_its_own_reason_code` | AC-2, AC-4 | MT-02 |
| `test_tick_regime.py::test_a_degraded_read_declines_on_data_quality` | AC-6 | MT-08 |
| `test_tick_regime.py::test_an_unavailable_toolbox_declines_as_specialist_unavailable` | AC-6 | MT-16 |
| `test_tick_regime.py::test_a_failing_read_write_fails_the_tick_closed` | AC-4 | MT-05 |
| `test_tick_regime.py::test_day_cost_aggregates_across_reads` | AC-10 | MT-12 |
| `test_regime_analyst.py::test_no_successful_tool_call_degrades` | AC-6 | MT-08 |
| `test_regime_analyst.py::test_an_error_string_from_a_tool_counts_as_a_failure` | AC-6 | MT-08 |
| `test_regime_analyst.py::test_no_submission_within_the_round_budget_degrades` | AC-6, AC-13 | MT-15 |
| `test_events.py::*` | AC-7 | MT-09 |
| `test_regime_analyst.py::test_an_active_event_window_skips_the_model_entirely` | AC-7 | MT-09 |
| `test_regime_analyst.py::test_a_broken_calendar_degrades_rather_than_guessing` | AC-7 | MT-09 |
| `test_regime_analyst.py::test_the_analyst_enforces_its_own_deadline` | AC-8 | MT-10 |
| `test_regime_analyst.py::test_a_provider_failure_raises_model_call_failed` | AC-6 | MT-16 |
| `test_regime_analyst.py::test_every_step_of_the_read_is_traced` | AC-9 | MT-11 |
| `test_guardrails_regime.py::test_neither_key_reaches_the_journal_or_the_traces` | AC-11 | MT-13 |
| `test_guardrails_regime.py::test_no_state_including_the_new_ones_can_produce_an_entry` | AC-5 | MT-06 |
| `test_regime_analyst.py::test_the_final_round_binds_only_the_submission_tool` | AC-13 | MT-15 |
| `test_regime_analyst.py::test_tool_output_is_truncated_at_the_cap` | AC-13 | MT-15 |
| `evals/test_regime_evals.py::test_label_agreement_clears_the_floor` | AC-12 | MT-14 |
| `evals/test_regime_evals.py::test_no_case_produces_an_ungrounded_submission` | AC-2, AC-12 | MT-02 |
| `evals/test_regime_evals.py::test_every_answer_is_shaped_correctly` | AC-1, AC-12 | MT-01 |
| `evals/test_regime_evals.py::test_only_whitelisted_tools_are_ever_called` | AC-3, AC-12 | MT-03 |
| `evals/test_regime_evals.py::test_the_same_snapshot_yields_a_stable_label` | AC-12 | MT-14 |
| `evals/test_regime_evals.py::test_a_read_stays_inside_its_cost_envelope` | AC-13 | MT-15 |

Two things stay human. Proving that a *real* MCP subprocess starts, loads OpenAlgo's tools and survives being killed is MT-01, MT-04 and MT-16's job, because a double cannot fail the way a subprocess does. And the visibility of the OpenAlgo key in the MCP server's command line — the known limitation — is confirmed by eye in MT-13 rather than asserted in code, because asserting it would be codifying the thing you want to keep watching.
