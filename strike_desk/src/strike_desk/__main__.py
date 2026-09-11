"""Command-line surface for the Strike Desk service."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from . import approval_view
from .config import IST, Settings, get_settings
from .decline_report import (
    DayReport,
    WindowReport,
    build_day_report,
    build_window_report,
    render_day,
    render_window,
    to_json,
)
from .decline_taxonomy import TAXONOMY_ARTIFACT
from .errors import McpUnavailable, ModelCallFailed, StrikeDeskError
from .journal import Journal
from .mcp_toolbox import McpToolbox
from .observability import Redactor, configure_logging, configure_tracing, get_tracer
from .openalgo_mirror import OpenAlgoMirror
from .playbook import Playbook
from .position_view import build_position_report, render_position_report
from .prompt_registry import PromptRegistry
from .proposal_view import as_dict
from .proposal_view import render as render_proposals
from .regime_analyst import build_regime_analyst, build_regime_read_row
from .risk_officer import RiskLimits
from .risk_view import as_dict as risk_as_dict
from .risk_view import render as risk_render
from .service import StrikeDeskService
from .specialists import SpecialistRequest


def _today() -> str:
    return datetime.now(tz=IST).date().isoformat()


def _trading_day(raw: str) -> str:
    """An IST trading day, rejected at the boundary rather than deep in a query."""
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{raw!r} is not an IST date as YYYY-MM-DD") from exc


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{raw!r} is not a whole number") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _cmd_position(settings: Settings, args: argparse.Namespace) -> int:
    """Print today's position states and the live position as the journal knows it."""
    journal = Journal(settings.db_path)
    try:
        journal.create_schema()
        day = args.day or _today()
        row = journal.live_position()
        snapshot: dict[str, object] = {"managed": False}
        if row is not None:
            snapshot = {
                "managed": True,
                "position_id": row.position_id,
                "symbol": row.symbol,
                "exchange": row.exchange,
                "quantity": row.quantity,
                "entry_price": row.entry_price,
                "levels": {
                    "stop_price": row.stop_price,
                    "target_price": row.target_price,
                    "time_stop_utc": (
                        row.time_stop_utc.isoformat() if row.time_stop_utc else None
                    ),
                    "time_stop_reason": "time-stop",
                },
                "last_price": None,
                "feed_source": "journal",
                "feed_age_ms": None,
                "attempts": len(journal.exits_for_position(row.position_id)),
            }
        report = build_position_report(journal, snapshot, day)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
        else:
            print(render_position_report(report, settings))
        return 2 if report["defects"] else 0
    finally:
        journal.close()


def _cmd_run(settings: Settings, _args: argparse.Namespace) -> int:
    service = StrikeDeskService(settings)
    service.start()
    service.run_forever()
    return 0


def _cmd_tick_now(settings: Settings, _args: argparse.Namespace) -> int:
    if not hasattr(signal, "SIGUSR1"):
        print(
            "tick-now requires SIGUSR1 (Linux/WSL/production). "
            "On Windows use the scheduled cadence or run under WSL.",
            file=sys.stderr,
        )
        return 1
    try:
        pid = int(settings.pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        print("strike-desk does not appear to be running (no readable PID file)", file=sys.stderr)
        return 1
    try:
        os.kill(pid, signal.SIGUSR1)
    except OSError as exc:
        print(f"could not signal pid {pid}: {exc}", file=sys.stderr)
        return 1
    print(f"requested an immediate tick from pid {pid}")
    return 0


def _cmd_kill(settings: Settings, args: argparse.Namespace) -> int:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    settings.kill_switch_path.write_text(
        f"{datetime.now(tz=UTC).isoformat()} {args.reason}", encoding="utf-8"
    )
    print(f"kill switch engaged: {args.reason}")
    return 0


def _cmd_resume(settings: Settings, _args: argparse.Namespace) -> int:
    if settings.kill_switch_path.exists():
        settings.kill_switch_path.unlink()
        print("kill switch released")
    else:
        print("kill switch was not engaged")
    return 0


def _cmd_status(settings: Settings, _args: argparse.Namespace) -> int:
    journal = Journal(settings.db_path)
    try:
        journal.create_schema()
        day = _today()
        report = build_day_report(journal, day, settings.index_symbol)

        reads = journal.list_regime_reads(day, limit=1000)
        by_status: dict[str, int] = {}
        for read in reads:
            by_status[read.status] = by_status.get(read.status, 0) + 1

        killed = settings.kill_switch_path.exists()
        print(f"index            : {settings.index_symbol}")
        print(f"cadence          : {settings.tick_interval_seconds}s")
        print(f"model            : {settings.regime_model}")
        print(f"prompt set       : {PromptRegistry.load(settings.prompts_dir).set_version}")
        print(f"taxonomy         : {TAXONOMY_ARTIFACT}")
        print(f"playbook         : {Playbook.from_settings(settings).artifact}")
        limits = RiskLimits.from_settings(settings)
        print(f"risk limits      : {limits.artifact}")
        print(limits.describe())
        print(f"execution        : {'ENABLED' if settings.execution_enabled else 'disabled'}")
        if settings.execution_enabled:
            mirror = OpenAlgoMirror(settings)
            try:
                health = mirror.health()
            finally:
                mirror.close()
            print(f"approval gate    : {'ok' if health.ok else 'UNUSABLE'} — {health.detail}")
            print(f"outstanding      : {len(journal.open_approvals())}")
        print(f"strategist model : {settings.strategist_model}")
        print(f"directional      : {settings.directional_regimes}")
        print(f"kill switch      : {'ENGAGED' if killed else 'released'}")
        if killed:
            reason = settings.kill_switch_path.read_text(encoding="utf-8").strip()
            print(f"  reason         : {reason}")
        if settings.heartbeat_path.exists():
            raw_beat = settings.heartbeat_path.read_text(encoding="utf-8").strip()
            beat = datetime.fromisoformat(raw_beat)
            age = (datetime.now(tz=UTC) - beat).total_seconds()
            print(f"last tick attempt: {beat.isoformat()} ({age:.0f}s ago)")
        else:
            print("last tick attempt: never")
        print(
            f"decisions {day}: {report.total}"
            f"  (declines {report.declines} | holds {report.holds} | entries {report.entries})"
        )
        for row in report.reasons:
            print(f"  {row.code:<26} {row.count:>4}  {row.category}/{row.disposition}")
        cost = journal.token_cost_micros(day)
        print(f"regime reads {day}: {len(reads)}  (${cost / 1_000_000:.4f})")
        for status, count in sorted(by_status.items()):
            print(f"  {status:<26} {count}")
    finally:
        journal.close()
    return 0


def _cmd_declines(settings: Settings, args: argparse.Namespace) -> int:
    """Count and classify what the desk decided, for one day or a window of days."""
    secrets = [settings.openalgo_api_key.get_secret_value()]
    if settings.anthropic_api_key is not None:
        secrets.append(settings.anthropic_api_key.get_secret_value())
    redactor = Redactor(secrets)
    configure_logging(settings, redactor)
    journal = Journal(settings.db_path)
    journal.create_schema()
    provider, _processor = configure_tracing(settings, journal, redactor)
    try:
        with get_tracer().start_as_current_span("report.declines") as span:
            span.set_attribute("report.taxonomy", TAXONOMY_ARTIFACT)
            report: DayReport | WindowReport
            if args.since is not None:
                days = args.since or settings.report_default_days
                report = build_window_report(journal, settings.index_symbol, limit=days)
                rendered = render_window(report)
                span.set_attribute("report.days", len(report.days))
            else:
                day = args.day or _today()
                report = build_day_report(journal, day, settings.index_symbol)
                rendered = render_day(report)
                span.set_attribute("report.days", 1)
                span.set_attribute("report.trading_day", day)
            span.set_attribute("report.total", report.total)
            span.set_attribute("report.defects", report.defects)
            span.set_attribute("report.unknown_codes", ",".join(report.unknown_codes) or "none")
            span.set_attribute("report.healthy", report.healthy)
        print(to_json(report) if args.json else rendered)
        return 0 if report.healthy else 2
    finally:
        provider.shutdown()
        journal.close()


def _cmd_journal(settings: Settings, args: argparse.Namespace) -> int:
    journal = Journal(settings.db_path)
    try:
        journal.create_schema()
        day = args.day or _today()
        for decision in journal.list_decisions(day, limit=args.limit):
            flag = "" if decision.trace_complete else "  [TRACE INCOMPLETE]"
            category = decision.reason_category or "unstamped"
            print(
                f"{decision.created_at_utc.isoformat()}  {decision.outcome:<8}"
                f"{decision.reason_code:<26} {category:<12} {decision.latency_ms:>5}ms  "
                f"{decision.trace_id}{flag}"
            )
            print(f"    {decision.reason_text}")
    finally:
        journal.close()
    return 0


def _cmd_regime(settings: Settings, _args: argparse.Namespace) -> int:
    """Run one regime read out of band, print it, and journal it as a CLI read."""
    secrets = [settings.openalgo_api_key.get_secret_value()]
    if settings.anthropic_api_key is not None:
        secrets.append(settings.anthropic_api_key.get_secret_value())
    redactor = Redactor(secrets)
    configure_logging(settings, redactor)

    journal = Journal(settings.db_path)
    journal.create_schema()
    provider, _processor = configure_tracing(settings, journal, redactor)
    prompts = PromptRegistry.load(settings.prompts_dir)
    toolbox = McpToolbox(settings)
    tick_id = f"cli:{uuid.uuid4()}"
    try:
        analyst = build_regime_analyst(settings, prompts, toolbox)
        with get_tracer().start_as_current_span("strike_desk.regime_cli") as root:
            trace_id = format(root.get_span_context().trace_id, "032x")
            result = analyst.run(
                SpecialistRequest(
                    tick_id=tick_id,
                    index_symbol=settings.index_symbol,
                    as_of=datetime.now(tz=UTC),
                    book={},
                )
            )
        journal.record_regime_read(
            **build_regime_read_row(
                dict(result.payload),
                settings=settings,
                prompts=prompts,
                tick_id=tick_id,
                trace_id=trace_id,
                trading_day=_today(),
                source="cli",
            )
        )
        payload = result.payload
        print(f"status     : {payload['status']}")
        print(f"label      : {payload.get('label')}")
        print(f"confidence : {payload.get('confidence')}")
        print(f"rationale  : {payload.get('rationale')}")
        print(
            f"tool calls : {payload.get('tool_call_count')} "
            f"({payload.get('tool_error_count')} failed)"
        )
        print(f"cost       : ${result.token_cost_micros / 1_000_000:.4f}")
        print(f"trace      : {trace_id}")
        if payload.get("defect"):
            print(f"defect     : {payload['defect']}")
        print("evidence   :")
        for item in payload.get("evidence") or []:
            print(f"  {json.dumps(item, sort_keys=True)}")
        return 0 if payload["status"] == "ok" else 2
    except (McpUnavailable, ModelCallFailed, StrikeDeskError) as exc:
        print(f"regime read failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        toolbox.close()
        provider.shutdown()
        journal.close()


def _resolve_days(args: argparse.Namespace, settings: Settings) -> list[str] | None:
    """Resolve --day/--since into a list of trading days, or None to ask the journal.

    Raises ValueError before any journal is opened when the arguments are unusable.
    """
    day = getattr(args, "day", None)
    since = getattr(args, "since", None)
    if day is not None and since is not None:
        raise ValueError("--day and --since are alternatives; pass one of them")
    if day:
        try:
            return [date.fromisoformat(str(day)).isoformat()]
        except ValueError as exc:
            raise ValueError(f"{day!r} is not an IST date as YYYY-MM-DD") from exc
    if since is not None and since not in (-1, 0) and int(since) < 1:
        raise ValueError("must be at least 1")
    return None


def _cmd_proposals(settings: Settings, args: argparse.Namespace) -> int:
    try:
        days = _resolve_days(args, settings)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    journal = Journal(settings.db_path)
    try:
        journal.create_schema()
        if days is None:
            n = getattr(args, "since", None)
            if n in (None, -1, 0):
                n = settings.report_default_days
            days = journal.recent_proposal_days(int(n))
        rows = [row for day in days for row in journal.list_proposals(day)]
        if args.json:
            print(
                json.dumps(
                    {
                        "taxonomy": TAXONOMY_ARTIFACT,
                        "playbook": Playbook.from_settings(settings).artifact,
                        "days": days,
                        "proposals": [as_dict(row) for row in rows],
                    },
                    indent=2,
                    default=str,
                )
            )
        else:
            print(render_proposals(rows, days=days))
    finally:
        journal.close()
    return 0


def _cmd_risk(settings: Settings, args: argparse.Namespace) -> int:
    try:
        days = _resolve_days(args, settings)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    journal = Journal(settings.db_path)
    try:
        journal.create_schema()
        if days is None:
            n = getattr(args, "since", None)
            if n in (None, -1, 0):
                n = settings.report_default_days
            days = journal.recent_risk_days(int(n))
        rows = [row for day in days for row in journal.list_risk_verdicts(day)]
        if args.json:
            print(
                json.dumps(
                    {
                        "limits": RiskLimits.from_settings(settings).artifact,
                        "days": days,
                        "verdicts": [risk_as_dict(row) for row in rows],
                    },
                    indent=2,
                    default=str,
                )
            )
        else:
            print(risk_render(rows, days=days))
    finally:
        journal.close()
    return 0


def _cmd_approvals(settings: Settings, args: argparse.Namespace) -> int:
    if args.day and args.since:
        print("--day and --since are mutually exclusive", file=sys.stderr)
        return 1
    if args.since is not None and args.since < 1:
        print("--since must be at least 1", file=sys.stderr)
        return 1

    journal = Journal(settings.db_path)
    try:
        journal.create_schema()
        if args.since:
            today = datetime.now(tz=IST).date()
            days = [
                (today - timedelta(days=offset)).isoformat()
                for offset in range(args.since - 1, -1, -1)
            ]
        else:
            days = [args.day or _today()]

        collected: list[tuple[Any, list[Any]]] = []
        for day in days:
            for row in journal.list_approvals(day, limit=args.limit):
                collected.append((row, list(journal.orders_for_approval(row.approval_id))))

        if args.json:
            print(approval_view.to_json(collected))
        elif not collected:
            print(f"no approvals for {', '.join(days)}")
        else:
            for row, orders in collected:
                print(approval_view.render(row, orders, journal))
        return 2 if any(row.defect for row, _ in collected) else 0
    finally:
        journal.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="strike-desk", description="Strike Desk decision tick")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run", help="run the desk service in the foreground")
    subparsers.add_parser("tick-now", help="ask the running service for one immediate tick")

    kill_parser = subparsers.add_parser("kill", help="engage the kill switch")
    kill_parser.add_argument("--reason", default="engaged by the trader")

    subparsers.add_parser("resume", help="release the kill switch")
    subparsers.add_parser("status", help="show liveness, kill state, decisions and reads")
    subparsers.add_parser("regime", help="run one regime read now and print it")

    journal_parser = subparsers.add_parser("journal", help="print today's decisions")
    journal_parser.add_argument("--day", type=_trading_day, help="IST trading day as YYYY-MM-DD")
    journal_parser.add_argument("--limit", type=_positive_int, default=50)

    declines_parser = subparsers.add_parser(
        "declines", help="count and classify the desk's no-trade decisions"
    )
    declines_parser.add_argument("--day", type=_trading_day, help="IST trading day as YYYY-MM-DD")
    declines_parser.add_argument(
        "--since",
        type=_positive_int,
        nargs="?",
        const=0,
        help="report the most recent N journalled trading days instead of one day; "
        "bare --since uses STRIKE_DESK_REPORT_DEFAULT_DAYS",
    )
    declines_parser.add_argument(
        "--json", action="store_true", help="print the same numbers as a JSON document"
    )

    proposals_parser = subparsers.add_parser("proposals", help="list what the strategist proposed")
    proposals_group = proposals_parser.add_mutually_exclusive_group()
    proposals_group.add_argument("--day", type=_trading_day, help="one IST trading day, YYYY-MM-DD")
    proposals_group.add_argument(
        "--since",
        type=_positive_int,
        nargs="?",
        const=0,
        help="the most recent N journalled days; bare --since uses STRIKE_DESK_REPORT_DEFAULT_DAYS",
    )
    proposals_parser.add_argument("--json", action="store_true", help="print JSON instead of text")

    risk_parser = subparsers.add_parser("risk", help="list every risk adjudication")
    risk_group = risk_parser.add_mutually_exclusive_group()
    risk_group.add_argument("--day", type=_trading_day, help="one IST trading day, YYYY-MM-DD")
    risk_group.add_argument(
        "--since",
        type=_positive_int,
        nargs="?",
        const=0,
        help="the most recent N journalled days; bare --since uses STRIKE_DESK_REPORT_DEFAULT_DAYS",
    )
    risk_parser.add_argument("--json", action="store_true", help="print JSON instead of text")

    approvals_parser = subparsers.add_parser("approvals", help="print approvals and their orders")
    approvals_parser.add_argument("--day", help="IST trading day as YYYY-MM-DD")
    approvals_parser.add_argument("--since", type=int, help="the last N days, ending today")
    approvals_parser.add_argument("--limit", type=int, default=100)
    approvals_parser.add_argument("--json", action="store_true")

    position_parser = subparsers.add_parser("position", help="show the managed position")
    position_parser.add_argument("--day", help="IST trading day as YYYY-MM-DD")
    position_parser.add_argument("--json", action="store_true", help="machine-readable output")

    args = parser.parse_args(argv)
    if getattr(args, "since", None) is not None and getattr(args, "day", None) is not None:
        parser.error("--day and --since are alternatives; pass one of them")
    settings = get_settings()
    handlers = {
        "run": _cmd_run,
        "tick-now": _cmd_tick_now,
        "kill": _cmd_kill,
        "resume": _cmd_resume,
        "status": _cmd_status,
        "regime": _cmd_regime,
        "journal": _cmd_journal,
        "declines": _cmd_declines,
        "proposals": _cmd_proposals,
        "risk": _cmd_risk,
        "approvals": _cmd_approvals,
        "position": _cmd_position,
    }
    return handlers[args.command](settings, args)


if __name__ == "__main__":
    raise SystemExit(main())
