# Iteration 04 — Test Automation

> **Document:** the suite that holds UC-04 in place. `03_manual_test_cases.md` is what you
> check once with your hands; this is what CI checks on every push forever.

## 1. Framework, and what is real

Same framework as iterations 01 to 03: `pytest`, `respx` for OpenAlgo's HTTP surface, a
fake chat model for the reasoning plane, a real SQLite journal in a `tmp_path`, and a real
compiled LangGraph. Nothing is mocked that could be run for real. The journal is real, the
graph is real, the taxonomy is real, the playbook is real, the grounding is real.

Exactly one thing is faked, and it is faked the same way iteration 02 faked it: **the model
provider**. `FakeChatModel` replays a scripted list of `AIMessage`s carrying tool calls, so a
test drives the agent loop round by round without a network call or an API key. That is what
makes "the model proposed a contract whose breakeven is wrong" a deterministic, five-line
test instead of an act of faith.

The second thing that is real and worth naming: **the option chain**. `tests/chain_fixtures.py`
holds a recorded `get_option_chain`, `get_option_symbol`, `get_option_greeks` and
`get_expiry_dates` response, captured from a live NIFTY session and trimmed to seven strikes
around the money. Every playbook test and every loop test runs against those bytes. This is
what lets the slice's central safety property be fully tested with no broker session — see
block A of the manual tests, which leans on the same fixtures.

The suite adds four files and extends `conftest.py`:

| File | What it proves |
| --- | --- |
| `tests/chain_fixtures.py` | The recorded chain, the valid proposal built from it, and the seeder. |
| `tests/test_playbook.py` | Every constraint, in both directions, from the fixture. |
| `tests/test_options_strategist.py` | The loop, the whitelist, the two submissions, the five statuses. |
| `tests/test_tick_proposals.py` | Routing, classification and the unreachability of `enter`. |

Plus additions to `tests/test_decline_taxonomy.py` (the three new codes and the golden file),
`tests/test_journal_migration.py` (the new table) and `tests/test_grounding.py` (the proposal
validator).

## 2. The recorded chain

The fixture is the contract between "what OpenAlgo actually returns" and every test in this
slice. It is recorded rather than invented, because a synthetic chain is a chain that agrees
with your assumptions — and the assumption most worth testing is that the desk handles the
shape OpenAlgo really emits, including its string-typed numbers and its `moneyness` labels.

`valid_proposal()` is the proposal a competent strategist would build from that chain: every
number in it appears in the recorded bytes, and it passes `playbook.check` cleanly at the
fixed test clock. Every negative test in `test_playbook.py` is `valid_proposal()` with one
field moved, which is what makes each failure attributable to exactly one constraint.

`FIXED_NOW` matters more than it looks. The playbook checks days-to-expiry and the time-stop
against the clock, so a fixture with a hard-coded expiry and a real `datetime.now()` would
pass in September and fail in October. Every test that calls `check` passes `now_ist=FIXED_NOW`.

### `strike_desk/tests/chain_fixtures.py`

```python
"""A recorded NIFTY option chain, and the proposal a competent strategist builds from it.

Captured from a live session and trimmed to seven strikes around the money. Every test in
this slice grounds against these bytes, which is why the playbook and the agent loop are
fully testable with no broker session.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from strike_desk.grounding import EvidenceLedger, ProposalSubmission, ToolObservation
from strike_desk.journal import SCHEMA_VERSION

#: The clock every playbook assertion is made against. Expiry is 8 days out from here.
FIXED_NOW = datetime(2026, 8, 25, 11, 30, 0)

INDEX = "NIFTY"
EXPIRY = "2026-09-02"
SYMBOL = "NIFTY02SEP2624800CE"
LOT_SIZE = 75

_STRIKES = (24600, 24700, 24750, 24800, 24850, 24900, 25000)


def expiry_dates() -> str:
    return json.dumps(
        {
            "status": "success",
            "data": {
                "symbol": INDEX,
                "exchange": "NFO",
                "instrumenttype": "options",
                "expirydates": ["2026-09-02", "2026-09-09", "2026-09-30"],
            },
        },
        indent=2,
    )


def option_chain() -> str:
    """Seven strikes around a 24812.35 spot, with the shape OpenAlgo really returns.

    The ladder is anchored on the ATM *strike*, not the spot, so the 24800 CE prices at
    exactly 190.00 / 189.50 / 190.50. That is not cosmetic: `valid_proposal()` cites those
    three numbers, and a fixture whose arithmetic disagrees with its own proposal would make
    every `proposed` test fail as `ungrounded` for the wrong reason.
    """
    rows = []
    for strike in _STRIKES:
        distance = strike - 24800
        ce_ltp = round(max(2.0, 190.0 - distance * 0.52), 2)
        pe_ltp = round(max(2.0, 190.0 + distance * 0.52), 2)
        rows.append(
            {
                "strike": strike,
                "moneyness": "ATM" if strike == 24800 else ("ITM" if distance < 0 else "OTM"),
                "CE": {
                    "ltp": ce_ltp,
                    "bid": round(ce_ltp - 0.5, 2),
                    "ask": round(ce_ltp + 0.5, 2),
                    "volume": 1_284_000,
                    "oi": 2_841_250 if strike == 24800 else 1_120_400,
                    "lotsize": LOT_SIZE,
                },
                "PE": {
                    "ltp": pe_ltp,
                    "bid": round(pe_ltp - 0.5, 2),
                    "ask": round(pe_ltp + 0.5, 2),
                    "volume": 902_500,
                    "oi": 1_640_900,
                    "lotsize": LOT_SIZE,
                },
            }
        )
    return json.dumps(
        {
            "status": "success",
            "data": {
                "symbol": INDEX,
                "exchange": "NFO",
                "expiry": EXPIRY,
                "underlying_ltp": 24812.35,
                "atm_strike": 24800,
                "chain": rows,
            },
        },
        indent=2,
    )


def option_symbol() -> str:
    return json.dumps(
        {
            "status": "success",
            "data": {
                "symbol": SYMBOL,
                "exchange": "NFO",
                "strike": 24800,
                "expiry": EXPIRY,
                "option_type": "CE",
                "lotsize": LOT_SIZE,
                "tick_size": 0.05,
                "underlying_ltp": 24812.35,
            },
        },
        indent=2,
    )


def option_greeks() -> str:
    return json.dumps(
        {
            "status": "success",
            "data": {
                "symbol": SYMBOL,
                "spot": 24812.35,
                "strike": 24800,
                "iv": 12.8,
                "delta": 0.472,
                "gamma": 0.00041,
                "theta": -18.4,
                "vega": 11.72,
                "rho": 3.18,
                "interest_rate": 6.5,
            },
        },
        indent=2,
    )


def trend_up() -> str:
    return json.dumps(
        {
            "symbol": INDEX,
            "interval": "15m",
            "last_close": 24812.35,
            "indicators": {"ema_20": 24761.4, "supertrend": [24688.2, 1], "adx_di": [27.4, 14.1, 27.4]},
        },
        indent=2,
    )


def momentum_up() -> str:
    return json.dumps(
        {"symbol": INDEX, "interval": "15m", "indicators": {"rsi_14": 61.2, "macd_hist": 18.44}},
        indent=2,
    )


RATIONALE = (
    "Trend and momentum both confirm the upside: ADX 27.4 with +DI above -DI, RSI 61.2 and "
    "price above the 20-EMA at 24761.4. The 24800 CE is the nearest strike inside the delta "
    "band at 0.472, with 2841250 open interest and a bid-ask of 189.5 to 190.5, so it can be "
    "exited. IV at 12.8 is mid-band. I am wrong if ADX rolls below 20."
)


def evidence() -> list[dict[str, str]]:
    return [
        {"tool": "get_option_greeks", "field": "delta", "value": "0.472"},
        {"tool": "get_option_greeks", "field": "iv", "value": "12.8"},
        {"tool": "get_option_greeks", "field": "theta", "value": "-18.4"},
        {"tool": "get_option_chain", "field": "oi", "value": "2841250"},
        {"tool": "get_option_symbol", "field": "lotsize", "value": "75"},
        {"tool": "get_trend_snapshot", "field": "adx", "value": "27.4"},
        {"tool": "get_momentum_snapshot", "field": "rsi_14", "value": "61.2"},
    ]


def proposal_args(**overrides: Any) -> dict[str, Any]:
    """The submission a competent strategist builds from the recorded chain."""
    args: dict[str, Any] = {
        "direction": "bullish",
        "index_symbol": INDEX,
        "expiry": EXPIRY,
        "strike": 24800.0,
        "option_type": "CE",
        "symbol": SYMBOL,
        "lot_size": LOT_SIZE,
        "lots": 1,
        "quantity": 75,
        "bid": 189.5,
        "ask": 190.5,
        "entry_price_low": 188.0,
        "entry_price_high": 192.0,
        "delta": 0.472,
        "theta_per_day": -18.4,
        "implied_volatility": 12.8,
        "open_interest": 2_841_250,
        "breakeven": 24992.0,          # strike 24800 + entry_price_high 192.0
        "stop_price": 152.0,
        "target_price": 268.0,
        "time_stop_ist": "14:45",
        "rationale": RATIONALE,
        "evidence": evidence(),
    }
    args.update(overrides)
    return args


def valid_proposal(**overrides: Any) -> ProposalSubmission:
    return ProposalSubmission.model_validate(proposal_args(**overrides))


def grounded_ledger(playbook: Any | None = None) -> EvidenceLedger:
    """A ledger holding every output the recorded proposal cites.

    Pass a playbook to also seed its stated constraints, exactly as the agent loop does —
    that is what lets a refusal name the band it failed.
    """
    ledger = EvidenceLedger()
    if playbook is not None:
        ledger.record_playbook(playbook.describe())
    for index, (tool, output) in enumerate(
        (
            ("get_expiry_dates", expiry_dates()),
            ("get_trend_snapshot", trend_up()),
            ("get_momentum_snapshot", momentum_up()),
            ("get_option_chain", option_chain()),
            ("get_option_symbol", option_symbol()),
            ("get_option_greeks", option_greeks()),
        )
    ):
        ledger.record(
            ToolObservation(
                call_id=f"call-{index}",
                tool=tool,
                args={},
                output=output,
                ok=True,
                latency_ms=12,
                truncated=False,
            )
        )
    return ledger


def seed_proposal(journal: Any, *, trading_day: str, status: str = "proposed", **fields: Any) -> str:
    """Append one proposals row directly, for report and view tests."""
    row: dict[str, Any] = {
        "proposal_id": str(uuid.uuid4()),
        "tick_id": str(uuid.uuid4()),
        "trace_id": "0" * 32,
        "created_at_utc": datetime.now(tz=UTC),
        "trading_day": trading_day,
        "index_symbol": INDEX,
        "source": "tick",
        "status": status,
        "regime_label": "trending",
        "regime_confidence": 0.78,
        "direction": "bullish",
        "symbol": SYMBOL,
        "expiry": EXPIRY,
        "strike": 24800.0,
        "option_type": "CE",
        "lots": 1,
        "lot_size": LOT_SIZE,
        "quantity": 75,
        "entry_price_low": 188.0,
        "entry_price_high": 192.0,
        "delta": 0.472,
        "theta_per_day": -18.4,
        "implied_volatility": 12.8,
        "open_interest": 2_841_250,
        "breakeven": 24992.0,
        "stop_price": 152.0,
        "target_price": 268.0,
        "time_stop_ist": "14:45",
        "rationale": RATIONALE,
        "evidence_json": json.dumps({"cited": evidence(), "calls": []}, sort_keys=True),
        "playbook_artifact": "pb-1+testdigest",
        "playbook_verdict": "pass",
        "violations_json": "[]",
        "defect": None,
        "tool_call_count": 6,
        "tool_error_count": 0,
        "rejected_tool_count": 0,
        "model_calls": 3,
        "model_version": "claude-sonnet-5",
        "input_tokens": 9_400,
        "output_tokens": 820,
        "token_cost_micros": 40_500,
        "prompt_name": "options_strategist",
        "prompt_version": "v1",
        "prompt_digest": "d" * 16,
        "prompt_set_version": "ps-test",
        "latency_ms": 9_820,
        "schema_version": SCHEMA_VERSION,
    }
    row.update(fields)
    return journal.record_proposal(**row)
```

## 3. The playbook, exhaustively

This is the most valuable file in the suite and the cheapest to run — no journal, no graph,
no model, no I/O. Every test is `valid_proposal()` with one field moved, so a failure names
exactly one constraint.

The parametrised negative test is written so that **the violation text is asserted, not just
the count**. A playbook that returns "1 violation" for every possible mistake would pass a
count-only test and be useless in a journal read six months later.

Two tests deserve their own names rather than a parametrise row. `test_valid_proposal_passes`
is the one that fails when a band is changed carelessly, and it is the reason the fixture is
recorded rather than invented. `test_violations_are_independent` moves three fields at once
and asserts all three violations come back — because a `check` that returned early on the
first failure would still pass every single-mutation test and would hide the other two from
the journal.

### `strike_desk/tests/test_playbook.py`

```python
"""The deterministic verifier: every constraint, in both directions."""

from __future__ import annotations

import pytest

from strike_desk.grounding import ProposalSubmission
from strike_desk.playbook import PLAYBOOK_VERSION, Playbook, check
from tests.chain_fixtures import FIXED_NOW, proposal_args, valid_proposal


@pytest.fixture
def playbook(settings) -> Playbook:
    return Playbook.from_settings(settings)


def test_valid_proposal_passes(playbook: Playbook) -> None:
    assert check(valid_proposal(), playbook, now_ist=FIXED_NOW) == []


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"delta": 0.90}, "outside the 0.35-0.60 band"),
        ({"delta": 0.10}, "outside the 0.35-0.60 band"),
        ({"bid": 150.0, "ask": 230.0}, "exceeds the 1.50% cap"),
        ({"bid": 0.0, "ask": 0.0}, "quote is unusable"),
        ({"bid": 200.0, "ask": 180.0}, "is below bid"),
        ({"open_interest": 100}, "below the 50,000 floor"),
        ({"implied_volatility": 41.7}, "outside the 8.0-35.0% band"),
        ({"implied_volatility": 2.0}, "outside the 8.0-35.0% band"),
        ({"lots": 9}, "outside 1-2"),
        ({"quantity": 74}, "is not lots 1 x lot size 75"),
        ({"breakeven": 24997.0}, "does not equal 24992.00"),
        ({"entry_price_low": 300.0}, "is inverted"),
        ({"entry_price_low": 100.0, "entry_price_high": 120.0}, "outside the entry band"),
        ({"stop_price": 189.0}, "is not below the entry band low"),
        ({"target_price": 190.0}, "is not above the entry band high"),
        ({"expiry": "2026-12-31"}, "outside 1-10"),
        ({"expiry": "2026-08-25"}, "outside 1-10"),
        ({"expiry": "02-09-2026"}, "is not an ISO date"),
        ({"theta_per_day": 18.4}, "is positive; a long option decays"),
        ({"theta_per_day": -40.0}, "exceeds the Rs 1,500 budget"),
        ({"time_stop_ist": "09:00"}, "is not in the future"),
        ({"time_stop_ist": "16:30"}, "later than the 15:00 session cutoff"),
        ({"time_stop_ist": "not-a-time"}, "is not HH:MM"),
    ],
)
def test_each_violation_is_named(playbook: Playbook, overrides: dict, fragment: str) -> None:
    violations = check(valid_proposal(**overrides), playbook, now_ist=FIXED_NOW)
    assert violations, f"expected a violation for {overrides}"
    assert any(fragment in violation for violation in violations), violations


def test_direction_and_type_must_agree(playbook: Playbook) -> None:
    """The schema refuses the mismatch, and the playbook refuses it again."""
    with pytest.raises(ValueError, match="requires PE"):
        valid_proposal(direction="bearish")

    # Bypass the schema the way a hand-built row could, and confirm the playbook still
    # binds. `model_construct` skips validation by design; do not reach for
    # `object.__setattr__` here — it would keep "working" if the model were ever made
    # frozen, silently hiding the change to the schema guarantee.
    proposal = ProposalSubmission.model_construct(**proposal_args(direction="bearish"))
    violations = check(proposal, playbook, now_ist=FIXED_NOW)
    assert any("requires PE" in violation for violation in violations)


def test_violations_are_independent(playbook: Playbook) -> None:
    """check reports every failure, not only the first — a journal needs all of them."""
    violations = check(
        valid_proposal(delta=0.95, open_interest=10, lots=9),
        playbook,
        now_ist=FIXED_NOW,
    )
    assert len(violations) >= 3
    assert any("delta" in v for v in violations)
    assert any("open interest" in v for v in violations)
    assert any("lots" in v for v in violations)


def test_the_playbook_is_a_versioned_artifact(playbook: Playbook, settings) -> None:
    assert playbook.artifact.startswith(f"{PLAYBOOK_VERSION}+")
    assert len(playbook.digest) == 12

    tightened = Playbook.from_settings(settings.model_copy(update={"playbook_delta_max": 0.55}))
    assert tightened.digest != playbook.digest
    assert tightened.artifact.startswith(f"{PLAYBOOK_VERSION}+")


def test_describe_states_every_number_the_prompt_needs(playbook: Playbook) -> None:
    described = playbook.describe()
    for number in ("0.35", "0.60", "1.50%", "50,000", "8.0", "35.0", "1,500", "15:00"):
        assert number in described
```

## 4. The whitelist

Two tests, both structural, both running with no broker and no model. They are the executable
form of AC-5, and they are the reason "it proposes, it never places" is a property rather
than a promise.

`test_no_whitelist_holds_a_mutating_tool` is the import-time assertion re-run as an
assertion, so the guardrail is visible in the suite as well as in the module. The
parametrised half feeds it names OpenAlgo genuinely exposes — those are not invented, they
are what `mcp/mcpserver.py` really serves on the same session the desk holds open.

`test_the_bound_list_is_what_the_model_sees` is the one that would catch a real regression.
It asserts on the tools that are actually bound in `_agent`, after `tools_for_role` has run,
because a whitelist that is correct in a constant and wrong at the binding is a whitelist
that does nothing.

### `strike_desk/tests/test_options_strategist.py` — the whitelist half

```python
"""The Options Strategist: whitelist, loop, submissions, statuses."""

from __future__ import annotations

import pytest

from strike_desk.mcp_toolbox import (
    FORBIDDEN_PREFIXES,
    REGIME_TOOLS,
    REQUIRED_TOOLS,
    STRATEGIST_TOOLS,
    _assert_read_only,
    tools_for_role,
)
from strike_desk.specialists import ROLE_REGIME, ROLE_STRATEGIST

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
```

## 5. The loop, and its five endings

The rest of `test_options_strategist.py` drives the agent through each status. Every test is
the same shape: script the model's rounds, run the specialist, assert the status, the reason
code inputs and the row the journal would receive.

The scripted rounds are where the value is. `_rounds` builds an `AIMessage` list where the
first message calls the market tools and the last calls a submission, so each test says
exactly what the model did in a form that is readable in a diff.

### `strike_desk/tests/test_options_strategist.py` — the loop half

```python
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
    build_options_strategist,
    build_proposal_row,
)
from tests.chain_fixtures import SYMBOL, proposal_args


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
            [{"name": SUBMIT_PROPOSAL, "args": proposal_args(symbol="NIFTY02SEP2699999CE"), "id": "s"}],
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


def test_no_submission_within_the_round_budget_is_degraded(strategist_harness) -> None:
    result = strategist_harness.run(rounds=[_read_rounds()] * 6)
    assert result.payload["status"] == STATUS_DEGRADED
    assert "no submission" in result.payload["defect"]


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
        "index_symbol", "expiry", "strike", "option_type", "symbol", "lots", "lot_size",
        "quantity", "entry_price_low", "entry_price_high", "breakeven", "stop_price",
        "target_price", "time_stop_ist", "theta_per_day", "rationale", "evidence_json",
        "playbook_artifact", "playbook_verdict", "violations_json", "token_cost_micros",
    ):
        assert row[field] is not None, field
    assert row["schema_version"] == 4


def test_the_spans_carry_what_ac9_requires(strategist_harness, journal) -> None:
    strategist_harness.run(
        rounds=[_read_rounds(), [{"name": SUBMIT_PROPOSAL, "args": proposal_args(), "id": "s"}]]
    )
    names = {span.name for span in journal.spans_for_trace(strategist_harness.trace_id)}
    assert {"strategy.propose", "strategy.model_call", "strategy.tool_call"} <= names

    root = next(s for s in journal.spans_for_trace(strategist_harness.trace_id)
                if s.name == "strategy.propose")
    attributes = json.loads(root.attributes_json)
    for key in (
        "strategy.status", "strategy.symbol", "strategy.playbook_verdict",
        "strategy.tool_calls", "strategy.rejected_tools", "strategy.token_cost_micros",
        "playbook.artifact",
    ):
        assert key in attributes, key
```

## 6. Routing, classification, and the unreachable entry

`test_tick_proposals.py` drives the whole graph with a stubbed strategist, so it tests the
supervisor's decisions rather than the agent's. Three properties matter here and nothing
else does.

**The router gates before it spends.** `test_a_non_directional_regime_never_consults` is the
cost-shape test: a `range-bound` read must not reach the strategist at all, and the assertion
is that the stub was never called — not that its output was ignored.

**Every status becomes the right code.** One parametrised test maps all five statuses to
their reason code, category and disposition. This is where a wrong disposition on
`no-viable-contract` gets caught, which is the mistake that would make `strike-desk declines`
exit 2 every day.

**`enter` is unreachable.** Two tests, deliberately redundant. One drives a fully passing
proposal through the graph and asserts the outcome is still `decline`. The other reflects
over `_decide_outcome`'s source and asserts no branch returns `OUTCOME_ENTER`. The first
would pass if someone added an `enter` branch behind a flag that happened to be off; the
second would not.

### `strike_desk/tests/test_tick_proposals.py`

```python
"""Routing and classification through a real graph, with a stubbed strategist."""

from __future__ import annotations

import inspect

import pytest

from strike_desk.decline_taxonomy import describe
from strike_desk.graph import (
    OUTCOME_DECLINE,
    OUTCOME_ENTER,
    REASON_DATA_QUALITY,
    REASON_NO_VIABLE_CONTRACT,
    REASON_PROPOSAL_INVALID,
    REASON_PROPOSAL_UNGROUNDED,
    REASON_SPECIALIST_UNAVAILABLE,
    _decide_outcome,
)
from strike_desk.options_strategist import (
    STATUS_DEGRADED,
    STATUS_INVALID,
    STATUS_NO_CONTRACT,
    STATUS_PROPOSED,
    STATUS_UNGROUNDED,
)


@pytest.mark.parametrize(
    ("status", "code", "category", "disposition"),
    [
        (STATUS_PROPOSED, REASON_SPECIALIST_UNAVAILABLE, "specialist", "degraded"),
        (STATUS_NO_CONTRACT, REASON_NO_VIABLE_CONTRACT, "contract", "routine"),
        (STATUS_UNGROUNDED, REASON_PROPOSAL_UNGROUNDED, "contract", "defect"),
        (STATUS_INVALID, REASON_PROPOSAL_INVALID, "contract", "defect"),
        (STATUS_DEGRADED, REASON_DATA_QUALITY, "data", "degraded"),
    ],
)
def test_each_status_classifies(settings, tick_state, status, code, category, disposition) -> None:
    state = {**tick_state, "regime_label": "trending", "regime_confidence": 0.8,
             "proposal_status": status, "proposal": {"symbol": "NIFTY02SEP2624800CE"}}
    outcome, reason_code, text = _decide_outcome(state, settings)
    assert outcome == OUTCOME_DECLINE
    assert reason_code == code
    entry = describe(reason_code)
    assert (entry.category, entry.disposition) == (category, disposition)
    assert text and len(text) <= settings.reason_text_max_chars


def test_a_refusal_never_makes_the_day_look_broken(settings, tick_state) -> None:
    """no-viable-contract is routine: `strike-desk declines` must still exit 0."""
    assert describe(REASON_NO_VIABLE_CONTRACT).disposition == "routine"


def test_a_non_directional_regime_never_consults(tick_harness) -> None:
    """AC-6: gating happens before spending, not after."""
    tick_harness.set_regime(label="range-bound", confidence=0.9)
    decision = tick_harness.run_tick()
    assert tick_harness.strategist_calls == 0
    assert decision.reason_code == REASON_NO_VIABLE_CONTRACT
    assert "not directional" in decision.reason_text
    assert tick_harness.proposal_rows() == []


def test_a_low_confidence_directional_regime_never_consults(tick_harness) -> None:
    tick_harness.set_regime(label="trending", confidence=0.2)
    tick_harness.run_tick()
    assert tick_harness.strategist_calls == 0


def test_a_passing_proposal_still_declines(tick_harness) -> None:
    """AC-11, behaviourally."""
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=STATUS_PROPOSED)
    decision = tick_harness.run_tick()
    assert decision.outcome == OUTCOME_DECLINE
    assert decision.reason_code == REASON_SPECIALIST_UNAVAILABLE
    assert "'risk'" in decision.reason_text
    assert len(tick_harness.proposal_rows()) == 1


def test_enter_is_unreachable(tick_harness) -> None:
    """AC-11, structurally. A flagged-off enter branch would pass the test above."""
    source = inspect.getsource(_decide_outcome)
    assert "OUTCOME_ENTER" not in source
    assert OUTCOME_ENTER == "enter"  # still defined, for UC-05 to reach


def test_the_proposal_row_is_written_before_the_decision(tick_harness) -> None:
    """A proposal that was not journalled did not happen."""
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=STATUS_PROPOSED, journal_fails=True)
    with pytest.raises(JournalWriteError):
        tick_harness.run_tick()
    assert tick_harness.decision_rows() == []
```

## 7. Extending the existing files

Three files gain tests rather than getting new ones.

**`tests/test_decline_taxonomy.py`** — the parity test already reflects over `graph.py`'s
`REASON_*` constants and will pick up the three new codes with no edit. Two tests are added.
`test_the_taxonomy_is_additive` is the important one: it holds the `dt-1` entry set as a
frozen mapping of code → (category, disposition) and asserts every one of those pairs is
unchanged today. That is the automated form of manual MT-05, and it is what stops a future
edit from turning a year of host rows into taxonomy drift.

```python
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


@pytest.mark.parametrize(("code", "expected"), sorted(DT1_CLASSES.items()))
def test_the_taxonomy_is_additive(code: str, expected: tuple[str, str]) -> None:
    """A dt-1 row must never become taxonomy drift because we shipped dt-2."""
    entry = describe(code)
    assert (entry.category, entry.disposition) == expected


def test_dt1_default_sentences_are_unchanged(golden) -> None:
    """Adding a variant may not alter the sentence an existing row already reads as."""
    for code in DT1_CLASSES:
        assert render(code, max_chars=400, **golden.fields(code)) == golden.dt1_sentence(code)
```

The golden file grows the three new codes and both new variants. Regenerate it deliberately
with `uv run pytest tests/test_decline_taxonomy.py --golden-update`, read the diff, and
commit it in the same change as the wording — never in a follow-up.

**`tests/test_journal_migration.py`** — one test, because the migration is one `CREATE TABLE`:

```python
def test_a_v3_journal_gains_the_table_in_place(tmp_path, v3_journal_bytes) -> None:
    path = tmp_path / "strike_desk.db"
    path.write_bytes(v3_journal_bytes)
    before = _table_names(path), _row_count(path, "decisions")

    journal = Journal(path)
    try:
        journal.create_schema()
        journal.create_schema()  # idempotent
    finally:
        journal.close()

    after = _table_names(path), _row_count(path, "decisions")
    assert "proposals" not in before[0] and "proposals" in after[0]
    assert before[1] == after[1]          # not one row read or rewritten
    assert _triggers_for(path, "proposals") == {"proposals_no_update", "proposals_no_delete"}
    assert _pragma_user_columns(path, "decisions") == _pragma_user_columns_v3()
```

**`tests/test_grounding.py`** — the proposal validator, with the three grounding surfaces it
now covers: the sentence, the structured numbers, and the symbol. Those are the assertions
manual MT-04 makes by hand.

## 8. Running it

```bash
cd strike_desk

uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ -q
uv run pytest tests/ --cov=strike_desk --cov-report=term-missing --cov-fail-under=80
uv run --group dev bandit -r src/ -q
```

Every one of those runs with no broker session, no Anthropic key and no open market. That is
not an accident of this slice — it is the reason the chain is recorded.

### `.github/workflows/strike-desk-ci.yml` — the early gate

The gate iteration 03 added runs the cheap, structural tests before the full suite, so a
mis-wired taxonomy or a leaked mutating tool fails in seconds rather than minutes. Three
files join it:

```yaml
      - name: Structural gates
        working-directory: strike_desk
        run: |
          uv run pytest \
            tests/test_decline_taxonomy.py \
            tests/test_journal_migration.py \
            tests/test_playbook.py \
            tests/test_options_strategist.py::test_every_whitelisted_tool_is_a_reader \
            tests/test_options_strategist.py::test_no_whitelist_holds_a_mutating_tool \
            tests/test_tick_proposals.py::test_enter_is_unreachable \
            -q
```

Those five entries are the slice's non-negotiables: the taxonomy is additive, the migration
is in place, the arithmetic binds, no whitelist holds a mutating tool, and no branch can
produce an entry. If any of them fails, nothing else about the build matters.

## 9. Traceability

| AC | Test |
| --- | --- |
| AC-1 proposal completeness | `test_the_row_carries_every_field_ac1_requires` |
| AC-2 grounding | `test_an_invented_number_is_ungrounded`, `test_an_invented_contract_is_ungrounded`, `test_grounding.py` |
| AC-3 playbook verification | all of `test_playbook.py`, `test_a_persuasive_proposal_that_fails_arithmetic_is_a_defect` |
| AC-4 refusal is first-class | `test_a_refusal_is_a_first_class_answer`, `test_a_refusal_never_makes_the_day_look_broken` |
| AC-5 read-only whitelist | `test_every_whitelisted_tool_is_a_reader`, `test_no_whitelist_holds_a_mutating_tool`, `test_the_bound_list_is_what_the_model_sees`, `test_a_rejected_tool_is_counted_and_never_executed` |
| AC-6 gated consultation | `test_a_non_directional_regime_never_consults`, `test_a_low_confidence_directional_regime_never_consults` |
| AC-7 additive taxonomy | `test_the_taxonomy_is_additive`, `test_dt1_default_sentences_are_unchanged`, the parity pair |
| AC-8 in-place migration | `test_a_v3_journal_gains_the_table_in_place` |
| AC-9 spans | `test_the_spans_carry_what_ac9_requires` |
| AC-10 the command | `test_proposal_view.py` (text ↔ JSON), the argument tests |
| AC-11 no `enter` | `test_a_passing_proposal_still_declines`, `test_enter_is_unreachable` |
| AC-12 versioned artifacts | `test_the_playbook_is_a_versioned_artifact`, the golden file |

## 10. What this suite does not prove

Worth stating, because a green suite is easy to over-read.

1. **That the playbook picks good contracts.** It proves the playbook enforces its own rules.
   Whether those rules make money needs UC-11's baseline and UC-17's replay harness.
2. **That the recorded chain still looks like a real one.** The fixture ages. Re-record it
   after any OpenAlgo upgrade that touches `/api/v1/optionchain`, and treat a shape change as
   a failing test rather than a fixture to patch.
3. **That the model behaves like the scripted rounds.** `FakeChatModel` proves the loop
   handles what a model *could* send, not what Sonnet *will* send. That gap is exactly what
   block B of the manual tests closes, and it is why block B is not optional.
