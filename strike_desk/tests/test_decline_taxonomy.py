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
    OUTCOMES,
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

#: Every code dt-1 shipped, with the class it shipped with. These may never change.
DT1_CLASSES = {
    "position-open": ("book", "routine"),
    "data-quality": ("data", "degraded"),
    "specialist-unavailable": ("specialist", "degraded"),
    "specialist-timeout": ("specialist", "degraded"),
    "regime-not-tradeable": ("regime", "routine"),
    "regime-low-confidence": ("regime", "routine"),
    "regime-ungrounded": ("regime", "defect"),
    "tick-timeout": ("system", "degraded"),
    "internal-error": ("system", "defect"),
}


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
    assert item.outcome in OUTCOMES
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
    assert (
        render("regime-not-tradeable", max_chars=tight, label="unknown", rationale="z" * 200)
        == verdict
    )


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
    assert TAXONOMY_VERSION == "dt-5"


#: Every code dt-1 and dt-2 shipped, with the class it shipped with. These may never change.
FROZEN_CLASSES = {
    "position-open": ("book", "routine"),
    "data-quality": ("data", "degraded"),
    "specialist-unavailable": ("specialist", "degraded"),
    "specialist-timeout": ("specialist", "degraded"),
    "regime-not-tradeable": ("regime", "routine"),
    "regime-low-confidence": ("regime", "routine"),
    "regime-ungrounded": ("regime", "defect"),
    "tick-timeout": ("system", "degraded"),
    "internal-error": ("system", "defect"),
    "no-viable-contract": ("contract", "routine"),
    "proposal-ungrounded": ("contract", "defect"),
    "proposal-invalid": ("contract", "defect"),
    "risk-session-stopped": ("risk", "routine"),
    "risk-input-unavailable": ("risk", "degraded"),
    "risk-veto": ("risk", "routine"),
    "risk-cleared": ("risk", "routine"),
}


@pytest.mark.parametrize(("code", "expected"), sorted(FROZEN_CLASSES.items()))
def test_the_taxonomy_is_additive(code: str, expected: tuple[str, str]) -> None:
    """An older row must never become taxonomy drift because we shipped dt-4."""
    found = describe(code)
    assert (found.category, found.disposition) == expected


def test_the_outcome_set_gained_enter_and_nothing_else() -> None:
    assert OUTCOMES == {"decline", "hold", "enter"}
    assert describe("risk-cleared").outcome == "enter"
    assert {entry.outcome for entry in REASONS.values() if entry.code != "risk-cleared"} == {
        "decline",
        "hold",
    }


def test_an_entry_never_flips_the_exit_code(journal) -> None:
    """AC-10. A cleared intent is routine; only defects change what `declines` returns."""
    from strike_desk.decline_report import build_day_report, build_window_report, render_window
    from tests.journal_fixtures import seed_decision

    seed_decision(journal, trading_day="2026-09-01", reason_code="risk-cleared")
    report = build_day_report(journal, "2026-09-01", "NIFTY")
    assert (report.entries, report.declines, report.defects) == (1, 0, 0)
    assert report.healthy is True
    assert report.drift == 0
    assert "entries" in render_window(build_window_report(journal, "NIFTY", ["2026-09-01"]))


def test_dt1_default_sentences_are_unchanged() -> None:
    """Adding a variant may not alter the sentence an existing row already reads as."""
    for case in GOLDEN["cases"]:
        if case["code"] not in DT1_CLASSES or case["variant"] != "default":
            continue
        assert render(case["code"], max_chars=400, **case["fields"]) == case["expect"]
