"""Fixtures and a seeding CLI for the position-monitor tests."""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from strike_desk.config import IST, Settings, get_settings
from strike_desk.journal import POSITION_FLAT, SCHEMA_VERSION, Journal
from strike_desk.levels import ExitLevels

STRATEGY = "strike-desk-NIFTY"
SYMBOL = "NIFTY30SEP2625000CE"
SEED_DAY = "2026-09-08"


def levels(
    stop: float = 80.0,
    target: float = 140.0,
    minutes_ahead: float = 30.0,
    reason: str = "time-stop",
) -> ExitLevels:
    return ExitLevels(
        stop_price=stop,
        target_price=target,
        time_stop_utc=datetime.now(tz=UTC) + timedelta(minutes=minutes_ahead),
        time_stop_reason=reason,
    )


def today() -> str:
    return datetime.now(tz=IST).date().isoformat()


def seed_chain(
    journal: Journal,
    *,
    stop: float = 80.0,
    target: float = 140.0,
    time_stop_ist: str = "14:45",
    quantity: int = 75,
    symbol: str = SYMBOL,
) -> dict[str, str]:
    """Write the proposal, approval and order rows one adoption needs, and return the ids."""
    now = datetime.now(tz=UTC)
    day = today()
    tick_id = f"test:{uuid.uuid4()}"
    trace_id = uuid.uuid4().hex[:32]
    proposal_id = str(uuid.uuid4())
    approval_id = str(uuid.uuid4())
    order_id = str(uuid.uuid4())

    journal.record_proposal(
        proposal_id=proposal_id,
        tick_id=tick_id,
        trace_id=trace_id,
        created_at_utc=now,
        trading_day=day,
        index_symbol="NIFTY",
        source="tick",
        status="proposed",
        regime_label="trending",
        regime_confidence=0.8,
        direction="bullish",
        symbol=symbol,
        expiry="2026-09-30",
        strike=25000.0,
        option_type="CE",
        lots=1,
        lot_size=quantity,
        quantity=quantity,
        entry_price_low=100.0,
        entry_price_high=105.0,
        delta=0.45,
        theta_per_day=-4.2,
        implied_volatility=14.5,
        open_interest=250000,
        breakeven=25105.0,
        stop_price=stop,
        target_price=target,
        time_stop_ist=time_stop_ist,
        rationale="seeded by the test fixtures for the position monitor",
        evidence_json="[]",
        playbook_artifact="pb-1",
        playbook_verdict="pass",
        violations_json="[]",
        defect=None,
        tool_call_count=0,
        tool_error_count=0,
        rejected_tool_count=0,
        model_calls=0,
        model_version="test",
        input_tokens=0,
        output_tokens=0,
        token_cost_micros=0,
        prompt_name="options_strategist",
        prompt_version="v1",
        prompt_digest="0" * 64,
        prompt_set_version="ps-1",
        latency_ms=0,
        schema_version=SCHEMA_VERSION,
    )
    journal.record_approval(
        approval_id=approval_id,
        status="approved",
        tick_id=tick_id,
        trace_id=trace_id,
        tick_trace_id=trace_id,
        proposal_id=proposal_id,
        verdict_id=None,
        created_at_utc=now,
        trading_day=day,
        index_symbol="NIFTY",
        symbol=symbol,
        exchange="NFO",
        action="BUY",
        product="MIS",
        lots=1,
        lot_size=quantity,
        quantity=quantity,
        limit_price=105.0,
        band_low=100.0,
        band_high=105.0,
        pending_order_id=1,
        strategy_tag=STRATEGY,
        deadline_utc=now + timedelta(minutes=5),
        wait_seconds=3.0,
        approved_by="tester",
        resolved_at_ist=None,
        broker_order_id="B-1",
        withdrawal=None,
        detail="seeded",
        payload_json="{}",
        response_json="{}",
        defect=False,
    )
    journal.record_order(
        order_id=order_id,
        approval_id=approval_id,
        tick_id=tick_id,
        trace_id=trace_id,
        created_at_utc=now,
        trading_day=day,
        pending_order_id=1,
        broker_order_id="B-1",
        symbol=symbol,
        exchange="NFO",
        action="BUY",
        product="MIS",
        price_type="LIMIT",
        limit_price=105.0,
        lots=1,
        lot_size=quantity,
        quantity=quantity,
        order_status="complete",
        average_price=102.5,
        raw_json="{}",
    )
    return {
        "proposal_id": proposal_id,
        "approval_id": approval_id,
        "order_id": order_id,
        "tick_id": tick_id,
    }


def seed_flat_loss(journal: Journal, *, realised: float = -600.0, day: str = SEED_DAY) -> str:
    """One flat position with a realised loss on the given trading day."""
    position_id = str(uuid.uuid4())
    journal.record_position_state(
        position_id=position_id,
        state=POSITION_FLAT,
        order_id=None,
        approval_id=None,
        proposal_id=None,
        tick_id=None,
        trace_id="0" * 32,
        created_at_utc=datetime.now(tz=UTC),
        trading_day=day,
        symbol=SYMBOL,
        exchange="NFO",
        product="MIS",
        quantity=75,
        entry_price=102.5,
        stop_price=80.0,
        target_price=140.0,
        time_stop_utc=datetime.now(tz=UTC),
        theta_per_day=-4.2,
        exit_reason="stop",
        realised_pnl=realised,
        detail="seeded flat loss",
        defect=False,
    )
    return position_id


def seed_two_entries(journal: Journal, *, day: str = SEED_DAY) -> list[str]:
    """Two BUY order rows on the given trading day."""
    ids: list[str] = []
    for index in range(2):
        order_id = str(uuid.uuid4())
        journal.record_order(
            order_id=order_id,
            approval_id=str(uuid.uuid4()),
            tick_id=f"test:entry-{index}",
            trace_id="0" * 32,
            created_at_utc=datetime.now(tz=UTC),
            trading_day=day,
            pending_order_id=index + 1,
            broker_order_id=f"B-entry-{index}",
            symbol=SYMBOL,
            exchange="NFO",
            action="BUY",
            product="MIS",
            price_type="LIMIT",
            limit_price=105.0,
            lots=1,
            lot_size=75,
            quantity=75,
            order_status="complete",
            average_price=102.5,
            raw_json="{}",
        )
        ids.append(order_id)
    return ids


class FakeFeed:
    """Stands in for PriceFeed: the monitor only ever asks it for the last tick."""

    def __init__(self, tick: Any = None) -> None:
        self.tick = tick
        self.closed = False
        self.started = False

    def start(self) -> None:
        self.started = True

    def wait_for_first_tick(self, timeout: float) -> bool:
        return self.tick is not None

    def last_tick(self) -> Any:
        return self.tick

    def is_connected(self) -> bool:
        return not self.closed

    def close(self) -> None:
        self.closed = True


def _seed_cli(args: argparse.Namespace) -> int:
    settings: Settings = get_settings()
    journal = Journal(settings.db_path)
    try:
        journal.create_schema()
        ids = seed_chain(
            journal,
            stop=args.stop_offset and float(args.premium - args.stop_offset) or 80.0,
            target=float(args.premium + args.target_offset),
            time_stop_ist=args.time_stop,
        )
        print(json.dumps(ids, indent=2))
    finally:
        journal.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="position_fixtures")
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed", help="write a proposal/approval/order chain to the journal")
    seed.add_argument("--premium", type=float, default=100.0)
    seed.add_argument("--stop-offset", type=float, default=2.0)
    seed.add_argument("--target-offset", type=float, default=40.0)
    seed.add_argument("--time-stop", default="14:45")
    args = parser.parse_args(argv)
    return _seed_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
