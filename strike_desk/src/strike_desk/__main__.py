"""Command-line surface for the Strike Desk service."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import uuid
from datetime import UTC, date, datetime, timedelta

from .config import IST, Settings, get_settings
from .errors import McpUnavailable, ModelCallFailed, StrikeDeskError
from .journal import Journal
from .mcp_toolbox import McpToolbox
from .observability import Redactor, configure_logging, configure_tracing, get_tracer
from .prompt_registry import PromptRegistry
from .regime_analyst import build_regime_analyst, build_regime_read_row
from .service import StrikeDeskService
from .session import SessionGate
from .specialists import SpecialistRequest
from .ltp_coa_live import CoaLiveRunner
from .strikeshield import StrikeShieldRunner
from .tradetron_hands import CoaHands, TradetronHands


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
        print(
            f"strikeshield     : "
            f"{'enabled' if settings.strikeshield_enabled else 'off'} "
            f"({settings.strikeshield_interval_seconds}s to Tradetron)"
        )
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


def _default_replay_window() -> tuple[str, str]:
    end = datetime.now(tz=IST).date() - timedelta(days=1)
    start = end - timedelta(days=6)
    return start.isoformat(), end.isoformat()


def _cmd_coa_replay(settings: Settings, args: argparse.Namespace) -> int:
    from .errors import OpenAlgoError
    from .ltp_coa_replay import build_tape, default_weekly_expiry, format_report, replay
    from .openalgo_client import OpenAlgoClient
    from .session import SessionGate

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    secrets = [settings.openalgo_api_key.get_secret_value()]
    if settings.anthropic_api_key is not None:
        secrets.append(settings.anthropic_api_key.get_secret_value())
    redactor = Redactor(secrets)
    configure_logging(settings, redactor)

    client = OpenAlgoClient(settings)
    gate = SessionGate(client, settings)

    def expiry_for(day: date) -> date:
        return gate.expiry_date_for(day) or default_weekly_expiry(day, settings.expiry_weekday)

    def fetch(symbol, exchange, interval, start_date, end_date, source="api"):
        try:
            return client.history(
                symbol, exchange, interval, start_date, end_date, source=source
            )
        except OpenAlgoError:
            return []

    try:
        print(
            f"CoA 5m replay {settings.index_symbol} {start.isoformat()} -> {end.isoformat()} "
            f"source={args.source}"
        )
        tape = build_tape(
            client,
            start,
            end,
            underlying=settings.index_symbol,
            spot_exchange=settings.index_spot_exchange,
            option_exchange=settings.option_exchange,
            strike_count=args.strikes,
            source=args.source,
            expiry_for=expiry_for,
            history=fetch,
        )
        report = replay(tape, underlying=settings.index_symbol)
        print(format_report(report))
        if not tape:
            print("no 5m bars in the window - check OpenAlgo history / Historify", file=sys.stderr)
            return 2
        return 0
    except OpenAlgoError as exc:
        print(f"OpenAlgo read failed: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


def _cmd_strikeshield(settings: Settings, args: argparse.Namespace) -> int:
    token = (
        settings.tt_auth_token.get_secret_value().strip()
        if settings.tt_auth_token is not None
        else ""
    )
    if not token:
        print("STRIKE_DESK_TT_AUTH_TOKEN is required for StrikeShield", file=sys.stderr)
        return 1
    secrets = [settings.openalgo_api_key.get_secret_value(), token]
    if settings.anthropic_api_key is not None:
        secrets.append(settings.anthropic_api_key.get_secret_value())
    redactor = Redactor(secrets)
    configure_logging(settings, redactor)
    from .openalgo_client import OpenAlgoClient

    client = OpenAlgoClient(settings)
    gate = SessionGate(client, settings)
    runner = StrikeShieldRunner(
        settings,
        client,
        TradetronHands(settings),
        is_trading_day=gate.is_trading_day,
        expiry_date_for=gate.expiry_date_for,
        trading_window=gate.trading_window,
    )
    try:
        if args.once:
            sent = runner.step()
            print("strikeshield once:", ", ".join(sent) if sent else "hold")
            return 0
        print("StrikeShield running; Ctrl-C to stop")
        while True:
            runner.step()
            time.sleep(settings.strikeshield_interval_seconds)
    except KeyboardInterrupt:
        print("strikeshield stopped")
        return 0
    finally:
        client.close()


def _cmd_coa_brief(settings: Settings, _args: argparse.Namespace) -> int:
    """Write the public /ltp brief. Does not talk to Tradetron Hands."""
    secrets = [settings.openalgo_api_key.get_secret_value()]
    if settings.anthropic_api_key is not None:
        secrets.append(settings.anthropic_api_key.get_secret_value())
    redactor = Redactor(secrets)
    configure_logging(settings, redactor)
    from .ltp_coa import evaluate
    from .ltp_coa_brief import build_brief, update_tape, write_public_brief
    from .ltp_coa_replay import expiry_token
    from .openalgo_client import OpenAlgoClient

    client = OpenAlgoClient(settings)
    gate = SessionGate(client, settings)
    now = datetime.now(tz=IST)
    try:
        expiry_day = gate.expiry_date_for(now.date())
        if expiry_day is None:
            print("coa brief: no expiry", file=sys.stderr)
            return 1
        payload_chain = client.optionchain(
            settings.index_symbol,
            settings.index_spot_exchange,
            expiry_token(expiry_day),
            strike_count=10,
        )
        chain = payload_chain.get("chain") or []
        spot = float(payload_chain.get("underlying_ltp") or 0)
        atm = float(payload_chain.get("atm_strike") or 0)
        if spot <= 0 or atm <= 0 or not chain:
            print("coa brief: empty chain", file=sys.stderr)
            return 1
        snap = evaluate(chain, spot, atm=atm)
        tape = update_tape(None, snap, now)
        brief = build_brief(snap, now, tape)
        write_public_brief(settings.public_brief_path, brief)
        print(brief["as_of_ist"], brief["stance"])
        print(brief["body"])
        for row in brief["opportunities"]:
            print(f"- {row['side']} {row['status']}: {row['trigger']}")
        print("wrote", settings.public_brief_path)
        return 0
    finally:
        client.close()


def _cmd_bar_continuation_bt(settings: Settings, args: argparse.Namespace) -> int:
    from .bar_continuation import (
        backtest,
        fetch_yahoo_nifty,
        format_report,
        parse_history_rows,
        session_bars,
    )
    from .errors import OpenAlgoError
    from .openalgo_client import OpenAlgoClient

    end = (
        date.fromisoformat(args.end)
        if args.end
        else datetime.now(tz=IST).date() - timedelta(days=1)
    )
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=59)
    bars = []
    source = args.source
    if source != "yahoo":
        client = OpenAlgoClient(settings)
        try:
            rows = client.history(
                settings.index_symbol,
                settings.index_spot_exchange,
                args.interval,
                start,
                end,
                source=source,
            )
            bars = parse_history_rows(rows)
        except OpenAlgoError:
            bars = []
        finally:
            client.close()
    if not bars:
        source = "yahoo"
        bars = fetch_yahoo_nifty(args.interval)
    bars = session_bars(bars, args.interval)
    if not bars:
        print("no NIFTY bars", file=sys.stderr)
        return 2
    report = backtest(
        bars,
        symbol=settings.index_symbol,
        interval=args.interval,
        flatten_eod=args.interval not in {"D", "1d", "day"},
    )
    first, last = bars[0].ts, bars[-1].ts
    print(
        f"bar-continuation {settings.index_symbol} {args.interval} "
        f"{first.date().isoformat()} -> {last.date().isoformat()} source={source}"
    )
    print(format_report(report))
    return 0


def _cmd_ofa(settings: Settings, args: argparse.Namespace) -> int:
    from .errors import OpenAlgoError
    from .ofa_analyzer import OfaAnalyzer
    from .ofa_hands import OfaHands
    from .ofa_live import OfaRunner, render
    from .openalgo_client import OpenAlgoClient

    if args.symbol:
        settings.ofa_symbol = args.symbol
    if args.exchange:
        settings.ofa_exchange = args.exchange
    secrets = [settings.openalgo_api_key.get_secret_value()]
    token = (
        settings.tt_auth_token_ofa.get_secret_value().strip()
        if settings.tt_auth_token_ofa is not None
        else ""
    )
    if token:
        secrets.append(token)
    redactor = Redactor(secrets)
    configure_logging(settings, redactor)
    client = OpenAlgoClient(settings)
    analyzer = OfaAnalyzer(settings, client)
    hands = OfaHands(settings) if token else None
    gate = SessionGate(client, settings)
    runner = OfaRunner(
        settings,
        client,
        hands=hands,
        analyzer=analyzer,
        expiry_date_for=gate.expiry_date_for,
        is_trading_day=gate.is_trading_day,
        trading_window=gate.trading_window,
    )
    try:
        if args.once:
            snap = runner.step()
            print(render(snap, runner.last_actions))
            return 0
        print("OFA mind + Analyzer + Hands running; Ctrl-C to stop")
        while True:
            try:
                snap = runner.step()
                print(render(snap, runner.last_actions))
            except OpenAlgoError as exc:
                print(f"OFA read failed: {exc}", file=sys.stderr)
            time.sleep(settings.ofa_interval_seconds)
    except KeyboardInterrupt:
        print("ofa stopped")
        return 0
    except OpenAlgoError as exc:
        print(f"OFA read failed: {exc}", file=sys.stderr)
        return 1
    finally:
        analyzer.close()
        client.close()


def _cmd_coa_live(settings: Settings, args: argparse.Namespace) -> int:
    token = (
        settings.tt_auth_token_coa.get_secret_value().strip()
        if settings.tt_auth_token_coa is not None
        else ""
    )
    if not token:
        print("STRIKE_DESK_TT_AUTH_TOKEN_COA is required for CoA Hands", file=sys.stderr)
        return 1
    secrets = [settings.openalgo_api_key.get_secret_value(), token]
    if settings.anthropic_api_key is not None:
        secrets.append(settings.anthropic_api_key.get_secret_value())
    redactor = Redactor(secrets)
    configure_logging(settings, redactor)
    from .openalgo_client import OpenAlgoClient

    client = OpenAlgoClient(settings)
    gate = SessionGate(client, settings)
    runner = CoaLiveRunner(
        settings,
        client,
        CoaHands(settings),
        is_trading_day=gate.is_trading_day,
        expiry_date_for=gate.expiry_date_for,
        trading_window=gate.trading_window,
    )
    try:
        if args.once:
            sent = runner.step()
            print("coa live once:", ", ".join(sent) if sent else "hold")
            return 0
        print("CoA live running; Ctrl-C to stop")
        while True:
            runner.step()
            time.sleep(settings.coa_interval_seconds)
    except KeyboardInterrupt:
        print("coa live stopped")
        return 0
    finally:
        client.close()


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

    shield_parser = subparsers.add_parser(
        "strikeshield",
        help="run StrikeShield (QuadShield mind to Tradetron hands)",
    )
    shield_parser.add_argument(
        "--once",
        action="store_true",
        help="evaluate one step and exit",
    )

    replay_start, replay_end = _default_replay_window()
    replay_parser = subparsers.add_parser(
        "coa-replay",
        help="replay Chart of Accuracy 1.0 on 5-minute NIFTY option chains",
    )
    replay_parser.add_argument("--start", default=replay_start, help="YYYY-MM-DD (IST)")
    replay_parser.add_argument("--end", default=replay_end, help="YYYY-MM-DD (IST)")
    replay_parser.add_argument("--strikes", type=int, default=10, help="ATM ± N strikes")
    replay_parser.add_argument(
        "--source",
        default="api",
        choices=("api", "db"),
        help="OpenAlgo history source: broker API or Historify db",
    )

    subparsers.add_parser(
        "coa-brief",
        help="write the public Chart of Accuracy brief for zerotouchalgo.com/ltp",
    )

    coa_parser = subparsers.add_parser(
        "coa-live",
        help="run CoA mind to the separate Tradetron Hands book",
    )
    coa_parser.add_argument("--once", action="store_true", help="evaluate one step and exit")

    bt_parser = subparsers.add_parser(
        "bar-continuation-bt",
        help="backtest last-bar continuation + ADX/RSI/EMA on NIFTY (mind only)",
    )
    bt_parser.add_argument("--interval", default="15m", help="15m, 5m, or 1d")
    bt_parser.add_argument("--start", default="", help="YYYY-MM-DD (OpenAlgo history)")
    bt_parser.add_argument("--end", default="", help="YYYY-MM-DD (OpenAlgo history)")
    bt_parser.add_argument(
        "--source",
        default="api",
        choices=("api", "db", "yahoo"),
        help="OpenAlgo api/db, or Yahoo ^NSEI fallback",
    )

    ofa_parser = subparsers.add_parser(
        "ofa",
        help="Order Flow Analysis: depth mind, Analyzer fill, Tradetron Hands",
    )
    ofa_parser.add_argument("--once", action="store_true", help="one depth snapshot and exit")
    ofa_parser.add_argument("--symbol", default="", help="override OFA futures symbol")
    ofa_parser.add_argument("--exchange", default="", help="override exchange (default NFO)")

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
        "strikeshield": _cmd_strikeshield,
        "coa-replay": _cmd_coa_replay,
        "coa-brief": _cmd_coa_brief,
        "coa-live": _cmd_coa_live,
        "bar-continuation-bt": _cmd_bar_continuation_bt,
        "ofa": _cmd_ofa,
    }
    return handlers[args.command](settings, args)


if __name__ == "__main__":
    raise SystemExit(main())
