"""Guardrails: no order path, no entry, no secrets in the record."""

from __future__ import annotations

import itertools
import json

import pytest

from strike_desk.errors import ReadOnlyViolation
from strike_desk.graph import (
    OUTCOME_DECLINE,
    OUTCOME_HOLD,
    TRADEABLE_REGIMES,
    _decide_outcome,
)
from strike_desk.openalgo_client import READ_ONLY_PATHS
from tests.conftest import API_KEY, StubSpecialist


def test_read_only_whitelist_is_exactly_the_four_read_paths():
    assert READ_ONLY_PATHS == {
        "/api/v1/ping",
        "/api/v1/funds",
        "/api/v1/positionbook",
        "/api/v1/market/timings",
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/placeorder",
        "/api/v1/placesmartorder",
        "/api/v1/optionsorder",
        "/api/v1/basketorder",
        "/api/v1/closeposition",
        "/api/v1/modifyorder",
    ],
)
def test_order_paths_are_unreachable(client, path):
    with pytest.raises(ReadOnlyViolation):
        client._post(path)  # noqa: SLF001


def test_no_state_can_produce_an_entry(settings):
    """Exhaustive sweep of the decision table: `enter` is unreachable in this slice."""
    labels = [*TRADEABLE_REGIMES, "event-driven", "high-volatility", "unknown", "nonsense"]
    books = [None, {"open_positions": []}, {"open_positions": [{"symbol": "NIFTY...CE"}]}]
    errors = [
        None,
        {"role": "regime", "kind": "timeout", "detail": "x"},
        {"role": "regime", "kind": "unavailable", "detail": "x"},
    ]
    for label, book, error, confidence, budget, book_error in itertools.product(
        labels, books, errors, [0.0, 0.54, 0.55, 1.0], [False, True], [None, "boom"]
    ):
        state = {
            "budget_exceeded": budget,
            "book_error": book_error,
            "book": book,
            "specialist_error": error,
            "regime_label": label,
            "regime_confidence": confidence,
        }
        outcome, reason_code, reason_text = _decide_outcome(state, settings)
        assert outcome in {OUTCOME_DECLINE, OUTCOME_HOLD}, (state, outcome)
        assert reason_code and reason_text


def test_api_key_never_reaches_the_journal_or_traces(runner, journal, today):
    runner.run_tick("schedule")
    with journal.session_scope() as session:
        from sqlalchemy import select

        from strike_desk.journal import Decision, TraceSpan

        blob = json.dumps(
            [
                [row.book_state_json, row.reason_text]
                for row in session.execute(select(Decision)).scalars()
            ]
            + [span.attributes_json for span in session.execute(select(TraceSpan)).scalars()]
        )
    assert API_KEY not in blob


def test_redactor_removes_exact_secrets_and_sensitive_keys():
    from strike_desk.observability import REDACTED, Redactor

    redactor = Redactor([API_KEY])
    payload = {"apikey": API_KEY, "note": f"called with {API_KEY}", "symbol": "NIFTY28JUL2624500CE"}
    cleaned = redactor(payload)
    assert cleaned["apikey"] == REDACTED
    assert API_KEY not in cleaned["note"]
    assert cleaned["symbol"] == "NIFTY28JUL2624500CE"  # no false positives


def test_a_registered_regime_specialist_still_cannot_cause_an_entry(runner, deps, journal, today):
    deps.registry.register(
        StubSpecialist(payload={"label": "trending", "confidence": 1.0}, model_version="stub")
    )
    runner.run_tick("schedule")
    assert journal.list_decisions(today)[0].outcome == "decline"
