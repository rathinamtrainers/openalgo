"""Helpers for seeding a journal, and for building one shaped like the last release."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from strike_desk.decline_taxonomy import describe
from strike_desk.journal import SCHEMA_VERSION, Journal

_UNSET: Any = object()

# The `decisions` table exactly as iteration 02 shipped it: no classification columns.
LEGACY_DECISIONS_DDL = """
CREATE TABLE decisions (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    tick_id VARCHAR(36) NOT NULL UNIQUE,
    trace_id VARCHAR(32) NOT NULL,
    created_at_utc DATETIME NOT NULL,
    trading_day VARCHAR(10) NOT NULL,
    index_symbol VARCHAR(32) NOT NULL,
    "trigger" VARCHAR(16) NOT NULL,
    outcome VARCHAR(16) NOT NULL,
    reason_code VARCHAR(48) NOT NULL,
    reason_text TEXT NOT NULL,
    regime_label VARCHAR(32),
    regime_confidence FLOAT,
    book_state_json TEXT NOT NULL,
    prompt_set_version VARCHAR(32) NOT NULL,
    model_version VARCHAR(128) NOT NULL,
    token_cost_micros INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    trace_complete BOOLEAN NOT NULL,
    schema_version INTEGER NOT NULL
)
"""

LEGACY_TRIGGERS = (
    "CREATE TRIGGER decisions_no_update BEFORE UPDATE ON decisions "
    "BEGIN SELECT RAISE(ABORT, 'decisions is append-only'); END",
    "CREATE TRIGGER decisions_no_delete BEFORE DELETE ON decisions "
    "BEGIN SELECT RAISE(ABORT, 'decisions is append-only'); END",
)

LEGACY_INSERT = """
INSERT INTO decisions (
    tick_id, trace_id, created_at_utc, trading_day, index_symbol, "trigger", outcome,
    reason_code, reason_text, book_state_json, prompt_set_version, model_version,
    token_cost_micros, latency_ms, trace_complete, schema_version
) VALUES (
    :tick_id, :trace_id, :created_at_utc, :trading_day, 'NIFTY', 'schedule', :outcome,
    :reason_code, :reason_text, '{}', 'ps-legacy', 'none', 0, 42, 1, 2
)
"""


def write_legacy_journal(db_path: Path, trading_day: str, rows: list[tuple[str, str]]) -> None:
    """Build a database with the previous release's schema and rows in it."""
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(LEGACY_DECISIONS_DDL)
            for statement in LEGACY_TRIGGERS:
                connection.exec_driver_sql(statement)
            for index, (outcome, reason_code) in enumerate(rows):
                connection.execute(
                    text(LEGACY_INSERT),
                    {
                        "tick_id": f"legacy-{index}",
                        "trace_id": uuid.uuid4().hex,
                        "created_at_utc": f"2026-08-19 04:{15 + index:02d}:00.000000",
                        "trading_day": trading_day,
                        "outcome": outcome,
                        "reason_code": reason_code,
                        "reason_text": f"legacy row {index}",
                    },
                )
    finally:
        engine.dispose()


def seed_decision(
    journal: Journal,
    trading_day: str,
    reason_code: str,
    *,
    outcome: str | None = None,
    category: Any = _UNSET,
    disposition: Any = _UNSET,
    at: datetime | None = None,
    token_cost_micros: int = 0,
    trace_complete: bool = True,
) -> None:
    """Append one decision, classified from the taxonomy unless a test overrides it."""
    entry = describe(reason_code)
    journal.record_decision(
        tick_id=str(uuid.uuid4()),
        trace_id=uuid.uuid4().hex,
        created_at_utc=at or datetime.now(tz=UTC),
        trading_day=trading_day,
        index_symbol="NIFTY",
        trigger="schedule",
        outcome=outcome or entry.outcome,
        reason_code=reason_code,
        reason_text=f"seeded {reason_code}",
        reason_category=entry.category if category is _UNSET else category,
        reason_disposition=entry.disposition if disposition is _UNSET else disposition,
        regime_label=None,
        regime_confidence=None,
        book_state_json="{}",
        prompt_set_version="ps-test",
        model_version="none",
        token_cost_micros=token_cost_micros,
        latency_ms=17,
        trace_complete=trace_complete,
        schema_version=SCHEMA_VERSION,
    )


def seed_regime_read(
    journal: Journal,
    trading_day: str,
    label: str,
    *,
    source: str = "tick",
    status: str = "ok",
) -> None:
    """Append one regime read so the report has labels to count."""
    journal.record_regime_read(
        read_id=str(uuid.uuid4()),
        tick_id=str(uuid.uuid4()),
        trace_id=uuid.uuid4().hex,
        created_at_utc=datetime.now(tz=UTC),
        trading_day=trading_day,
        index_symbol="NIFTY",
        source=source,
        status=status,
        label=label,
        confidence=0.7,
        rationale="seeded read",
        evidence_json="[]",
        defect=None,
        tool_call_count=2,
        tool_error_count=0,
        model_calls=1,
        model_version="claude-haiku-4-5",
        input_tokens=900,
        output_tokens=120,
        token_cost_micros=1500,
        prompt_name="regime_analyst",
        prompt_version="v2",
        prompt_digest="deadbeef",
        prompt_set_version="ps-test",
        latency_ms=1200,
        schema_version=SCHEMA_VERSION,
    )
