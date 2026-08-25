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


def test_new_rows_land_classified_in_the_widened_table(legacy):
    legacy.create_schema()
    seed_decision(legacy, LEGACY_DAY, "internal-error")
    report = build_day_report(legacy, LEGACY_DAY, "NIFTY")
    assert report.total == 4
    assert report.unstamped == 3
    assert report.defects == 1
