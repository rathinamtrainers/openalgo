"""One proposal row, rendered as text or as JSON. Two views, one object."""

from __future__ import annotations

import json
from typing import Any

from .decline_taxonomy import TAXONOMY_ARTIFACT
from .journal import Proposal

STATUS_MARK = {
    "proposed": "PROPOSED",
    "no-contract": "no contract",
    "ungrounded": "UNGROUNDED",
    "invalid": "INVALID",
    "degraded": "degraded",
}


def as_dict(row: Proposal) -> dict[str, Any]:
    """Everything one proposal row holds, in the shape --json prints."""
    return {
        "proposal_id": row.proposal_id,
        "tick_id": row.tick_id,
        "trace_id": row.trace_id,
        "trading_day": row.trading_day,
        "created_at_utc": row.created_at_utc.isoformat(),
        "status": row.status,
        "regime": {"label": row.regime_label, "confidence": row.regime_confidence},
        "contract": {
            "direction": row.direction,
            "symbol": row.symbol,
            "expiry": row.expiry,
            "strike": row.strike,
            "option_type": row.option_type,
            "lots": row.lots,
            "lot_size": row.lot_size,
            "quantity": row.quantity,
            "entry_band": [row.entry_price_low, row.entry_price_high],
            "delta": row.delta,
            "theta_per_day": row.theta_per_day,
            "implied_volatility": row.implied_volatility,
            "open_interest": row.open_interest,
            "breakeven": row.breakeven,
            "stop_price": row.stop_price,
            "target_price": row.target_price,
            "time_stop_ist": row.time_stop_ist,
        },
        "playbook": {
            "artifact": row.playbook_artifact,
            "verdict": row.playbook_verdict,
            "violations": json.loads(row.violations_json or "[]"),
        },
        "rationale": row.rationale,
        "defect": row.defect,
        "cost": {
            "model": row.model_version,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "token_cost_micros": row.token_cost_micros,
        },
        "tools": {
            "calls": row.tool_call_count,
            "errors": row.tool_error_count,
            "rejected": row.rejected_tool_count,
        },
        "prompt": {
            "name": row.prompt_name,
            "version": row.prompt_version,
            "digest": row.prompt_digest,
            "set": row.prompt_set_version,
        },
        "latency_ms": row.latency_ms,
    }


def render(rows: list[Proposal], *, days: list[str]) -> str:
    """The terminal rendering of the same rows --json prints."""
    lines = [
        f"proposals        : {len(rows)} over {len(days)} day(s)",
        f"taxonomy         : {TAXONOMY_ARTIFACT}",
        "",
    ]
    if not rows:
        lines.append("(no proposal attempts in this window)")
        return "\n".join(lines)

    for row in rows:
        view = as_dict(row)
        contract = view["contract"]
        mark = STATUS_MARK.get(row.status, row.status)
        header = f"{row.created_at_utc.strftime('%H:%M:%S')}  {mark}"
        if contract["symbol"]:
            header += (
                f"  {contract['symbol']}  x{contract['lots']} lot(s)"
                f"  entry {contract['entry_band'][0]}-{contract['entry_band'][1]}"
            )
        lines.append(header)
        if contract["symbol"]:
            lines.append(
                f"    delta {contract['delta']}  iv {contract['implied_volatility']}%"
                f"  oi {contract['open_interest']}  theta {contract['theta_per_day']}/day"
            )
            lines.append(
                f"    breakeven {contract['breakeven']}  stop {contract['stop_price']}"
                f"  target {contract['target_price']}  time-stop {contract['time_stop_ist']} IST"
            )
        lines.append(f"    playbook  {row.playbook_verdict}  ({row.playbook_artifact})")
        for violation in view["playbook"]["violations"]:
            lines.append(f"      - {violation}")
        if row.defect:
            lines.append(f"    defect    {row.defect}")
        if row.rationale:
            lines.append(f"    {row.rationale}")
        lines.append(
            f"    cost ${row.token_cost_micros / 1_000_000:.4f}"
            f"  tools {row.tool_call_count}/{row.tool_error_count} err"
            f"/{row.rejected_tool_count} rejected  {row.latency_ms}ms"
        )
        lines.append("")
    return "\n".join(lines).rstrip()
