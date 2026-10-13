"""The Options Strategist: whitelist, loop, submissions, statuses."""

from __future__ import annotations

import json

import pytest

from strike_desk.journal import SCHEMA_VERSION
from strike_desk.mcp_toolbox import (
    FORBIDDEN_PREFIXES,
    REGIME_TOOLS,
    REQUIRED_TOOLS,
    STRATEGIST_TOOLS,
    _assert_read_only,
    tools_for_role,
)
from strike_desk.options_strategist import (
    STATUS_DEGRADED,
    STATUS_INVALID,
    STATUS_NO_CONTRACT,
    STATUS_PROPOSED,
    STATUS_UNGROUNDED,
    SUBMIT_NO_CONTRACT,
    SUBMIT_PROPOSAL,
    VERDICT_FAIL,
    VERDICT_NA,
    VERDICT_PASS,
    build_proposal_row,
)
from strike_desk.specialists import ROLE_REGIME, ROLE_STRATEGIST
from tests.chain_fixtures import SYMBOL, proposal_args

#: Tools OpenAlgo's MCP server really exposes on the same session the desk holds open.
MUTATING_TOOLS = (
    "place_order",
    "placeorder",
    "place_options_order",
    "place_options_multi_order",
    "modifyorder",
    "cancelorder",
    "cancel_all_orders",
    "close_all_positions",
    "square_off_position",
    "send_telegram_alert",
    "set_analyzer_mode",
)


def test_every_whitelisted_tool_is_a_reader() -> None:
    for name in REQUIRED_TOOLS:
        assert name.startswith("get_"), name
        assert not name.startswith(FORBIDDEN_PREFIXES), name


@pytest.mark.parametrize("name", MUTATING_TOOLS)
def test_no_whitelist_holds_a_mutating_tool(name: str) -> None:
    assert name not in REGIME_TOOLS
    assert name not in STRATEGIST_TOOLS
    with pytest.raises(ValueError, match="must be read-only"):
        _assert_read_only((*STRATEGIST_TOOLS, name))


def test_the_strategist_gets_the_option_tools_and_the_analyst_does_not() -> None:
    for name in ("get_option_chain", "get_option_symbol", "get_option_greeks"):
        assert name in STRATEGIST_TOOLS
        assert name not in REGIME_TOOLS


def test_the_bound_list_is_what_the_model_sees(fake_tools) -> None:
    """The guardrail must hold at the binding, not only in the constant."""
    bound = tools_for_role(fake_tools, ROLE_STRATEGIST)
    names = {tool.name for tool in bound}
    assert names == set(STRATEGIST_TOOLS)
    assert not any(name.startswith(FORBIDDEN_PREFIXES) for name in names)

    analyst_names = {tool.name for tool in tools_for_role(fake_tools, ROLE_REGIME)}
    assert analyst_names == set(REGIME_TOOLS)
    assert "get_option_chain" not in analyst_names


def _read_rounds() -> list[dict]:
    """The tool calls a competent strategist makes before it submits."""
    return [
        {"name": "get_expiry_dates", "args": {"symbol": "NIFTY"}, "id": "c1"},
        {"name": "get_trend_snapshot", "args": {"symbol": "NIFTY"}, "id": "c2"},
        {"name": "get_momentum_snapshot", "args": {"symbol": "NIFTY"}, "id": "c3"},
        {"name": "get_option_chain", "args": {"symbol": "NIFTY", "strike_count": 7}, "id": "c4"},
        {"name": "get_option_symbol", "args": {"offset": "ATM"}, "id": "c5"},
        {"name": "get_option_greeks", "args": {"symbol": SYMBOL}, "id": "c6"},
    ]


def test_a_grounded_proposal_passes_the_playbook(strategist_harness) -> None:
    result = strategist_harness.run(
        rounds=[_read_rounds(), [{"name": SUBMIT_PROPOSAL, "args": proposal_args(), "id": "s"}]]
    )
    payload = result.payload
    assert payload["status"] == STATUS_PROPOSED
    assert payload["playbook_verdict"] == VERDICT_PASS
    assert payload["violations"] == []
    assert payload["defect"] is None
    assert payload["proposal"]["symbol"] == SYMBOL
    assert payload["rejected_tool_count"] == 0
    assert payload["tool_call_count"] == 6


def test_a_refusal_is_a_first_class_answer(strategist_harness) -> None:
    """AC-4: no-contract is not an error, carries no defect, and has no verdict."""
    refusal = {
        "reason": "The 24800 CE quotes 189.5 to 190.5, but every other strike inside the "
        "delta band shows open interest of 1120400, under the 50,000 floor per leg I need.",
        "evidence": [{"tool": "get_option_chain", "field": "oi", "value": "1120400"}],
    }
    result = strategist_harness.run(
        rounds=[_read_rounds(), [{"name": SUBMIT_NO_CONTRACT, "args": refusal, "id": "s"}]]
    )
    assert result.payload["status"] == STATUS_NO_CONTRACT
    assert result.payload["playbook_verdict"] == VERDICT_NA
    assert result.payload["defect"] is None
    assert result.payload["proposal"] is None


def test_a_refusal_may_name_the_band_it_failed(strategist_harness) -> None:
    """The playbook's constants are things the desk gave the agent, so citing one is
    grounded. Without ledger.record_playbook this test fails and refusals become useless."""
    refusal = {
        "reason": "Nothing qualifies: the 0.35 to 0.60 delta band contains only strikes "
        "whose open interest of 1120400 sits under the 50,000 floor.",
        "evidence": [{"tool": "get_option_chain", "field": "oi", "value": "1120400"}],
    }
    result = strategist_harness.run(
        rounds=[_read_rounds(), [{"name": SUBMIT_NO_CONTRACT, "args": refusal, "id": "s"}]]
    )
    assert result.payload["status"] == STATUS_NO_CONTRACT
    assert result.payload["defect"] is None


def test_the_playbook_seed_does_not_loosen_contract_grounding(strategist_harness) -> None:
    """Seeding the constraints must not let the chain itself be invented."""
    result = strategist_harness.run(
        rounds=[
            _read_rounds(),
            [{"name": SUBMIT_PROPOSAL, "args": proposal_args(open_interest=1_999_999), "id": "s"}],
        ]
    )
    assert result.payload["status"] == STATUS_UNGROUNDED
    assert "open_interest" in result.payload["defect"]


def test_an_invented_number_is_ungrounded(strategist_harness) -> None:
    """AC-2: the rationale may cite nothing the ledger did not observe."""
    args = proposal_args(
        rationale="The 24800 CE is cheap with IV at 41.7 and ADX 27.4 confirming the trend."
    )
    result = strategist_harness.run(
        rounds=[_read_rounds(), [{"name": SUBMIT_PROPOSAL, "args": args, "id": "s"}]]
    )
    assert result.payload["status"] == STATUS_UNGROUNDED
    assert "41.7" in result.payload["defect"]


def test_an_invented_contract_is_ungrounded(strategist_harness) -> None:
    """A correct sentence over an invented contract is still ungrounded."""
    result = strategist_harness.run(
        rounds=[
            _read_rounds(),
            [
                {
                    "name": SUBMIT_PROPOSAL,
                    "args": proposal_args(symbol="NIFTY02SEP2699999CE"),
                    "id": "s",
                }
            ],
        ]
    )
    assert result.payload["status"] == STATUS_UNGROUNDED
    assert "symbol" in result.payload["defect"]


def test_a_persuasive_proposal_that_fails_arithmetic_is_a_defect(strategist_harness) -> None:
    """AC-3: the model's confidence has no standing against the playbook."""
    result = strategist_harness.run(
        rounds=[
            _read_rounds(),
            [{"name": SUBMIT_PROPOSAL, "args": proposal_args(breakeven=24812.35), "id": "s"}],
        ]
    )
    payload = result.payload
    assert payload["status"] == STATUS_INVALID
    assert payload["playbook_verdict"] == VERDICT_FAIL
    assert any("breakeven" in violation for violation in payload["violations"])
    assert payload["proposal"]["symbol"] == SYMBOL  # the contract is kept, not discarded


def test_a_rejected_tool_is_counted_and_never_executed(strategist_harness) -> None:
    """AC-5 at runtime: an attempt past the whitelist is data, not an order."""
    result = strategist_harness.run(
        rounds=[
            [{"name": "place_options_order", "args": {"symbol": SYMBOL}, "id": "bad"}],
            _read_rounds(),
            [{"name": SUBMIT_PROPOSAL, "args": proposal_args(), "id": "s"}],
        ]
    )
    assert result.payload["rejected_tool_count"] == 1
    assert result.payload["status"] == STATUS_PROPOSED
    assert strategist_harness.executed_tools() == [call["name"] for call in _read_rounds()]


def test_no_submission_after_a_live_chain_is_atm_ce(strategist_harness) -> None:
    """Prove overlay: six silent rounds with a live chain become current-week ATM CE."""
    result = strategist_harness.run(rounds=[_read_rounds()] * 6)
    payload = result.payload
    assert payload["status"] == STATUS_PROPOSED
    assert payload["proposal"]["option_type"] == "CE"
    assert payload["proposal"]["strike"] == 24800
    assert payload["proposal"]["lots"] == 1
    assert payload["defect"] is None


def test_last_round_prose_after_a_chain_is_atm_not_chain_unread(strategist_harness) -> None:
    result = strategist_harness.run(rounds=[_read_rounds()])
    payload = result.payload
    assert payload["status"] == STATUS_PROPOSED
    assert payload["proposal"]["symbol"] == SYMBOL


def test_bearish_rationale_selects_atm_pe(strategist_harness) -> None:
    from tests.chain_fixtures import option_greeks_pe

    for tool in strategist_harness.source.tools(""):
        if tool.name == "get_option_greeks":
            tool.output = option_greeks_pe()
    result = strategist_harness.run(
        rounds=[_read_rounds()],
        regime_rationale="bearish. Daily -DI led +DI and ADX 33.46 confirms the down move.",
    )
    payload = result.payload
    assert payload["status"] == STATUS_PROPOSED
    assert payload["proposal"]["option_type"] == "PE"
    assert payload["proposal"]["direction"] == "bearish"
    assert payload["proposal"]["symbol"].endswith("PE")


def test_chain_without_greeks_is_no_contract_not_chain_unread(strategist_harness) -> None:
    for tool in strategist_harness.source.tools(""):
        if tool.name == "get_option_greeks":
            tool.raises = True
    result = strategist_harness.run(
        rounds=[[{"name": "get_option_chain", "args": {"symbol": "NIFTY"}, "id": "c4"}]]
    )
    assert result.payload["status"] == STATUS_NO_CONTRACT
    assert "greeks" in result.payload["rationale"]


def test_no_chain_read_stays_degraded_as_no_submission(strategist_harness) -> None:
    for tool in strategist_harness.source.tools(""):
        if tool.name in {
            "get_option_chain",
            "get_option_greeks",
            "get_option_symbol",
            "get_expiry_dates",
        }:
            tool.raises = True
    result = strategist_harness.run(
        rounds=[[{"name": "get_trend_snapshot", "args": {"symbol": "NIFTY"}, "id": "c2"}]]
    )
    assert result.payload["status"] == STATUS_DEGRADED
    assert "no submission" in result.payload["defect"]


def test_closed_session_retries_then_proposes(strategist_harness) -> None:
    from strike_desk.errors import McpSessionClosed

    source = strategist_harness.source
    attempts = {"n": 0}
    original_submit = source.submit

    def submit(factory, timeout):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise McpSessionClosed("MCP session closed during a tool call")
        return original_submit(factory, timeout)

    broken = {"n": 0}
    source.submit = submit  # type: ignore[method-assign]
    source.mark_broken = lambda: broken.__setitem__("n", broken["n"] + 1)  # type: ignore[method-assign]

    result = strategist_harness.run(
        rounds=[_read_rounds(), [{"name": SUBMIT_PROPOSAL, "args": proposal_args(), "id": "s"}]]
    )
    assert attempts["n"] == 2
    assert broken["n"] == 1
    assert result.payload["status"] == STATUS_PROPOSED


def test_model_failure_still_proposes_atm_from_python_chain(strategist_harness) -> None:
    class Boom:
        async def ainvoke(self, *_args, **_kwargs):
            raise RuntimeError("credit balance is too low")

        def bind_tools(self, *_args, **_kwargs):
            return self

    strategist_harness.strategist._model = Boom()  # type: ignore[method-assign]
    result = strategist_harness.run(rounds=[])
    payload = result.payload
    assert payload["status"] == STATUS_PROPOSED
    assert payload["proposal"]["option_type"] == "CE"
    assert payload["proposal"]["lots"] == 1


def test_every_tool_failing_is_degraded_not_ungrounded(strategist_harness) -> None:
    """A dead broker session must not look like a reasoning defect."""
    result = strategist_harness.run(rounds=[_read_rounds()], all_tools_fail=True)
    assert result.payload["status"] == STATUS_DEGRADED
    assert "every tool call failed" in result.payload["defect"]


def test_the_row_carries_every_field_ac1_requires(strategist_harness, settings, prompts) -> None:
    result = strategist_harness.run(
        rounds=[_read_rounds(), [{"name": SUBMIT_PROPOSAL, "args": proposal_args(), "id": "s"}]]
    )
    row = build_proposal_row(
        result.payload,
        settings=settings,
        prompts=prompts,
        tick_id="tick-1",
        trace_id="0" * 32,
        trading_day="2026-08-25",
        source="tick",
        regime_label="trending",
        regime_confidence=0.78,
    )
    for field in (
        "index_symbol",
        "expiry",
        "strike",
        "option_type",
        "symbol",
        "lots",
        "lot_size",
        "quantity",
        "entry_price_low",
        "entry_price_high",
        "breakeven",
        "stop_price",
        "target_price",
        "time_stop_ist",
        "theta_per_day",
        "rationale",
        "evidence_json",
        "playbook_artifact",
        "playbook_verdict",
        "violations_json",
        "token_cost_micros",
    ):
        assert row[field] is not None, field
    assert row["schema_version"] == SCHEMA_VERSION


def test_the_spans_carry_what_ac9_requires(strategist_harness, journal) -> None:
    strategist_harness.run(
        rounds=[_read_rounds(), [{"name": SUBMIT_PROPOSAL, "args": proposal_args(), "id": "s"}]]
    )
    names = {span.name for span in journal.spans_for_trace(strategist_harness.trace_id)}
    assert {"strategy.propose", "strategy.model_call", "strategy.tool_call"} <= names

    root = next(
        s
        for s in journal.spans_for_trace(strategist_harness.trace_id)
        if s.name == "strategy.propose"
    )
    attributes = json.loads(root.attributes_json)
    for key in (
        "strategy.status",
        "strategy.symbol",
        "strategy.playbook_verdict",
        "strategy.tool_calls",
        "strategy.rejected_tools",
        "strategy.token_cost_micros",
        "playbook.artifact",
    ):
        assert key in attributes, key


def test_atm_overlay_reads_a_wrapped_mcp_chain(settings) -> None:
    from datetime import datetime

    from strike_desk.config import IST
    from strike_desk.grounding import EvidenceLedger, ToolObservation
    from strike_desk.options_strategist import try_atm_overlay
    from strike_desk.playbook import Playbook
    from strike_desk.specialists import SpecialistRequest
    from tests.chain_fixtures import option_chain, option_greeks

    inner = option_chain()
    wrapped = str([{"type": "text", "text": inner}])
    ledger = EvidenceLedger()
    ledger.record(
        ToolObservation(
            call_id="c",
            tool="get_option_chain",
            args={},
            output=wrapped,
            ok=True,
            latency_ms=1,
            truncated=False,
        )
    )
    ledger.record(
        ToolObservation(
            call_id="g",
            tool="get_option_greeks",
            args={},
            output=option_greeks(),
            ok=True,
            latency_ms=1,
            truncated=False,
        )
    )
    request = SpecialistRequest(
        tick_id="t",
        index_symbol="NIFTY",
        as_of=datetime(2026, 8, 25, 6, 0, tzinfo=IST),
        book={"regime": {"rationale": "ADX at 27.4 confirms the move."}},
    )
    overlay = try_atm_overlay(
        ledger,
        request,
        Playbook.from_settings(settings),
        now_ist=datetime(2026, 8, 25, 11, 30, tzinfo=IST),
        index_symbol="NIFTY",
    )
    assert overlay is not None
    assert overlay.option_type == "CE"
    assert overlay.strike == 24800
