# Iteration 03 — Test Automation

## 1. Framework, and what is real

pytest, as in every iteration, with the fixtures `tests/conftest.py` already provides — an isolated `Settings` per test pointing at a temporary state directory, a `Journal` on a real SQLite file, a tracer whose spans land in that journal's `traces` table, a scripted OpenAlgo behind `respx`, and a `TickRunner` wired to all of it. Nothing new is mocked here and nothing new needs to be: this slice has no model call, no network call and no subprocess, so every test runs against real SQLite and real taxonomy code. The only doubles in play are the ones iteration 01 and 02 already built — the `respx` router standing in for OpenAlgo, and `StubSpecialist` standing in for the Regime Analyst.

That also settles what the quality gate looks like. Where a slice exercises agent reasoning it needs a live-model eval suite; this one exercises none, so the equivalent regression gate is deterministic: a frozen golden file that pins every trader-facing sentence, and a parity check that reflects over the decision table and the taxonomy and fails in either direction. Iteration 02's live regime evals are untouched and still run only when the reasoning plane changes.

Four test modules and one fixture module are added. Coverage intent is unchanged — the suite must hold the repository's `--cov-fail-under=80` — and both new source modules are pure enough to sit comfortably above it: the taxonomy is exercised entry by entry, and the report is exercised through both its text and its JSON rendering as well as through the command.

## 2. Seeding a journal, and ageing one

Two jobs need a helper. Report tests need decisions in the journal without running a tick for each one, and the migration test needs a database shaped the way the *previous* release shaped it. `seed_decision` appends a row classified from the taxonomy by default, with a sentinel that lets a test store `None` (an unstamped row) or a deliberately wrong category (a drifted row) — the two conditions the report's health block exists to catch. `write_legacy_journal` builds `decisions` from raw DDL with no classification columns and with the same append-only triggers the real table carries, then inserts rows through the timestamp format SQLAlchemy's SQLite dialect writes, so the rows read back exactly like rows the old binary wrote. Note the quoted `"trigger"` column: it is a SQL keyword, and the ORM quotes it for you everywhere else.

### `strike_desk/tests/journal_fixtures.py`

```python
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
```

## 3. The taxonomy gate

This is the module CI runs before anything else, because it is the one that fails when someone adds a reason code and forgets to classify it. `emitted_codes` reflects over `strike_desk.graph` for every `REASON_*` constant rather than listing them, so the check cannot rot as the decision table grows; the two subset assertions then bind in both directions — an unclassified code fails, and an entry no branch emits fails too.

The golden cases are the wording regression. Each case names a code, a variant and the exact fields a branch supplies, and asserts the rendered sentence byte for byte. Every case renders with no rationale, which is what keeps the test deterministic: the analyst's sentence is non-deterministic model output, and pinning it would produce a suite that passes on your laptop and fails in CI. The rationale's *handling* is tested separately, against a synthetic string, and asserts the property that matters — the verdict is never the part that gets cut.

### `strike_desk/tests/test_decline_taxonomy.py`

```python
"""The taxonomy: parity with the decision table, and frozen trader-facing wording."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from strike_desk import graph as graph_module
from strike_desk.decline_taxonomy import (
    CATEGORIES,
    DISPOSITIONS,
    MIN_RATIONALE_ROOM,
    RATIONALE_PREFIX,
    REASONS,
    TAXONOMY_ARTIFACT,
    TAXONOMY_DIGEST,
    TAXONOMY_VERSION,
    ReasonCodeUnknown,
    compute_digest,
    describe,
    entry,
    known_codes,
    render,
)

ENTRIES = tuple(REASONS[code] for code in sorted(REASONS))

GOLDEN = json.loads(
    (Path(__file__).parent / "golden" / "reason_text.json").read_text(encoding="utf-8")
)


def emitted_codes() -> set[str]:
    """Every reason code the decision table can write, read off the module itself."""
    return {
        value
        for name, value in vars(graph_module).items()
        if name.startswith("REASON_") and isinstance(value, str)
    }


def test_every_code_the_graph_can_emit_is_classified():
    assert emitted_codes() <= known_codes(), sorted(emitted_codes() - known_codes())


def test_no_taxonomy_entry_is_orphaned():
    assert known_codes() <= emitted_codes(), sorted(known_codes() - emitted_codes())


@pytest.mark.parametrize("item", ENTRIES, ids=lambda item: item.code)
def test_entries_are_well_formed(item):
    assert item.category in CATEGORIES
    assert item.disposition in DISPOSITIONS
    assert item.outcome in {"decline", "hold"}
    assert item.summary.strip()
    assert item.templates["default"].strip()


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda case: case["id"])
def test_frozen_sentences(case):
    """A trader-facing sentence changes only when someone edits this file on purpose."""
    rendered = render(
        case["code"],
        max_chars=GOLDEN["max_chars"],
        variant=case["variant"],
        **case["fields"],
    )
    assert rendered == case["expect"]


def test_the_golden_file_covers_every_code():
    assert {case["code"] for case in GOLDEN["cases"]} == known_codes()


def test_the_rationale_is_appended_and_is_the_only_part_ever_cut():
    verdict = render("regime-not-tradeable", max_chars=400, label="unknown")
    capped = render("regime-not-tradeable", max_chars=140, label="unknown", rationale="y" * 300)
    assert capped.startswith(verdict + RATIONALE_PREFIX)
    assert len(capped) <= 140
    assert capped.endswith("...")


def test_a_rationale_with_no_room_is_dropped_rather_than_the_verdict():
    verdict = render("regime-not-tradeable", max_chars=400, label="unknown")
    tight = len(verdict) + len(RATIONALE_PREFIX) + MIN_RATIONALE_ROOM - 1
    assert render(
        "regime-not-tradeable", max_chars=tight, label="unknown", rationale="z" * 200
    ) == verdict


def test_a_missing_field_renders_visibly_rather_than_raising():
    assert "unspecified" in render("tick-timeout", max_chars=400)


def test_a_sentence_is_always_one_line():
    text = render("data-quality", max_chars=400, variant="book", detail="line one\nline two")
    assert "\n" not in text and "  " not in text


def test_an_unknown_variant_falls_back_to_the_default_sentence():
    fallback = render("data-quality", max_chars=400, variant="not-a-variant", detail="x")
    assert fallback == render("data-quality", max_chars=400, detail="x")


def test_an_unrecognised_code_describes_as_a_defect_and_refuses_to_render():
    placeholder = describe("some-future-code")
    assert (placeholder.category, placeholder.disposition) == ("unknown", "defect")
    with pytest.raises(ReasonCodeUnknown):
        render("some-future-code", max_chars=400, detail="x")
    with pytest.raises(ReasonCodeUnknown):
        entry("some-future-code")


def test_the_artifact_moves_when_the_taxonomy_moves():
    assert TAXONOMY_ARTIFACT == f"{TAXONOMY_VERSION}+{TAXONOMY_DIGEST}"
    assert len(TAXONOMY_DIGEST) == 12
    assert compute_digest(ENTRIES[1:]) != TAXONOMY_DIGEST
```

### `strike_desk/tests/golden/reason_text.json`

```json
{
  "pass_bar": "every rendered sentence must match exactly; changing the wording means editing this file",
  "max_chars": 400,
  "cases": [
    {
      "id": "GD-01",
      "code": "position-open",
      "variant": "default",
      "fields": {
        "count": 1,
        "index": "NIFTY",
        "symbols": "NIFTY28JUL2624500CE"
      },
      "expect": "Held: 1 open NIFTY position(s) (NIFTY28JUL2624500CE). This tick manages the book; it does not add to it."
    },
    {
      "id": "GD-02",
      "code": "data-quality",
      "variant": "book",
      "fields": {
        "detail": "positionbook: HTTP 503"
      },
      "expect": "Declined: the book could not be read from OpenAlgo (positionbook: HTTP 503). An unreadable book is never assumed flat."
    },
    {
      "id": "GD-03",
      "code": "data-quality",
      "variant": "regime",
      "fields": {
        "detail": "every tool call failed"
      },
      "expect": "Declined: the regime could not be read from live data (every tool call failed). The desk does not classify a market it could not see."
    },
    {
      "id": "GD-04",
      "code": "data-quality",
      "variant": "default",
      "fields": {
        "detail": "the feed was stale"
      },
      "expect": "Declined: live data could not be read (the feed was stale). The desk does not act on a market it could not see."
    },
    {
      "id": "GD-05",
      "code": "specialist-unavailable",
      "variant": "default",
      "fields": {
        "role": "regime",
        "detail": "no specialist registered for this role"
      },
      "expect": "Declined: no usable 'regime' specialist (no specialist registered for this role)."
    },
    {
      "id": "GD-06",
      "code": "specialist-unavailable",
      "variant": "no_strategist",
      "fields": {
        "role": "strategist",
        "label": "trending",
        "confidence": "0.80"
      },
      "expect": "Declined: regime 'trending' is tradeable at 0.80 confidence, but no 'strategist' specialist is registered to propose a contract. A regime read alone is never an entry."
    },
    {
      "id": "GD-07",
      "code": "specialist-timeout",
      "variant": "default",
      "fields": {
        "role": "regime",
        "detail": "exceeded 25.0s"
      },
      "expect": "Declined: the 'regime' specialist did not answer within its timeout (exceeded 25.0s)."
    },
    {
      "id": "GD-08",
      "code": "regime-not-tradeable",
      "variant": "default",
      "fields": {
        "label": "event-driven"
      },
      "expect": "Declined: regime read as 'event-driven', which this playbook does not trade."
    },
    {
      "id": "GD-09",
      "code": "regime-low-confidence",
      "variant": "default",
      "fields": {
        "label": "trending",
        "confidence": "0.40",
        "floor": "0.55"
      },
      "expect": "Declined: regime 'trending' is tradeable but confidence 0.40 is below the 0.55 floor."
    },
    {
      "id": "GD-10",
      "code": "regime-ungrounded",
      "variant": "default",
      "fields": {
        "detail": "the rationale cites 24810, which no tool returned"
      },
      "expect": "Declined: the regime read cited data it did not fetch (the rationale cites 24810, which no tool returned). An ungrounded read is a defect, not an opinion."
    },
    {
      "id": "GD-11",
      "code": "tick-timeout",
      "variant": "default",
      "fields": {
        "budget": "40"
      },
      "expect": "Declined: the tick exceeded its 40s budget before a decision could be assembled."
    },
    {
      "id": "GD-12",
      "code": "internal-error",
      "variant": "default",
      "fields": {
        "error": "RuntimeError"
      },
      "expect": "Declined: the tick raised RuntimeError before assembling a decision. The desk stays out when it cannot reason."
    }
  ]
}
```

## 4. The report

The report tests seed a day whose shape is the point: four routine regime declines, one low-confidence decline, one hold, one degraded timeout and one defect, plus three regime reads of which one is a CLI read that must not be counted. From that one fixture the counts, the categories, the dispositions, the cost, the labels and the rendered text are all checked against numbers you can verify by hand from the fixture itself.

The three record-health cases are the ones worth reading closely, because they are what proves AC-7 and AC-8. An unstamped row still reports under `regime`, because the category is resolved from the taxonomy rather than read from the column. A row whose stored category says `system` when the taxonomy says `regime` is counted under `regime` and flagged as drift. A row carrying a code this build does not define is counted under `unknown`, named in the health block, and turns the day unhealthy — which is what makes the command exit 2.

The command tests drive `main()` directly with `get_settings` monkeypatched to the test's settings, so argument parsing, exit codes and the `report.declines` span are all covered without a subprocess. They take the `tracing` fixture so the global tracer provider is reset around each one and the span lands in the same journal the assertions read.

### `strike_desk/tests/test_decline_report.py`

```python
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
    assert attributes["report.taxonomy"].startswith("dt-1+")
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
    assert payload["taxonomy_version"] == "dt-1"
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
    assert "taxonomy         : dt-1+" in printed
    assert "regime-not-tradeable" in printed
    assert "regime/routine" in printed
```

## 5. The migration

The migration test opens a legacy database with this release's `Journal` and proves the four things a deployment depends on: the columns are absent until `create_schema()` runs and present afterwards, no legacy row is read or rewritten in the process, the append-only triggers still abort an `UPDATE` and a `DELETE` on the widened table, and calling `create_schema()` twice changes nothing. It then reports over the legacy rows to prove they classify, which is the claim the whole read-time-resolution design rests on.

### `strike_desk/tests/test_journal_migration.py`

```python
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
```

## 6. Classification through a real tick

The unit tests prove the taxonomy is right; these prove it is actually reached. Each case runs a real tick through the graph and asserts the stored classification and, for the first, the two new span attributes. The crashed-tick case matters most: it monkeypatches `read_book_state` to raise so the decision is written by the runner's fallback rather than by the graph, which is the write path a classification layer is most likely to miss. The last test is a sweep — every row any of these ticks wrote must carry a code the taxonomy knows, a category and disposition equal to that code's entry, and a sentence inside the configured cap.

### `strike_desk/tests/test_tick_declines.py`

```python
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


def test_a_regime_decline_is_routine_and_keeps_the_analyst_sentence(
    runner, deps, journal, today
):
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
```

## 7. Running it

```bash
cd strike_desk
uv sync --group dev
uv run ruff check .
uv run pytest -q                                        # the whole suite
uv run pytest tests/test_decline_taxonomy.py -q         # the wording and parity gate
uv run pytest --cov=strike_desk --cov-report=term-missing
```

CI gains the two new deterministic gates in the step that runs before the full suite, so a parity failure or a changed sentence is the first thing the log shows rather than something you find three minutes later. The eval job is unchanged: it still triggers only on a change under the reasoning plane's paths, and this slice touches none of them.

### `.github/workflows/strike-desk-ci.yml`

```yaml
name: strike-desk

on:
  push:
    paths: ["strike_desk/**", ".github/workflows/strike-desk-ci.yml"]
  pull_request:
    paths: ["strike_desk/**", ".github/workflows/strike-desk-ci.yml"]

jobs:
  verify:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: strike_desk
    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Install Python 3.12 and dependencies
        run: |
          uv python install 3.12
          uv sync --group dev

      - name: Lint
        run: uv run ruff check .

      - name: Guardrail and regression gate
        run: >-
          uv run pytest -q
          tests/test_guardrails.py
          tests/test_guardrails_regime.py
          tests/test_decline_taxonomy.py
          tests/test_journal_migration.py
          tests/regression

      - name: Full suite with coverage
        run: uv run pytest --cov=strike_desk --cov-report=term-missing --cov-fail-under=80

      - name: Security scan
        run: |
          uv run bandit -q -r src
          uv run pip-audit

  evals:
    needs: verify
    runs-on: ubuntu-latest
    if: github.event_name == 'pull_request'
    defaults:
      run:
        working-directory: strike_desk
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Decide whether the reasoning plane changed
        id: changed
        run: |
          git diff --name-only origin/${{ github.base_ref }}...HEAD > /tmp/changed.txt
          if grep -qE 'strike_desk/src/strike_desk/(prompts/|regime_analyst|grounding|model_client)' /tmp/changed.txt \
             || grep -q 'strike_desk/tests/evals/' /tmp/changed.txt; then
            echo "run=true" >> "$GITHUB_OUTPUT"
          else
            echo "run=false" >> "$GITHUB_OUTPUT"
          fi

      - uses: astral-sh/setup-uv@v5
        if: steps.changed.outputs.run == 'true'
        with:
          enable-cache: true

      - name: Install and run the regime evals
        if: steps.changed.outputs.run == 'true'
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          uv python install 3.12
          uv sync --group dev
          uv run pytest tests/evals -m evals -q
```

## 8. Traceability

| Automated test | Covers | Backstops manual case |
| --- | --- | --- |
| `test_tick_declines.py::test_a_declining_tick_records_its_class_and_stamps_the_span` | AC-1, AC-10 | MT-01, MT-08 |
| `test_tick_declines.py::test_a_hold_is_classified_as_a_routine_book_outcome` | AC-1 | MT-02 |
| `test_tick_declines.py::test_an_unreadable_book_is_degraded_data` | AC-1, AC-3 | MT-02 |
| `test_tick_declines.py::test_a_regime_decline_is_routine_and_keeps_the_analyst_sentence` | AC-1, AC-3 | MT-03 |
| `test_tick_declines.py::test_a_crashed_tick_is_classified_as_a_system_defect` | AC-1 | MT-09 |
| `test_tick_declines.py::test_no_row_is_ever_written_with_a_class_the_taxonomy_disagrees_with` | AC-1, AC-3 | MT-01 |
| `test_decline_taxonomy.py::test_every_code_the_graph_can_emit_is_classified` | AC-2 | MT-10 |
| `test_decline_taxonomy.py::test_no_taxonomy_entry_is_orphaned` | AC-2 | MT-10 |
| `test_decline_taxonomy.py::test_entries_are_well_formed` | AC-2 | MT-10 |
| `test_decline_taxonomy.py::test_frozen_sentences` | AC-3, AC-12 | MT-11 |
| `test_decline_taxonomy.py::test_the_golden_file_covers_every_code` | AC-12 | MT-11 |
| `test_decline_taxonomy.py::test_the_rationale_is_appended_and_is_the_only_part_ever_cut` | AC-3 | MT-03 |
| `test_decline_taxonomy.py::test_a_rationale_with_no_room_is_dropped_rather_than_the_verdict` | AC-3 | MT-03 |
| `test_decline_taxonomy.py::test_a_missing_field_renders_visibly_rather_than_raising` | AC-3 | — |
| `test_decline_taxonomy.py::test_a_sentence_is_always_one_line` | AC-3 | MT-01 |
| `test_decline_taxonomy.py::test_an_unrecognised_code_describes_as_a_defect_and_refuses_to_render` | AC-8 | MT-07 |
| `test_decline_taxonomy.py::test_the_artifact_moves_when_the_taxonomy_moves` | AC-12 | MT-11 |
| `test_decline_report.py::test_a_day_is_counted_by_outcome_reason_category_and_disposition` | AC-4 | MT-04 |
| `test_decline_report.py::test_the_text_carries_the_counts_the_shares_and_the_summaries` | AC-4 | MT-04 |
| `test_decline_report.py::test_regime_labels_come_from_tick_reads_only` | AC-5 | MT-04 |
| `test_decline_report.py::test_first_and_last_are_reported_in_ist` | AC-4 | MT-04 |
| `test_decline_report.py::test_rows_written_before_the_columns_existed_still_classify` | AC-7, AC-8 | MT-06 |
| `test_decline_report.py::test_a_stored_category_that_disagrees_is_counted_as_drift` | AC-8 | MT-07 |
| `test_decline_report.py::test_an_unknown_code_is_a_defect_and_makes_the_day_unhealthy` | AC-8, AC-11 | MT-07 |
| `test_decline_report.py::test_an_incomplete_trace_is_surfaced` | AC-8 | MT-07 |
| `test_decline_report.py::test_an_empty_day_is_zero_and_healthy` | AC-4 | MT-05 |
| `test_decline_report.py::test_a_window_aggregates_the_days_it_holds` | AC-6 | MT-05 |
| `test_decline_report.py::test_an_explicit_day_list_beats_the_recency_window` | AC-6 | MT-05 |
| `test_decline_report.py::test_json_and_text_cannot_disagree` | AC-6 | MT-05 |
| `test_decline_report.py::test_the_same_day_reports_identically_twice` | AC-7 | MT-06 |
| `test_decline_report.py::test_the_command_prints_a_clean_day_and_exits_zero` | AC-4, AC-11 | MT-04 |
| `test_decline_report.py::test_the_command_emits_its_own_span` | AC-10 | MT-08 |
| `test_decline_report.py::test_the_command_exits_two_on_a_defect` | AC-11 | MT-07 |
| `test_decline_report.py::test_the_command_prints_json_on_request` | AC-6 | MT-05 |
| `test_decline_report.py::test_the_command_rejects_a_malformed_day` | AC-6 | MT-12 |
| `test_decline_report.py::test_the_command_rejects_a_day_and_a_window_together` | AC-6 | MT-12 |
| `test_decline_report.py::test_status_reads_from_the_same_report` | AC-11, AC-12 | MT-11 |
| `test_journal_migration.py::test_the_columns_are_absent_until_the_schema_is_created` | AC-9 | MT-06 |
| `test_journal_migration.py::test_no_legacy_row_is_touched` | AC-9 | MT-06 |
| `test_journal_migration.py::test_legacy_rows_still_report_by_category` | AC-7 | MT-06 |
| `test_journal_migration.py::test_the_table_is_still_append_only_after_widening` | AC-9 | MT-06 |
| `test_journal_migration.py::test_creating_the_schema_twice_is_safe` | AC-9 | MT-06 |
| `test_journal_migration.py::test_new_rows_land_classified_in_the_widened_table` | AC-1, AC-9 | MT-06 |
