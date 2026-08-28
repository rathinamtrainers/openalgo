"""Widening an existing journal in place, without touching a row."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from strike_desk.decline_report import build_day_report
from strike_desk.journal import ADDED_COLUMNS, Journal
from tests.journal_fixtures import seed_decision, write_legacy_journal

LEGACY_DAY = "2026-08-19"
LEGACY_ROWS = [
    ("decline", "specialist-unavailable"),
    ("hold", "position-open"),
    ("decline", "regime-not-tradeable"),
]


@pytest.fixture
def legacy(settings):
    """A journal written by the previous release, opened by this one."""
    write_legacy_journal(settings.db_path, LEGACY_DAY, LEGACY_ROWS)
    journal = Journal(settings.db_path)
    yield journal
    journal.close()


def columns(journal: Journal) -> set[str]:
    with journal.session_scope() as session:
        rows = session.execute(text("PRAGMA table_info(decisions)")).fetchall()
    return {str(row[1]) for row in rows}


def test_the_columns_are_absent_until_the_schema_is_created(legacy):
    assert "reason_category" not in columns(legacy)
    legacy.create_schema()
    assert {name for _table, name, _type in ADDED_COLUMNS} <= columns(legacy)


def test_no_legacy_row_is_touched(legacy):
    legacy.create_schema()
    rows = legacy.list_decisions(LEGACY_DAY, limit=10)
    assert len(rows) == len(LEGACY_ROWS)
    assert all(row.reason_category is None and row.reason_disposition is None for row in rows)
    assert {row.reason_text for row in rows} == {f"legacy row {index}" for index in range(3)}


def test_legacy_rows_still_report_by_category(legacy):
    legacy.create_schema()
    report = build_day_report(legacy, LEGACY_DAY, "NIFTY")
    assert report.total == 3
    assert report.by_category == {"specialist": 1, "book": 1, "regime": 1}
    assert report.unstamped == 3 and report.drift == 0
    assert report.healthy is True


def test_the_table_is_still_append_only_after_widening(legacy):
    legacy.create_schema()
    for statement in ("UPDATE decisions SET reason_category='book'", "DELETE FROM decisions"):
        with pytest.raises(Exception, match="append-only"):
            with legacy.session_scope() as session:
                session.execute(text(statement))


def test_creating_the_schema_twice_is_safe(legacy):
    legacy.create_schema()
    before = columns(legacy)
    legacy.create_schema()
    assert columns(legacy) == before


def _table_names(path) -> set[str]:
    from sqlalchemy import create_engine
    from sqlalchemy import text as sql_text

    engine = create_engine(f"sqlite:///{path}", future=True)
    try:
        with engine.begin() as connection:
            rows = connection.execute(
                sql_text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        return {str(row[0]) for row in rows}
    finally:
        engine.dispose()


def _row_count(path, table: str) -> int:
    from sqlalchemy import create_engine
    from sqlalchemy import text as sql_text

    engine = create_engine(f"sqlite:///{path}", future=True)
    try:
        with engine.begin() as connection:
            return int(connection.execute(sql_text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
    finally:
        engine.dispose()


def _triggers_for(path, table: str) -> set[str]:
    from sqlalchemy import create_engine
    from sqlalchemy import text as sql_text

    engine = create_engine(f"sqlite:///{path}", future=True)
    try:
        with engine.begin() as connection:
            rows = connection.execute(
                sql_text("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=:t"),
                {"t": table},
            ).fetchall()
        return {str(row[0]) for row in rows}
    finally:
        engine.dispose()


def test_a_v3_journal_gains_the_table_in_place(tmp_path) -> None:
    """A journal written at schema 3 gains ``proposals`` in place; no row is rewritten."""
    from sqlalchemy import create_engine
    from sqlalchemy import text as sql_text

    path = tmp_path / "v3.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
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
                    reason_category VARCHAR(24),
                    reason_disposition VARCHAR(16),
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
            )
            connection.execute(
                sql_text(
                    "INSERT INTO decisions (tick_id, trace_id, created_at_utc, trading_day, "
                    "index_symbol, \"trigger\", outcome, reason_code, reason_text, "
                    "reason_category, reason_disposition, book_state_json, prompt_set_version, "
                    "model_version, token_cost_micros, latency_ms, trace_complete, schema_version) "
                    "VALUES (:tick_id, :trace_id, :created_at, :trading_day, 'NIFTY', "
                    "'schedule', 'decline', 'regime-not-tradeable', 'legacy v3', "
                    "'regime', 'routine', '{}', 'ps-v3', 'none', 0, 12, 1, 3)"
                ),
                {
                    "tick_id": "v3-1",
                    "trace_id": "0" * 32,
                    "created_at": "2026-08-21 04:00:00",
                    "trading_day": "2026-08-21",
                },
            )
    finally:
        engine.dispose()

    before = _table_names(path), _row_count(path, "decisions")
    journal = Journal(path)
    try:
        journal.create_schema()
        journal.create_schema()
        rows = journal.list_decisions("2026-08-21")
        assert len(rows) == 1
        assert rows[0].reason_text == "legacy v3"
        assert rows[0].reason_category == "regime"
    finally:
        journal.close()

    after = _table_names(path), _row_count(path, "decisions")
    assert "proposals" not in before[0] and "proposals" in after[0]
    assert before[1] == after[1]
    assert _triggers_for(path, "proposals") == {"proposals_no_update", "proposals_no_delete"}


def test_new_rows_land_classified_in_the_widened_table(legacy):
    legacy.create_schema()
    seed_decision(legacy, LEGACY_DAY, "internal-error")
    report = build_day_report(legacy, LEGACY_DAY, "NIFTY")
    assert report.total == 4
    assert report.unstamped == 3
    assert report.defects == 1
