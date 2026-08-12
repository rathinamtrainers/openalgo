"""Journal: append-only enforcement, wrapping, and queries."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from strike_desk.errors import JournalWriteError
from strike_desk.journal import SCHEMA_VERSION


def make_row(tick_id: str = "tick-1", **overrides):
    row = {
        "tick_id": tick_id,
        "trace_id": "0" * 32,
        "created_at_utc": datetime.now(tz=UTC),
        "trading_day": "2026-07-22",
        "index_symbol": "NIFTY",
        "trigger": "schedule",
        "outcome": "decline",
        "reason_code": "specialist-unavailable",
        "reason_text": "Declined: no usable 'regime' specialist.",
        "regime_label": None,
        "regime_confidence": None,
        "book_state_json": "{}",
        "prompt_set_version": "ps-000000000000",
        "model_version": "none",
        "token_cost_micros": 0,
        "latency_ms": 12,
        "trace_complete": True,
        "schema_version": SCHEMA_VERSION,
    }
    row.update(overrides)
    return row


def test_decision_round_trips(journal):
    journal.record_decision(**make_row())
    rows = journal.list_decisions("2026-07-22")
    assert len(rows) == 1
    assert rows[0].reason_code == "specialist-unavailable"
    assert journal.count_decisions("2026-07-22") == 1
    assert journal.count_decisions("2026-07-23") == 0


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE decisions SET outcome='enter'",
        "DELETE FROM decisions",
        "UPDATE traces SET name='tampered'",
        "DELETE FROM traces",
    ],
)
def test_tables_are_append_only(journal, statement):
    journal.record_decision(**make_row())
    journal.record_span(
        trace_id="0" * 32,
        span_id="1" * 16,
        parent_span_id=None,
        name="strike_desk.tick",
        started_at_utc=datetime.now(tz=UTC),
        ended_at_utc=datetime.now(tz=UTC),
        duration_ms=5,
        status="UNSET",
        attributes_json="{}",
    )
    with pytest.raises(Exception, match="append-only"):
        with journal.session_scope() as session:
            session.execute(text(statement))


def test_duplicate_tick_id_raises_journal_write_error(journal):
    journal.record_decision(**make_row("tick-dup"))
    with pytest.raises(JournalWriteError):
        journal.record_decision(**make_row("tick-dup"))


def test_spans_for_trace_are_ordered(journal):
    base = datetime.now(tz=UTC)
    for index, name in enumerate(["strike_desk.tick", "tick.plan", "tick.decide"]):
        journal.record_span(
            trace_id="a" * 32,
            span_id=f"{index:016d}",
            parent_span_id=None if index == 0 else "0" * 16,
            name=name,
            started_at_utc=base.replace(microsecond=index * 1000),
            ended_at_utc=base.replace(microsecond=(index + 1) * 1000),
            duration_ms=1,
            status="UNSET",
            attributes_json="{}",
        )
    assert [span.name for span in journal.spans_for_trace("a" * 32)] == [
        "strike_desk.tick",
        "tick.plan",
        "tick.decide",
    ]
