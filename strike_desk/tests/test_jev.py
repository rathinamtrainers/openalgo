from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx
from pydantic import SecretStr

from strike_desk.errors import ModelCallFailed
from strike_desk.grounding import EvidenceLedger, ToolObservation
from strike_desk.jev_client import decide
from strike_desk.jev_regime import _grounded_sentence, _questions, _trend


def test_trend_parse() -> None:
    raw = json.dumps({"indicators": {"adx_di": [16.59, 28.30, 31.36]}})
    assert _trend(raw) == (16.59, 28.30, 31.36)


def test_grounded_sentence_starts_with_side() -> None:
    text, evidence = _grounded_sentence(
        "bearish", (16.59, 28.30, 31.36), (30.05, 18.2, 22.1), 11.46
    )
    assert text.startswith("bearish")
    assert "16.59" in text and "28.3" in text
    assert evidence[0].tool == "get_trend_snapshot"


@respx.mock
def test_decide_reads_answers(settings) -> None:
    settings = settings.model_copy(
        update={"jev_api_key": SecretStr("sk-or-test"), "regime_provider": "jev"}
    )
    route = respx.post("https://openrouter.ai/api/alpha/decisions").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "typesafe/jev-1.13",
                "answers": {
                    "label": {"type": "choice", "choice": "trending", "confidence": 0.8},
                    "side": {"type": "choice", "choice": "bearish", "confidence": 0.9},
                },
                "usage": {"input_tokens": 200, "output_tokens": 10},
            },
        )
    )
    body = decide(settings, {"daily_plus_di": 16}, _questions())
    assert route.called
    assert body["answers"]["label"]["choice"] == "trending"


@respx.mock
def test_decide_http_error_is_model_failure(settings) -> None:
    settings = settings.model_copy(update={"jev_api_key": SecretStr("sk-or-test")})
    respx.post("https://openrouter.ai/api/alpha/decisions").mock(
        return_value=httpx.Response(401, json={"error": "no"})
    )
    with pytest.raises(ModelCallFailed, match="Jev HTTP 401"):
        decide(settings, {"x": 1}, _questions())


def test_finish_trending_bearish(settings) -> None:
    from strike_desk.jev_regime import JevRegimeAnalyst
    from strike_desk.prompt_registry import PromptRegistry

    prompts = PromptRegistry.load(settings.prompts_dir)
    analyst = JevRegimeAnalyst(settings, prompts, tool_source=None)  # type: ignore[arg-type]
    ledger = EvidenceLedger()
    ledger.record_playbook("daily-weight")
    raw = json.dumps({"indicators": {"adx_di": [16.59, 28.30, 31.36]}})
    ledger.record(
        ToolObservation(
            call_id="1",
            tool="get_trend_snapshot",
            args={"interval": "D"},
            output=raw,
            ok=True,
            latency_ms=1,
            truncated=False,
        )
    )
    fetched = {
        "state": {},
        "daily": (16.59, 28.30, 31.36),
        "ten": (30.05, 18.2, 22.1),
        "vix": 11.46,
    }
    # Put 10m and VIX on the ledger so grounding can see those numbers.
    ledger.record(
        ToolObservation(
            call_id="2",
            tool="get_trend_snapshot",
            args={"interval": "10m"},
            output=json.dumps({"indicators": {"adx_di": [30.05, 18.2, 22.1]}}),
            ok=True,
            latency_ms=1,
            truncated=False,
        )
    )
    ledger.record(
        ToolObservation(
            call_id="3",
            tool="get_quote",
            args={"symbol": "INDIAVIX"},
            output=json.dumps({"data": {"ltp": 11.46}}),
            ok=True,
            latency_ms=1,
            truncated=False,
        )
    )
    outcome = analyst._finish(
        {
            "label": {"choice": "trending", "confidence": 0.8},
            "side": {"choice": "bearish"},
        },
        fetched,
        ledger,
        {
            "calls": [],
            "tool_calls": 3,
            "tool_errors": 0,
            "model_calls": 1,
            "input_tokens": 10,
            "output_tokens": 2,
        },
    )
    assert outcome.status == "ok"
    assert outcome.label == "trending"
    assert outcome.rationale.startswith("bearish")
    assert outcome.confidence >= 0.55


def test_session_closed_reconnects_then_reads(settings) -> None:
    from strike_desk.jev_regime import JevRegimeAnalyst, _SessionClosed
    from strike_desk.prompt_registry import PromptRegistry
    from strike_desk.specialists import SpecialistRequest

    class OnceBroken:
        def __init__(self) -> None:
            self.starts = 0
            self.broken = 0
            self.calls = 0

        def ensure_started(self) -> None:
            self.starts += 1

        def mark_broken(self) -> None:
            self.broken += 1

        def tools(self, role: str = "") -> list:
            return []

        def submit(self, factory, timeout: float):
            self.calls += 1
            if self.calls == 1:
                raise _SessionClosed("MCP session closed during a tool call")
            return type("O", (), {"status": "ok", "label": "trending"})()

    prompts = PromptRegistry.load(settings.prompts_dir)
    analyst = JevRegimeAnalyst(settings, prompts, OnceBroken())  # type: ignore[arg-type]
    result = analyst._read(
        SpecialistRequest(tick_id="t", index_symbol="NIFTY", as_of=datetime.now(tz=UTC), book={})
    )
    assert result.status == "ok"
    assert result.label == "trending"


def test_trend_parses_wrapped_pretty_snapshot() -> None:
    inner = json.dumps({"indicators": {"adx_di": [16.59, 28.30, 31.36]}}, indent=2)
    raw = str([{"type": "text", "text": inner, "id": "lc_test"}])
    assert _trend(raw) == (16.59, 28.30, 31.36)


def test_trend_does_not_treat_wrapped_history_error_as_numbers() -> None:
    raw = str(
        [
            {
                "type": "text",
                "text": "Error getting trend snapshot: history error: HTTP 500",
                "id": "lc_test",
            }
        ]
    )
    assert _trend(raw) == (None, None, None)


def test_grounded_sentence_is_never_shorter_than_the_schema() -> None:
    text, _evidence = _grounded_sentence("bullish", (None, None, None), (None, None, None), None)
    assert len(text) >= 10
    assert text.startswith("bullish")


def test_finish_missing_dis_is_unknown_not_ungrounded(settings) -> None:
    from strike_desk.jev_regime import JevRegimeAnalyst
    from strike_desk.prompt_registry import PromptRegistry

    prompts = PromptRegistry.load(settings.prompts_dir)
    analyst = JevRegimeAnalyst(settings, prompts, tool_source=None)  # type: ignore[arg-type]
    ledger = EvidenceLedger()
    ledger.record_playbook("daily-weight")
    ledger.record(
        ToolObservation(
            call_id="1",
            tool="get_trend_snapshot",
            args={"interval": "D"},
            output="Error getting trend snapshot: history error",
            ok=False,
            latency_ms=1,
            truncated=False,
        )
    )
    outcome = analyst._finish(
        {
            "label": {"choice": "unknown", "confidence": 0.2},
            "side": {"choice": "bullish"},
        },
        {"state": {}, "daily": (None, None, None), "ten": (None, None, None), "vix": None},
        ledger,
        {
            "calls": [],
            "tool_calls": 1,
            "tool_errors": 1,
            "model_calls": 1,
            "input_tokens": 10,
            "output_tokens": 2,
        },
    )
    assert outcome.status == "ok"
    assert outcome.label == "unknown"
    assert outcome.defect is None
    assert len(outcome.rationale) >= 10


def test_finish_overrides_jev_unknown_when_daily_dis_disagree(settings) -> None:
    from strike_desk.jev_regime import JevRegimeAnalyst
    from strike_desk.prompt_registry import PromptRegistry

    prompts = PromptRegistry.load(settings.prompts_dir)
    analyst = JevRegimeAnalyst(settings, prompts, tool_source=None)  # type: ignore[arg-type]
    ledger = EvidenceLedger()
    ledger.record_playbook("daily-weight")
    raw = json.dumps({"indicators": {"adx_di": [12.4, 28.3, 31.36]}})
    ledger.record(
        ToolObservation(
            call_id="1",
            tool="get_trend_snapshot",
            args={"interval": "D"},
            output=raw,
            ok=True,
            latency_ms=1,
            truncated=False,
        )
    )
    outcome = analyst._finish(
        {
            "label": {"choice": "unknown", "confidence": 0.2},
            "side": {"choice": "bullish"},
        },
        {"state": {}, "daily": (12.4, 28.3, 31.36), "ten": (None, None, None), "vix": None},
        ledger,
        {
            "calls": [],
            "tool_calls": 1,
            "tool_errors": 0,
            "model_calls": 1,
            "input_tokens": 10,
            "output_tokens": 2,
        },
    )
    assert outcome.status == "ok"
    assert outcome.label == "trending"
    assert outcome.rationale.startswith("bearish")
    assert "12.4" in outcome.rationale and "28.3" in outcome.rationale
