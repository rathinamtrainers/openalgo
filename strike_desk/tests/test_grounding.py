"""Grounding: the ledger, the tolerance, and the refusal."""

from __future__ import annotations

import pytest

from strike_desk.grounding import (
    EvidenceLedger,
    RegimeSubmission,
    ToolObservation,
    extract_numbers,
    validate_proposal,
    validate_submission,
)


def observation(tool: str, output: str, ok: bool = True, args=None) -> ToolObservation:
    return ToolObservation(
        call_id="c1",
        tool=tool,
        args=args or {"symbol": "NIFTY"},
        output=output,
        ok=ok,
        latency_ms=12,
        truncated=False,
    )


def ledger_with(*observations) -> EvidenceLedger:
    ledger = EvidenceLedger()
    for item in observations:
        ledger.record(item)
    return ledger


def submission(rationale: str, evidence=None, label: str = "trending") -> RegimeSubmission:
    return RegimeSubmission(
        label=label,
        confidence=0.72,
        rationale=rationale,
        evidence=evidence or [{"tool": "get_trend_snapshot", "field": "adx_di", "value": "31.7"}],
    )


def test_extract_numbers_handles_separators_and_decimals():
    assert extract_numbers('{"ltp": 24,512.35, "oi": 1200}') == {24512.35, 1200.0}


def test_grounded_rationale_passes():
    ledger = ledger_with(observation("get_trend_snapshot", '{"adx_di": [22.4, 18.1, 31.7]}'))
    assert validate_submission(submission("ADX at 31.7 confirms the move."), ledger, 320) is None


def test_rounding_at_the_cited_precision_passes():
    ledger = ledger_with(observation("get_trend_snapshot", '{"adx_di": [22.4, 18.1, 31.74]}'))
    assert validate_submission(submission("ADX reads 31.7."), ledger, 320) is None


def test_invented_number_fails():
    ledger = ledger_with(observation("get_trend_snapshot", '{"adx_di": [22.4, 18.1, 31.7]}'))
    defect = validate_submission(submission("ADX at 38.2 confirms the move."), ledger, 320)
    assert defect is not None and "38.2" in defect


def test_number_from_a_failed_call_is_not_grounded():
    ledger = ledger_with(observation("get_quote", '{"ltp": 24512.35}', ok=False))
    defect = validate_submission(submission("Spot at 24512.35."), ledger, 320)
    assert defect is not None


def test_evidence_from_an_uncalled_tool_fails():
    ledger = ledger_with(observation("get_quote", '{"ltp": 24512.35}'))
    defect = validate_submission(
        submission(
            "Spot at 24512.35.",
            evidence=[{"tool": "get_option_chain", "field": "pcr", "value": "1.2"}],
        ),
        ledger,
        320,
    )
    assert defect is not None and "get_option_chain" in defect


def test_ungrounded_evidence_value_fails():
    ledger = ledger_with(observation("get_quote", '{"ltp": 24512.35}'))
    defect = validate_submission(
        submission(
            "Spot is holding above its open.",
            evidence=[{"tool": "get_quote", "field": "ltp", "value": "24999.00"}],
        ),
        ledger,
        320,
    )
    assert defect is not None and "get_quote.ltp" in defect


def test_overlong_rationale_fails():
    ledger = ledger_with(observation("get_quote", '{"ltp": 24512.35}'))
    defect = validate_submission(submission("word " * 200), ledger, 320)
    assert defect is not None and "cap" in defect


@pytest.mark.parametrize("label", ["trend", "TRENDING", "bullish", ""])
def test_labels_outside_the_fixed_set_are_rejected_by_the_schema(label):
    with pytest.raises(ValueError):
        RegimeSubmission(label=label, confidence=0.5, rationale="a" * 20, evidence=[])


def test_confidence_outside_zero_to_one_is_rejected():
    with pytest.raises(ValueError):
        RegimeSubmission(
            label="trending",
            confidence=1.4,
            rationale="a" * 20,
            evidence=[{"tool": "t", "field": "f", "value": "1"}],
        )


def test_a_recorded_proposal_is_grounded():
    from tests.chain_fixtures import grounded_ledger, valid_proposal

    assert validate_proposal(valid_proposal(), grounded_ledger(), 700) is None


def test_an_invented_structured_field_is_ungrounded():
    from tests.chain_fixtures import grounded_ledger, valid_proposal

    defect = validate_proposal(valid_proposal(open_interest=1_999_999), grounded_ledger(), 700)
    assert defect is not None and "open_interest" in defect


def test_an_invented_symbol_is_ungrounded():
    from tests.chain_fixtures import grounded_ledger, valid_proposal

    defect = validate_proposal(
        valid_proposal(symbol="NIFTY02SEP2699999CE"), grounded_ledger(), 700
    )
    assert defect is not None and "symbol" in defect
