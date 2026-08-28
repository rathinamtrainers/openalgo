"""Every decision the tick writes is classified, on both write paths."""

from __future__ import annotations

import json

import httpx

from strike_desk.decline_taxonomy import describe, known_codes
from tests.conftest import StubSpecialist, position


def only_decision(journal, today):
    rows = journal.list_decisions(today)
    assert len(rows) == 1, f"expected exactly one decision, got {len(rows)}"
    return rows[0]


def decide_attributes(journal, trace_id):
    for span in journal.spans_for_trace(trace_id):
        if span.name == "tick.decide":
            return json.loads(span.attributes_json)
    raise AssertionError("the tick emitted no tick.decide span")


def test_a_declining_tick_records_its_class_and_stamps_the_span(runner, journal, today):
    runner.run_tick("schedule")
    row = only_decision(journal, today)
    assert (row.reason_code, row.reason_category, row.reason_disposition) == (
        "specialist-unavailable",
        "specialist",
        "degraded",
    )
    attributes = decide_attributes(journal, row.trace_id)
    assert attributes["decision.reason_category"] == "specialist"
    assert attributes["decision.reason_disposition"] == "degraded"


def test_a_hold_is_classified_as_a_routine_book_outcome(runner, journal, openalgo, today):
    openalgo.post("/api/v1/positionbook").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": [position()]})
    )
    runner.run_tick("schedule")
    row = only_decision(journal, today)
    assert (row.outcome, row.reason_category, row.reason_disposition) == (
        "hold",
        "book",
        "routine",
    )


def test_an_unreadable_book_is_degraded_data(runner, journal, openalgo, today):
    openalgo.post("/api/v1/positionbook").mock(return_value=httpx.Response(503))
    runner.run_tick("schedule")
    row = only_decision(journal, today)
    assert (row.reason_category, row.reason_disposition) == ("data", "degraded")
    assert "never assumed flat" in row.reason_text


def test_a_regime_decline_is_routine_and_keeps_the_analyst_sentence(runner, deps, journal, today):
    deps.registry.register(
        StubSpecialist(
            payload={
                "label": "event-driven",
                "confidence": 0.9,
                "rationale": "RBI policy window is open.",
            },
            model_version="stub-1",
        )
    )
    runner.run_tick("schedule")
    row = only_decision(journal, today)
    assert (row.reason_code, row.reason_category, row.reason_disposition) == (
        "regime-not-tradeable",
        "regime",
        "routine",
    )
    assert row.reason_text.endswith("Analyst: RBI policy window is open.")


def test_a_crashed_tick_is_classified_as_a_system_defect(runner, journal, today, monkeypatch):
    import strike_desk.graph as graph_module

    def explode(*_args, **_kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(graph_module, "read_book_state", explode)
    runner.run_tick("schedule")
    row = only_decision(journal, today)
    assert (row.reason_code, row.reason_category, row.reason_disposition) == (
        "internal-error",
        "system",
        "defect",
    )
    assert "RuntimeError" in row.reason_text


def test_no_row_is_ever_written_with_a_class_the_taxonomy_disagrees_with(
    runner, deps, journal, today
):
    deps.registry.register(
        StubSpecialist(payload={"label": "trending", "confidence": 0.9}, model_version="stub-1")
    )
    runner.run_tick("schedule")
    runner.run_tick("manual")
    rows = journal.list_decisions(today, limit=10)
    assert rows
    for row in rows:
        assert row.reason_code in known_codes()
        entry = describe(row.reason_code)
        assert (row.reason_category, row.reason_disposition) == (
            entry.category,
            entry.disposition,
        )
        assert len(row.reason_text) <= deps.settings.reason_text_max_chars
