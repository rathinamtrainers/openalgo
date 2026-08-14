"""Command-line surface for the Strike Desk service."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import uuid
from datetime import UTC, datetime

from .config import IST, Settings, get_settings
from .errors import McpUnavailable, ModelCallFailed, StrikeDeskError
from .journal import Journal
from .mcp_toolbox import McpToolbox
from .observability import Redactor, configure_logging, configure_tracing, get_tracer
from .prompt_registry import PromptRegistry
from .regime_analyst import build_regime_analyst, build_regime_read_row
from .service import StrikeDeskService
from .specialists import SpecialistRequest


def _today() -> str:
    return datetime.now(tz=IST).date().isoformat()


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
        decisions = journal.list_decisions(day, limit=1000)
        by_reason: dict[str, int] = {}
        for decision in decisions:
            by_reason[decision.reason_code] = by_reason.get(decision.reason_code, 0) + 1

        reads = journal.list_regime_reads(day, limit=1000)
        by_status: dict[str, int] = {}
        for read in reads:
            by_status[read.status] = by_status.get(read.status, 0) + 1

        killed = settings.kill_switch_path.exists()
        print(f"index            : {settings.index_symbol}")
        print(f"cadence          : {settings.tick_interval_seconds}s")
        print(f"model            : {settings.regime_model}")
        print(f"prompt set       : {PromptRegistry.load(settings.prompts_dir).set_version}")
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
        print(f"decisions {day}: {len(decisions)}")
        for reason, count in sorted(by_reason.items()):
            print(f"  {reason:<26} {count}")
        cost = journal.token_cost_micros(day)
        print(f"regime reads {day}: {len(reads)}  (${cost / 1_000_000:.4f})")
        for status, count in sorted(by_status.items()):
            print(f"  {status:<26} {count}")
    finally:
        journal.close()
    return 0


def _cmd_journal(settings: Settings, args: argparse.Namespace) -> int:
    journal = Journal(settings.db_path)
    try:
        journal.create_schema()
        day = args.day or _today()
        for decision in journal.list_decisions(day, limit=args.limit):
            flag = "" if decision.trace_complete else "  [TRACE INCOMPLETE]"
            print(
                f"{decision.created_at_utc.isoformat()}  {decision.outcome:<8}"
                f"{decision.reason_code:<26} {decision.latency_ms:>5}ms  "
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
        print(f"tool calls : {payload.get('tool_call_count')} "
              f"({payload.get('tool_error_count')} failed)")
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
    journal_parser.add_argument("--day", help="IST trading day as YYYY-MM-DD")
    journal_parser.add_argument("--limit", type=int, default=50)

    args = parser.parse_args(argv)
    settings = get_settings()
    handlers = {
        "run": _cmd_run,
        "tick-now": _cmd_tick_now,
        "kill": _cmd_kill,
        "resume": _cmd_resume,
        "status": _cmd_status,
        "regime": _cmd_regime,
        "journal": _cmd_journal,
    }
    return handlers[args.command](settings, args)


if __name__ == "__main__":
    raise SystemExit(main())
