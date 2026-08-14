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
