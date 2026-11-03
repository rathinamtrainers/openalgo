"""Journal: append-only enforcement, wrapping, and queries."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from strike_desk.errors import JournalWriteError
from strike_desk.journal import SCHEMA_VERSION, append_decision_export


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
    assert journal.list_recent_decisions(limit=5)[0].tick_id == "tick-1"


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


def test_append_decision_export_writes_jsonl(tmp_path):
    path = tmp_path / "decisions.jsonl"
    append_decision_export(make_row("tick-export"), path)
    append_decision_export(make_row("tick-export-2"), path)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert "tick-export" in lines[0]


def test_record_decision_appends_jsonl_export(journal, tmp_path, monkeypatch):
    export = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("STRIKE_DESK_DECISIONS_EXPORT", str(export))
    journal.record_decision(**make_row("tick-from-journal"))
    lines = export.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "tick-from-journal" in lines[0]


def _unmanaged_symbols(journal, trading_day, positions):
    """Same call graph.py plan uses on GCE: live_symbols(trading_day)."""
    live = journal.live_symbols(trading_day)
    unmanaged = []
    for position in positions:
        symbol = str(position.get("symbol") or "").strip().upper()
        if symbol and symbol not in live:
            unmanaged.append(symbol)
    return unmanaged


@pytest.mark.parametrize(
    "index_symbol",
    ["NIFTY", "BANKNIFTY", "SENSEX", "CRUDEOIL", "BTC", "ETH", "COA", "OVERLAY"],
)
def test_live_symbols_accepts_optional_trading_day(journal, index_symbol):
    """Plan/graph callers pass trading_day; stub journals used to take only self."""
    trading_day = "2026-11-03"
    assert journal.live_symbols() == frozenset()
    assert journal.live_symbols(trading_day) == frozenset()
    assert journal.live_symbols(trading_day=trading_day) == frozenset()
    assert _unmanaged_symbols(
        journal,
        trading_day,
        [{"symbol": f"{index_symbol}03NOV2622600CE"}],
    ) == [f"{index_symbol}03NOV2622600CE"]
