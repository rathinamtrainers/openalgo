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
        ({"label": "range-bound", "confidence": 0.95}, "no-viable-contract"),
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


def test_unexpected_error_is_journalled_as_internal_error(
    runner, deps, journal, today, monkeypatch
):
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
