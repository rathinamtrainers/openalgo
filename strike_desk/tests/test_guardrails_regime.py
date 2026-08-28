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
        labels,
        books,
        errors,
        [0.0, 0.54, 0.55, 1.0],
        [None, "every tool call failed"],
        [False, True],
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
