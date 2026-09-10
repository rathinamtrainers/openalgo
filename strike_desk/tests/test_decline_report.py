"""The report: counted from the journal, classified from the taxonomy, printed two ways."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from strike_desk import __main__ as cli
from strike_desk.decline_report import (
    build_day_report,
    build_window_report,
    render_day,
    render_window,
    to_json,
)
from tests.journal_fixtures import seed_decision, seed_regime_read


@pytest.fixture
def seeded(journal, today):
    """A day with routine declines, a hold, a degraded read and one defect."""
    for _ in range(4):
        seed_decision(journal, today, "regime-not-tradeable", token_cost_micros=1500)
    seed_decision(journal, today, "regime-low-confidence", token_cost_micros=1500)
    seed_decision(journal, today, "position-open")
    seed_decision(journal, today, "specialist-timeout")
    seed_decision(journal, today, "internal-error")
    seed_regime_read(journal, today, "range-bound")
    seed_regime_read(journal, today, "unknown")
    seed_regime_read(journal, today, "trending", source="cli")
    return journal


def test_a_day_is_counted_by_outcome_reason_category_and_disposition(seeded, today):
    report = build_day_report(seeded, today, "NIFTY")
    assert report.total == 8
    assert report.by_outcome == {"decline": 7, "hold": 1}
    assert report.declines == 7 and report.holds == 1 and report.entries == 0
    assert report.no_trade == 8
    counts = {row.code: row.count for row in report.reasons}
    assert counts == {
        "regime-not-tradeable": 4,
        "regime-low-confidence": 1,
        "position-open": 1,
        "specialist-timeout": 1,
        "internal-error": 1,
    }
    assert report.by_category == {"regime": 5, "book": 1, "specialist": 1, "system": 1}
    assert report.by_disposition == {"routine": 6, "degraded": 1, "defect": 1}
    assert report.token_cost_micros == 5 * 1500


def test_the_text_carries_the_counts_the_shares_and_the_summaries(seeded, today):
    text = render_day(build_day_report(seeded, today, "NIFTY"))
    assert "decline report - " + today in text
    assert "declines 7 | holds 1 | entries 0" in text
    assert "regime-not-tradeable" in text and "50.0%" in text
    assert "the regime is not one this playbook trades" in text
    assert "regime/routine" in text
    assert "$0.0075" in text


def test_regime_labels_come_from_tick_reads_only(seeded, today):
    report = build_day_report(seeded, today, "NIFTY")
    assert report.regime_labels == {"range-bound": 1, "unknown": 1}


def test_first_and_last_are_reported_in_ist(journal, today):
    morning = datetime(2026, 8, 21, 4, 0, tzinfo=UTC)
    afternoon = datetime(2026, 8, 21, 9, 45, tzinfo=UTC)
    seed_decision(journal, today, "regime-not-tradeable", at=morning)
    seed_decision(journal, today, "position-open", at=afternoon)
    report = build_day_report(journal, today, "NIFTY")
    assert (report.first_at_ist, report.last_at_ist) == ("09:30:00", "15:15:00")


def test_rows_written_before_the_columns_existed_still_classify(journal, today):
    seed_decision(journal, today, "regime-not-tradeable", category=None, disposition=None)
    report = build_day_report(journal, today, "NIFTY")
    assert report.by_category == {"regime": 1}
    assert report.unstamped == 1 and report.drift == 0
    assert report.healthy is True


def test_a_stored_category_that_disagrees_is_counted_as_drift(journal, today):
    seed_decision(journal, today, "regime-not-tradeable", category="system")
    report = build_day_report(journal, today, "NIFTY")
    assert report.by_category == {"regime": 1}
    assert report.drift == 1 and report.unstamped == 0


def test_an_unknown_code_is_a_defect_and_makes_the_day_unhealthy(journal, today):
    seed_decision(journal, today, "future-code", outcome="decline")
    report = build_day_report(journal, today, "NIFTY")
    assert report.by_category == {"unknown": 1}
    assert report.unknown_codes == ("future-code",)
    assert report.defects == 1 and report.healthy is False
    assert "future-code" in render_day(report)


def test_an_incomplete_trace_is_surfaced(journal, today):
    seed_decision(journal, today, "regime-not-tradeable", trace_complete=False)
    assert build_day_report(journal, today, "NIFTY").trace_incomplete == 1


def test_an_empty_day_is_zero_and_healthy(journal):
    report = build_day_report(journal, "2026-01-01", "NIFTY")
    assert report.total == 0 and report.healthy is True
    assert report.first_at_ist is None
    assert "decisions        : 0" in render_day(report)


def test_a_window_aggregates_the_days_it_holds(journal):
    seed_decision(journal, "2026-08-19", "regime-not-tradeable")
    seed_decision(journal, "2026-08-20", "regime-not-tradeable")
    seed_decision(journal, "2026-08-20", "position-open")
    seed_decision(journal, "2026-08-21", "internal-error")
    window = build_window_report(journal, "NIFTY", limit=2)
    assert [day.trading_day for day in window.days] == ["2026-08-20", "2026-08-21"]
    assert window.total == 3
    assert window.by_reason == {
        "regime-not-tradeable": 1,
        "position-open": 1,
        "internal-error": 1,
    }
    assert window.by_disposition == {"routine": 2, "defect": 1}
    assert window.healthy is False
    assert "2026-08-20" in render_window(window)


def test_an_explicit_day_list_beats_the_recency_window(journal):
    seed_decision(journal, "2026-08-19", "regime-not-tradeable")
    seed_decision(journal, "2026-08-21", "internal-error")
    window = build_window_report(journal, "NIFTY", days=["2026-08-19"])
    assert [day.trading_day for day in window.days] == ["2026-08-19"]
    assert window.healthy is True


def test_json_and_text_cannot_disagree(seeded, today):
    report = build_day_report(seeded, today, "NIFTY")
    payload = json.loads(to_json(report))["report"]
    assert payload["total"] == report.total
    assert payload["by_disposition"] == report.by_disposition
    assert {row["reason_code"]: row["count"] for row in payload["reasons"]} == {
        row.code: row.count for row in report.reasons
    }


def test_the_same_day_reports_identically_twice(seeded, today):
    first = build_day_report(seeded, today, "NIFTY")
    second = build_day_report(seeded, today, "NIFTY")
    assert first.as_dict() == second.as_dict()


# --- the command surface ----------------------------------------------------


@pytest.fixture
def cli_settings(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


def test_the_command_prints_a_clean_day_and_exits_zero(
    journal, today, cli_settings, tracing, capsys
):
    for _ in range(3):
        seed_decision(journal, today, "regime-not-tradeable")
    assert cli.main(["declines"]) == 0
    printed = capsys.readouterr().out
    assert "by disposition" in printed and "record health" in printed


def test_the_command_emits_its_own_span(seeded, cli_settings, tracing, journal, capsys):
    from sqlalchemy import select

    from strike_desk.journal import TraceSpan

    cli.main(["declines"])
    capsys.readouterr()
    with journal.session_scope() as session:
        spans = {
            span.name: json.loads(span.attributes_json)
            for span in session.execute(select(TraceSpan)).scalars()
        }
    assert "report.declines" in spans
    attributes = spans["report.declines"]
    assert attributes["report.taxonomy"].startswith("dt-4+")
    assert attributes["report.total"] == 8
    assert attributes["report.defects"] == 1
    assert attributes["report.healthy"] is False


def test_the_command_exits_two_on_a_defect(journal, today, cli_settings, tracing, capsys):
    seed_decision(journal, today, "internal-error")
    assert cli.main(["declines"]) == 2
    assert "internal-error" in capsys.readouterr().out


def test_the_command_prints_json_on_request(seeded, cli_settings, tracing, capsys):
    cli.main(["declines", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["taxonomy_version"] == "dt-4"
    assert payload["report"]["total"] == 8


def test_the_command_rejects_a_malformed_day(cli_settings):
    with pytest.raises(SystemExit) as raised:
        cli.main(["declines", "--day", "21-08-2026"])
    assert raised.value.code == 2


def test_the_command_rejects_a_day_and_a_window_together(cli_settings):
    with pytest.raises(SystemExit) as raised:
        cli.main(["declines", "--day", "2026-08-21", "--since", "3"])
    assert raised.value.code == 2


def test_status_reads_from_the_same_report(seeded, cli_settings, capsys):
    assert cli.main(["status"]) == 0
    printed = capsys.readouterr().out
    assert "taxonomy         : dt-4+" in printed
    assert "playbook         : pb-1+" in printed
    assert "risk limits      : rl-1+" in printed
    assert "strategist model : claude-sonnet-5" in printed
    assert "regime-not-tradeable" in printed
    assert "regime/routine" in printed
