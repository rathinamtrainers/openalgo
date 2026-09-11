"""The live position rendered for a human or for a machine."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .config import IST, Settings
from .journal import POSITION_ORPHANED, Journal
from .levels import ExitLevels, distances


def build_position_report(journal: Journal, snapshot: dict[str, Any], day: str) -> dict[str, Any]:
    """Merge what the monitor holds in memory with what the journal recorded today."""
    rows = journal.list_positions(day, limit=200)
    states = [
        {
            "position_id": row.position_id,
            "state": row.state,
            "symbol": row.symbol,
            "quantity": row.quantity,
            "exit_reason": row.exit_reason,
            "realised_pnl": row.realised_pnl,
            "defect": bool(row.defect),
            "at": row.created_at_utc.astimezone(IST).strftime("%H:%M:%S IST"),
            "detail": row.detail,
        }
        for row in rows
    ]
    report: dict[str, Any] = {
        "trading_day": day,
        "generated_at": datetime.now(tz=UTC).astimezone(IST).isoformat(),
        "live": snapshot,
        "states": states,
        "defects": [state for state in states if state["defect"]
                    or state["state"] == POSITION_ORPHANED],
    }
    if snapshot.get("managed"):
        levels = snapshot["levels"]
        report["distances"] = distances(
            ExitLevels(
                stop_price=float(levels["stop_price"]),
                target_price=float(levels["target_price"]),
                time_stop_utc=datetime.fromisoformat(str(levels["time_stop_utc"])),
                time_stop_reason=str(levels["time_stop_reason"]),
            ),
            snapshot.get("last_price"),
        )
    return report


def render_position_report(report: dict[str, Any], settings: Settings | None = None) -> str:
    """The text the operator reads. Every number in it comes from the report dict."""
    lines: list[str] = [f"position report for {report['trading_day']}", ""]
    if settings is not None:
        lines.append(f"autonomy: {settings.autonomy}")
    live = report.get("live") or {}
    if not live.get("managed"):
        lines.append("no position under management")
    else:
        levels = live["levels"]
        gaps = report.get("distances") or {}
        time_stop = datetime.fromisoformat(str(levels["time_stop_utc"]))
        lines.extend(
            [
                f"symbol      : {live['symbol']} ({live['exchange']})",
                f"quantity    : {live['quantity']} at {live['entry_price']:.2f}",
                f"last price  : {live.get('last_price')} "
                f"[{live.get('feed_source')} {live.get('feed_age_ms')}ms]",
                f"stop        : {levels['stop_price']:.2f}  (distance {gaps.get('to_stop')})",
                f"target      : {levels['target_price']:.2f}  (distance {gaps.get('to_target')})",
                f"time stop   : {time_stop.astimezone(IST).strftime('%H:%M:%S IST')} "
                f"({levels['time_stop_reason']})",
                f"exit tries  : {live.get('attempts', 0)}",
            ]
        )
    lines.append("")
    lines.append("today:")
    for state in report["states"]:
        flag = "  [DEFECT]" if state["defect"] else ""
        lines.append(
            f"  {state['at']}  {state['state']:<12} {state['symbol']:<28}"
            f"{state.get('exit_reason') or '':<18}{flag}"
        )
        lines.append(f"      {state['detail']}")
    return "\n".join(lines)
