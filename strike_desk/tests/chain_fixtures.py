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
            "indicators": {
                "ema_20": 24761.4,
                "supertrend": [24688.2, 1],
                "adx_di": [27.4, 14.1, 27.4],
            },
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


def seed_proposal(
    journal: Any, *, trading_day: str, status: str = "proposed", **fields: Any
) -> str:
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
