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
    tools = [
        CannedTool(name=name, output="Error getting quote: broker session expired")
        for name in TREND_SNAPSHOT
    ]
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
            ai_message(
                [
                    submit(
                        rationale="Nothing numeric here at all.",
                        evidence=[{"tool": "get_quote", "field": "ltp", "value": "unreadable"}],
                    )
                ]
            ),
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
    assert (payload["status"], payload["label"], payload["confidence"]) == (
        STATUS_OK,
        "event-driven",
        1.0,
    )
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
