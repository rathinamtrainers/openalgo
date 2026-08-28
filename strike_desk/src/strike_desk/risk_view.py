"""One risk verdict row, rendered as text or as JSON. Two views, one object."""

from __future__ import annotations

import json
from typing import Any

from .journal import RiskVerdictRow
from .risk_officer import RISK_LIMITS_VERSION, UNIT_RUPEES

VERDICT_MARK = {
    "pass": "PASS",  # nosec B105 — display label, not a credential
    "reduce": "REDUCE",
    "veto": "VETO",
    "hold": "HOLD",
}


def _value(value: Any, unit: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"Rs {number:,.0f}" if unit == UNIT_RUPEES else f"{number:g}"


def as_dict(row: RiskVerdictRow) -> dict[str, Any]:
    """Everything one verdict row holds, in the shape --json prints."""
    return {
        "verdict_id": row.verdict_id,
        "tick_id": row.tick_id,
        "trace_id": row.trace_id,
        "proposal_id": row.proposal_id,
        "trading_day": row.trading_day,
        "created_at_utc": row.created_at_utc.isoformat(),
        "index_symbol": row.index_symbol,
        "symbol": row.symbol,
        "verdict": row.verdict,
        "session_stop": bool(row.session_stop),
        "tripped": {
            "limit": row.tripped_limit,
            "configured": row.configured_value,
            "observed": row.observed_value,
            "unit": row.limit_unit,
        },
        "capital_base": row.capital_base,
        "lots": {"requested": row.lots_requested, "cleared": row.lots_cleared},
        "premium_at_risk": row.premium_at_risk,
        "max_loss_at_stop": row.max_loss_at_stop,
        "checks": json.loads(row.checks_json or "[]"),
        "limits_artifact": row.limits_artifact,
        "detail": row.detail,
        "latency_us": row.latency_us,
    }


def render(rows: list[RiskVerdictRow], *, days: list[str]) -> str:
    """The terminal rendering of the same rows --json prints."""
    lines = [
        f"risk verdicts    : {len(rows)} over {len(days)} day(s)",
        f"limits           : {RISK_LIMITS_VERSION} (per row below)",
        "",
    ]
    if not rows:
        lines.append("(no adjudications in this window)")
        return "\n".join(lines)

    for row in rows:
        view = as_dict(row)
        mark = VERDICT_MARK.get(row.verdict, row.verdict)
        header = f"{row.created_at_utc.strftime('%H:%M:%S')}  {mark}"
        if row.symbol:
            header += f"  {row.symbol}"
        if row.lots_requested:
            header += f"  {row.lots_requested} -> {row.lots_cleared} lot(s)"
        if row.session_stop:
            header += "  [session stop]"
        lines.append(header)
        if row.tripped_limit:
            lines.append(
                f"    tripped   {row.tripped_limit}  configured "
                f"{_value(row.configured_value, row.limit_unit)}  observed "
                f"{_value(row.observed_value, row.limit_unit)}"
            )
        lines.append(
            f"    base      Rs {row.capital_base:,.0f}   premium "
            f"Rs {row.premium_at_risk:,.0f}   risk Rs {row.max_loss_at_stop:,.0f}"
        )
        for check in view["checks"]:
            flag = "x" if check["breached"] else " "
            lines.append(
                f"      [{flag}] {check['limit']:<26} "
                f"{_value(check['configured'], check['unit']):>14} vs "
                f"{_value(check['observed'], check['unit']):>14}"
            )
        lines.append(f"    {row.detail}")
        lines.append(f"    {row.limits_artifact}   {row.latency_us}us")
        lines.append("")
    return "\n".join(lines).rstrip()
