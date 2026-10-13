"""Always-on Jev validation: wrapped MCP payloads must not become ungrounded sits."""

from __future__ import annotations

import json

from strike_desk.grounding import (
    EvidenceLedger,
    ToolObservation,
    tool_output_ok,
    unwrap_tool_output,
)
from strike_desk.jev_regime import JevRegimeAnalyst, _grounded_sentence, _trend
from strike_desk.prompt_registry import PromptRegistry

# Host 24 Sep 2026: LangChain stored a Python-repr content block, not raw JSON.
HOST_WRAPPED_HISTORY_ERROR = str(
    [
        {
            "type": "text",
            "text": (
                "Error getting trend snapshot: history error: "
                "HTTP 500 Incorrect api_key or access_token"
            ),
            "id": "lc_host",
        }
    ]
)


def test_host_wrapped_history_error_is_a_failed_call_not_a_snapshot():
    inner = unwrap_tool_output(HOST_WRAPPED_HISTORY_ERROR)
    assert inner.startswith("Error getting trend snapshot")
    assert tool_output_ok(HOST_WRAPPED_HISTORY_ERROR) is False
    assert _trend(HOST_WRAPPED_HISTORY_ERROR) == (None, None, None)


def test_a_live_snapshot_still_grounds_a_trending_read(settings):
    prompts = PromptRegistry.load(settings.prompts_dir)
    analyst = JevRegimeAnalyst(settings, prompts, tool_source=None)  # type: ignore[arg-type]
    daily = json.dumps(
        {
            "symbol": "NIFTY",
            "interval": "D",
            "indicators": {"adx_di": [12.4, 28.3, 31.36]},
            "legend": {"adx_di": "[+DI, -DI, ADX]"},
        },
        indent=2,
    )
    wrapped = json.dumps([{"type": "text", "text": daily}])
    ledger = EvidenceLedger()
    ledger.record_playbook("daily-weight 10m-entry prove trending 0.72")
    ledger.record(
        ToolObservation(
            call_id="d",
            tool="get_trend_snapshot",
            args={"interval": "D"},
            output=unwrap_tool_output(wrapped),
            ok=tool_output_ok(wrapped),
            latency_ms=80,
            truncated=False,
        )
    )
    outcome = analyst._finish(
        {"label": {"choice": "unknown"}, "side": {"choice": "bullish"}},
        {"state": {}, "daily": _trend(wrapped), "ten": (None, None, None), "vix": None},
        ledger,
        {
            "calls": ledger.summary(),
            "tool_calls": 1,
            "tool_errors": 0,
            "model_calls": 1,
            "input_tokens": 50,
            "output_tokens": 8,
        },
    )
    assert outcome.status == "ok"
    assert outcome.label == "trending"
    assert outcome.defect is None
    assert len(outcome.rationale) >= 10
    assert "12.4" in outcome.rationale
    assert outcome.trend_snapshot is not None
    assert outcome.trend_snapshot["daily_adx"] == 31.36


def test_short_side_word_is_not_a_valid_rationale():
    text, _ = _grounded_sentence("bullish", (None, None, None), (None, None, None), None)
    assert text != "bullish."
    assert len(text) >= 10
