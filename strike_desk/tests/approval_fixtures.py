"""OpenAlgo-shaped fixtures for the approval gate: a mirror database and a cleared tick.

The recorded contract is iteration 04's: a Rs 192.00 entry against a Rs 152.00 stop over 75
units, so one lot risks Rs 3,000 and deploys Rs 14,400 of premium.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strike_desk.approval_gate import TickContext
from strike_desk.config import IST
from strike_desk.openalgo_mirror import ApiKeyRow, MirrorBase, PendingOrderRow

from .chain_fixtures import LOT_SIZE, SYMBOL, valid_proposal

USER = "amit"
TRACE = "0" * 32
BAND_LOW = 188.0
BAND_HIGH = 192.0
STOP = 152.0

#: Rs 3,000 of risk and Rs 14,400 of premium per lot, at the fixture's lot size.
RISK_PER_LOT = (BAND_HIGH - STOP) * LOT_SIZE
PREMIUM_PER_LOT = BAND_HIGH * LOT_SIZE


def _ist_stamp(moment: datetime | None = None) -> str:
    when = (moment or datetime.now(tz=UTC)).astimezone(IST)
    return when.strftime("%Y-%m-%d %H:%M:%S IST")


# --- the mirror database ---------------------------------------------------


def make_openalgo_db(path: Path, *, order_mode: str = "semi_auto", user: str = USER) -> Path:
    """Create an OpenAlgo-shaped database with one api_keys row."""
    engine = create_engine(f"sqlite:///{path}", future=True)
    MirrorBase.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add(ApiKeyRow(user_id=user, order_mode=order_mode))
        session.commit()
    engine.dispose()
    return path


def _writer(path: Path):
    engine = create_engine(f"sqlite:///{path}", future=True)
    return engine, sessionmaker(bind=engine)


def set_order_mode(path: Path, mode: str, *, user: str = USER) -> None:
    engine, maker = _writer(path)
    with maker() as session:
        row = session.query(ApiKeyRow).filter_by(user_id=user).one()
        row.order_mode = mode
        session.commit()
    engine.dispose()


def queue_pending_order(path: Path, *, user: str = USER, order_data: dict | None = None) -> int:
    """Write the row OpenAlgo's order router would write, and return its id."""
    engine, maker = _writer(path)
    with maker() as session:
        row = PendingOrderRow(
            user_id=user,
            api_type="placeorder",
            order_data=json.dumps(order_data or {"symbol": SYMBOL, "quantity": LOT_SIZE}),
            status="pending",
            created_at_ist=_ist_stamp(),
        )
        session.add(row)
        session.commit()
        pending_order_id = int(row.id)
    engine.dispose()
    return pending_order_id


def approve_pending(
    path: Path,
    pending_order_id: int,
    *,
    by: str = USER,
    broker_order_id: str | None = "24090100000041",
    broker_status: str = "open",
) -> None:
    engine, maker = _writer(path)
    with maker() as session:
        row = session.get(PendingOrderRow, pending_order_id)
        row.status = "approved"
        row.approved_by = by
        row.approved_at_ist = _ist_stamp()
        row.broker_order_id = broker_order_id
        row.broker_status = broker_status
        session.commit()
    engine.dispose()


def reject_pending(
    path: Path, pending_order_id: int, *, by: str = USER, reason: str = "strike too far OTM"
) -> None:
    engine, maker = _writer(path)
    with maker() as session:
        row = session.get(PendingOrderRow, pending_order_id)
        row.status = "rejected"
        row.rejected_by = by
        row.rejected_at_ist = _ist_stamp()
        row.rejected_reason = reason
        session.commit()
    engine.dispose()


def delete_pending(path: Path, pending_order_id: int) -> None:
    engine, maker = _writer(path)
    with maker() as session:
        session.delete(session.get(PendingOrderRow, pending_order_id))
        session.commit()
    engine.dispose()


# --- a cleared tick --------------------------------------------------------


def cleared_proposal(**overrides: Any) -> dict[str, Any]:
    """The recorded contract as the strategist's payload carries it."""
    payload = valid_proposal().model_dump()
    payload.update(
        {
            "symbol": SYMBOL,
            "lot_size": LOT_SIZE,
            "lots": 2,
            "quantity": 2 * LOT_SIZE,
            "entry_price_low": BAND_LOW,
            "entry_price_high": BAND_HIGH,
            "stop_price": STOP,
        }
    )
    payload.update(overrides)
    return payload


def cleared_risk(lots: int = 2, **overrides: Any) -> dict[str, Any]:
    """A pass verdict at ``lots``, in the shape ``RiskVerdict.as_dict`` emits."""
    verdict = {
        "verdict": "pass",
        "checks": [],
        "tripped": None,
        "lots_requested": 2,
        "lots_cleared": lots,
        "capital_base": 1_500_000.0,
        "premium_at_risk": PREMIUM_PER_LOT * lots,
        "max_loss_at_stop": RISK_PER_LOT * lots,
        "session_stop": False,
        "detail": "every limit has headroom",
    }
    verdict.update(overrides)
    return verdict


def cleared_context(
    *, tick_id: str = "tick-0001", lots: int = 2, proposal: dict | None = None
) -> TickContext:
    return TickContext(
        tick_id=tick_id,
        trace_id=TRACE,
        trading_day=datetime.now(tz=IST).date().isoformat(),
        proposal=proposal if proposal is not None else cleared_proposal(),
        risk=cleared_risk(lots),
        proposal_id="proposal-0001",
        verdict_id="verdict-0001",
    )


def proposed_payload() -> dict[str, Any]:
    """A scripted strategist answer, in the shape ``_record_proposal`` reads."""
    return {
        "status": "proposed",
        "proposal": cleared_proposal(),
        "rationale": "Trending read with the chain wide open at the 24500 strike.",
        "evidence": [{"tool": "get_option_chain", "field": "ask", "value": "192.00"}],
        "verdict": "pass",
        "violations": [],
        "tool_call_count": 3,
        "tool_error_count": 0,
        "model_calls": 2,
    }


def regime_payload(label: str = "trending", confidence: float = 0.8) -> dict[str, Any]:
    return {
        "status": "ok",
        "label": label,
        "confidence": confidence,
        "rationale": "Higher highs on the 15m with VIX steady.",
        "evidence": [{"tool": "get_quotes", "field": "ltp", "value": "24512.35"}],
        "tool_call_count": 2,
        "tool_error_count": 0,
        "model_calls": 2,
    }


def queue_one_intent(gate, **kwargs: Any):
    """Submit one cleared intent through a real gate and return the SubmitResult."""
    return gate.submit(cleared_context(**kwargs))


if __name__ == "__main__":  # pragma: no cover - the manual harness of 03_manual_test_cases.md
    from strike_desk.approval_gate import ApprovalGate
    from strike_desk.config import get_settings
    from strike_desk.execution_client import ExecutionClient
    from strike_desk.journal import Journal
    from strike_desk.openalgo_mirror import OpenAlgoMirror

    if sys.argv[1:2] != ["queue"]:
        raise SystemExit("usage: python -m tests.approval_fixtures queue")
    live_settings = get_settings()
    live_journal = Journal(live_settings.db_path)
    live_journal.create_schema()
    live_mirror = OpenAlgoMirror(live_settings)
    live_client = ExecutionClient(live_settings)
    try:
        result = ApprovalGate(live_settings, live_journal, live_mirror, live_client).submit(
            cleared_context(tick_id=f"manual-{datetime.now(tz=UTC):%H%M%S}")
        )
        print(f"{result.status}: {result.detail}")
    finally:
        live_client.close()
        live_mirror.close()
        live_journal.close()
