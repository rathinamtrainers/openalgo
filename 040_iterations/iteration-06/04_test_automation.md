# Iteration 06 — Test Automation

> **Document:** the suite that holds UC-06 in place. `03_manual_test_cases.md` is what you
> check once with your hands; this is what CI checks on every push forever.

## 1. Framework, and what is real

Same framework as iterations 01 to 05: `pytest`, `respx` for OpenAlgo's HTTP surface, a real
SQLite journal in a `tmp_path`, a real compiled LangGraph with a real `SqliteSaver`, and
`freezegun` where a deadline has to pass without the suite waiting for it.

What is different here is that the *other* database is real too. `pending_orders` and
`api_keys` are created in a temporary file from the mirror's own declarative models and filled
with the rows OpenAlgo would write, so the mirror is exercised against a database rather than
against a double, and the read-only guarantee is proved by trying to write and being refused.
The one thing a temporary file cannot prove is that OpenAlgo's real table still has these
column names — a schema drift upstream would pass here and fail on the host — so MT-23 in the
manual cases reads the real database with the real service user, and that check is not
optional.

**This slice runs no agent loop and makes no model call**, so it adds no eval set: the regime
and strategist evals iterations 02 and 04 built are untouched and still gate the reasoning
plane. What gates *this* slice in CI is the guardrail file and a frozen regression set of
approval scenarios — the deterministic analogue of an eval, where the fixed input is a
`pending_orders` state and the pass bar is the exact settlement it must produce.

Six files are new and four are extended:

| File | What it proves |
| --- | --- |
| `tests/approval_fixtures.py` | An OpenAlgo-shaped mirror database, a cleared tick context, and the scripted specialist answers. |
| `tests/test_openalgo_mirror.py` | Reads, read-only-ness, and every way the gate can be unhealthy. |
| `tests/test_execution_client.py` | The whitelist, payload validation, the no-retry rule on placement, and how a refusal is classified. |
| `tests/test_approval_gate.py` | Price alignment, intent arithmetic, the preflight, the bypass, and idempotent settlement. |
| `tests/test_approval_watcher.py` | Every resolution, the late click, the order follow, the kill switch, and the fallback path. |
| `tests/test_guardrails_execution.py` | The five structural guarantees that must fail loudly: no autonomous order, no path outside the whitelist, no second intent, no write to OpenAlgo, no duplicate settlement. |
| `tests/test_tick_approval.py` | The graph end to end: `enter` to queued to settled, and the two `plan` gates. |
| `tests/regression/approval_scenarios.json` | The frozen set: queue state in, settlement out. |
| `tests/conftest.py` | Six fixtures and one edit. |
| `tests/test_journal_migration.py` | The two new tables and their uniqueness. |

## 2. Fixtures

### `strike_desk/tests/conftest.py` — edits

One change to an existing fixture, so a test can move the account without fighting respx's
route matching: serve funds from the module dict rather than from a captured literal.

```python
        router.post("/api/v1/funds").mock(
            side_effect=lambda request: httpx.Response(
                200, json={"status": "success", "data": dict(FUNDS)}
            )
        )
```

And six fixtures at the end of the file:

```python
@pytest.fixture
def rich_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Rs 15,00,000 capital base — two lots of the recorded contract clear every limit."""
    monkeypatch.setitem(FUNDS, "availablecash", "1200000.00")
    monkeypatch.setitem(FUNDS, "utiliseddebits", "300000.00")


@pytest.fixture
def openalgo_db(tmp_path) -> Path:
    """An OpenAlgo database holding one semi-auto api_keys row and no pending orders."""
    from .approval_fixtures import make_openalgo_db

    return make_openalgo_db(tmp_path / "openalgo.db")


@pytest.fixture
def execution_settings(settings: Settings, openalgo_db: Path) -> Settings:
    from .approval_fixtures import USER

    return settings.model_copy(
        update={
            "execution_enabled": True,
            "openalgo_user": USER,
            "openalgo_db_path": openalgo_db,
            "approval_deadline_seconds": 300,
            "approval_poll_seconds": 1,
            "fill_deadline_seconds": 300,
        }
    )


@pytest.fixture
def mirror(execution_settings: Settings):
    from strike_desk.openalgo_mirror import OpenAlgoMirror

    mirror = OpenAlgoMirror(execution_settings)
    yield mirror
    mirror.close()


@pytest.fixture
def execution_client(execution_settings: Settings, openalgo):
    from strike_desk.execution_client import ExecutionClient

    client = ExecutionClient(execution_settings)
    yield client
    client.close()


@pytest.fixture
def gate(execution_settings, journal, mirror, execution_client):
    from strike_desk.approval_gate import ApprovalGate

    return ApprovalGate(execution_settings, journal, mirror, execution_client)


@pytest.fixture
def execution_deps(
    execution_settings, client, journal, registry, tracing, prompts, gate
) -> TickDeps:
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    connection = sqlite3.connect(str(execution_settings.checkpoint_path), check_same_thread=False)
    checkpointer = SqliteSaver(connection)
    checkpointer.setup()
    yield TickDeps(
        settings=execution_settings,
        client=client,
        journal=journal,
        registry=registry,
        prompts=prompts,
        span_processor=tracing,
        checkpointer=checkpointer,
        gate=gate,
    )
    connection.close()


@pytest.fixture
def execution_runner(execution_deps: TickDeps) -> TickRunner:
    return TickRunner(execution_deps, SessionGate(execution_deps.client, execution_deps.settings))
```

`Path` joins the conftest imports.

### `strike_desk/tests/approval_fixtures.py`

The mirror database is built from `MirrorBase.metadata`, which is the honest way round: if a
column name in `openalgo_mirror.py` is wrong, the fixture creates the wrong table and the test
still passes — so the fixture's job is not to prove the names but to give the reads something
to read. The names themselves are pinned by MT-23 against the real file.

`proposed_payload` is the single definition of a scripted strategist answer for this slice. If
`build_proposal_row` asks for a key it does not carry, add it here rather than in a test.

```python
"""OpenAlgo-shaped fixtures for the approval gate: a mirror database and a cleared tick.

The recorded contract is iteration 04's: a Rs 192.00 entry against a Rs 152.00 stop over 75
units, so one lot risks Rs 3,000 and deploys Rs 14,400 of premium.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
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
```

## 3. The mirror

### `strike_desk/tests/test_openalgo_mirror.py`

```python
"""The read-only window onto OpenAlgo: what it reads, and what it refuses to do."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from strike_desk.errors import MirrorUnavailable
from strike_desk.openalgo_mirror import OpenAlgoMirror

from .approval_fixtures import (
    USER,
    approve_pending,
    delete_pending,
    queue_pending_order,
    reject_pending,
    set_order_mode,
)


def test_semi_auto_is_the_only_healthy_mode(mirror, openalgo_db):
    assert mirror.order_mode() == "semi_auto"
    assert mirror.health().ok is True

    set_order_mode(openalgo_db, "auto")
    health = mirror.health()
    assert health.ok is False
    assert health.order_mode == "auto"
    assert "without a human approval" in health.detail


def test_a_missing_database_is_unhealthy_not_a_crash(execution_settings):
    settings = execution_settings.model_copy(
        update={"openalgo_db_path": execution_settings.state_dir / "nope.db"}
    )
    mirror = OpenAlgoMirror(settings)
    try:
        assert mirror.health().ok is False
        with pytest.raises(MirrorUnavailable):
            mirror.order_mode()
    finally:
        mirror.close()


def test_an_unconfigured_user_is_unhealthy(execution_settings):
    mirror = OpenAlgoMirror(execution_settings.model_copy(update={"openalgo_user": None}))
    try:
        assert "OPENALGO_USER" in mirror.health().detail
    finally:
        mirror.close()


def test_an_unknown_user_is_unhealthy(execution_settings):
    mirror = OpenAlgoMirror(execution_settings.model_copy(update={"openalgo_user": "someone"}))
    try:
        assert "no api_keys row" in mirror.health().detail
    finally:
        mirror.close()


def test_pending_order_states_read_back(mirror, openalgo_db):
    pending_order_id = queue_pending_order(openalgo_db)
    row = mirror.pending_order(pending_order_id)
    assert row.status == "pending" and row.resolved is False and row.user_id == USER

    approve_pending(openalgo_db, pending_order_id, by="amit")
    row = mirror.pending_order(pending_order_id)
    assert row.status == "approved" and row.resolved is True
    assert row.approved_by == "amit" and row.broker_order_id == "24090100000041"
    assert row.resolved_at_ist == row.approved_at_ist


def test_a_rejection_carries_its_reason(mirror, openalgo_db):
    pending_order_id = queue_pending_order(openalgo_db)
    reject_pending(openalgo_db, pending_order_id, reason="strike too far OTM")
    row = mirror.pending_order(pending_order_id)
    assert row.status == "rejected" and row.rejected_reason == "strike too far OTM"


def test_a_deleted_row_reads_as_none(mirror, openalgo_db):
    pending_order_id = queue_pending_order(openalgo_db)
    delete_pending(openalgo_db, pending_order_id)
    assert mirror.pending_order(pending_order_id) is None


def test_the_connection_cannot_write(mirror):
    """AC-13: the guarantee is the driver's, not our discipline's."""
    with pytest.raises(OperationalError, match="readonly database"):
        with mirror._session_scope() as session:  # noqa: SLF001 - proving the connection mode
            session.execute(text("UPDATE api_keys SET order_mode = 'auto'"))
            session.commit()
```

## 4. The execution client

### `strike_desk/tests/test_execution_client.py`

```python
"""Three paths, one validation pass, and the rule that a placement is never retried."""

from __future__ import annotations

import httpx
import pytest

from strike_desk.errors import ExecutionPathViolation, InvalidOrderPayload, OpenAlgoError

PAYLOAD = {
    "strategy": "strike-desk:abcd1234",
    "symbol": "NIFTY02SEP2624500CE",
    "exchange": "NFO",
    "action": "BUY",
    "quantity": 150,
    "pricetype": "LIMIT",
    "product": "MIS",
    "price": 192.0,
}


def test_only_three_paths_are_reachable(execution_client):
    """AC-5: refused before a socket is opened, so no route needs to be mocked."""
    for path in ("/api/v1/closeposition", "/api/v1/positionbook", "/api/v1/modifyorder"):
        with pytest.raises(ExecutionPathViolation):
            execution_client._request(path, {}, retries=0)  # noqa: SLF001


@pytest.mark.parametrize(
    "mutation",
    [
        {"action": "SHORT"},
        {"quantity": 0},
        {"quantity": 1.5},
        {"product": "BO"},
        {"pricetype": "GTT"},
        {"price": 0.0},
        {"symbol": "  "},
    ],
)
def test_a_malformed_payload_never_leaves(execution_client, openalgo, mutation):
    with pytest.raises(InvalidOrderPayload):
        execution_client.place_order({**PAYLOAD, **mutation})
    assert not any(call.request.url.path == "/api/v1/placeorder" for call in openalgo.calls)


def test_a_queued_placement_is_a_receipt(execution_client, openalgo):
    openalgo.post("/api/v1/placeorder").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "mode": "semi_auto",
                "pending_order_id": 41,
                "message": "Order queued for approval in Action Center",
            },
        )
    )
    receipt = execution_client.place_order(PAYLOAD)
    assert receipt.queued is True
    assert receipt.pending_order_id == 41
    assert receipt.broker_order_id is None


def test_a_placement_that_reached_the_broker_is_not_queued(execution_client, openalgo):
    """AC-3: the client classifies it; the gate is what stops the desk."""
    openalgo.post("/api/v1/placeorder").mock(
        return_value=httpx.Response(200, json={"status": "success", "orderid": "24090100000041"})
    )
    receipt = execution_client.place_order(PAYLOAD)
    assert receipt.queued is False
    assert receipt.pending_order_id is None
    assert receipt.broker_order_id == "24090100000041"


def test_a_placement_is_never_retried(execution_client, openalgo):
    """A repeat POST is a second order, so a 503 fails rather than retries."""
    route = openalgo.post("/api/v1/placeorder").mock(return_value=httpx.Response(503, json={}))
    with pytest.raises(OpenAlgoError):
        execution_client.place_order(PAYLOAD)
    assert route.call_count == 1


def test_order_status_returns_the_normalised_order(execution_client, openalgo):
    openalgo.post("/api/v1/orderstatus").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"orderid": "1", "order_status": "complete", "average_price": 191.85},
            },
        )
    )
    data = execution_client.order_status("1", "strike-desk:abcd1234")
    assert data["order_status"] == "complete" and data["average_price"] == 191.85


def test_a_refused_cancel_is_an_answer_not_an_exception(execution_client, openalgo):
    """OpenAlgo blocks cancelorder for a semi-auto API key; that is policy, not a fault."""
    openalgo.post("/api/v1/cancelorder").mock(
        return_value=httpx.Response(
            403,
            json={
                "status": "error",
                "message": "Cancel order operation is not allowed in Semi-Auto mode.",
            },
        )
    )
    permitted, detail = execution_client.cancel_order("1", "strike-desk:abcd1234")
    assert permitted is False and "Semi-Auto" in detail


def test_a_permitted_cancel_says_so(execution_client, openalgo):
    openalgo.post("/api/v1/cancelorder").mock(
        return_value=httpx.Response(200, json={"status": "success", "orderid": "1"})
    )
    permitted, detail = execution_client.cancel_order("1", "strike-desk:abcd1234")
    assert permitted is True and "cancelled" in detail
```

## 5. The gate

### `strike_desk/tests/test_approval_gate.py`

```python
"""Pricing, sizing, the preflight, the bypass, and settlement that runs twice safely."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from strike_desk.approval_gate import (
    WITHDRAWAL_NOT_QUEUED,
    Resolution,
    align_price,
    build_intent,
    settle_approval,
    strategy_tag,
)
from strike_desk.errors import InvalidOrderPayload, UnpriceableBand
from strike_desk.journal import (
    APPROVAL_APPROVED,
    APPROVAL_GATE_BYPASSED,
    APPROVAL_GATE_UNAVAILABLE,
    APPROVAL_PENDING,
    APPROVAL_SUBMIT_FAILED,
    APPROVAL_UNPRICEABLE,
)

from .approval_fixtures import (
    BAND_HIGH,
    BAND_LOW,
    cleared_context,
    cleared_proposal,
    set_order_mode,
)

QUEUED = {"status": "success", "mode": "semi_auto", "pending_order_id": 41}


def _queue_route(openalgo, body=None, status_code=200):
    return openalgo.post("/api/v1/placeorder").mock(
        return_value=httpx.Response(status_code, json=body if body is not None else QUEUED)
    )


@pytest.mark.parametrize(
    ("low", "high", "tick", "expected"),
    [
        (188.0, 192.0, 0.05, 192.0),
        (188.0, 192.03, 0.05, 192.0),
        (188.0, 192.049, 0.05, 192.0),
        (0.10, 0.14, 0.05, 0.10),
        (11.9, 12.0, 0.1, 12.0),
    ],
)
def test_the_limit_price_is_the_top_of_the_band_on_the_grid(low, high, tick, expected):
    """AC-4: floored to the tick, never above the band, exact in Decimal."""
    assert align_price(low, high, tick) == pytest.approx(expected)


@pytest.mark.parametrize(("low", "high"), [(192.01, 192.04), (192.06, 192.09), (192.0, 191.0)])
def test_a_band_with_no_tick_in_it_is_refused(low, high):
    with pytest.raises(UnpriceableBand):
        align_price(low, high, 0.05)


def test_the_quantity_comes_from_the_verdict_not_the_proposal(execution_settings):
    """AC-4: the model's own quantity is ignored; the cleared lot count is not."""
    context = cleared_context(lots=1, proposal=cleared_proposal(lots=2, quantity=150))
    intent = build_intent(context, execution_settings, datetime.now(tz=UTC))
    assert intent.lots == 1
    assert intent.quantity == intent.lot_size
    assert intent.payload()["quantity"] == intent.lot_size
    assert intent.payload()["pricetype"] == "LIMIT"
    assert intent.payload()["action"] == "BUY"
    assert intent.payload()["product"] == execution_settings.order_product
    assert intent.strategy == strategy_tag(execution_settings, context.tick_id)


def test_an_unusable_intent_raises_before_anything_is_built(execution_settings):
    with pytest.raises(InvalidOrderPayload):
        build_intent(cleared_context(lots=0), execution_settings, datetime.now(tz=UTC))


def test_a_queued_intent_is_journalled_once(gate, journal, openalgo, today):
    route = _queue_route(openalgo)
    result = gate.submit(cleared_context())

    assert route.call_count == 1
    assert result.status == APPROVAL_PENDING and result.pending_order_id == 41
    rows = journal.list_approvals(today)
    assert [row.status for row in rows] == [APPROVAL_PENDING]
    row = rows[0]
    assert row.pending_order_id == 41
    assert row.quantity == row.lots * row.lot_size
    assert row.limit_price == BAND_HIGH
    assert (row.band_low, row.band_high) == (BAND_LOW, BAND_HIGH)
    assert row.proposal_id == "proposal-0001" and row.verdict_id == "verdict-0001"
    assert row.defect is False


def test_auto_mode_stops_the_submission_before_the_wire(gate, journal, openalgo, openalgo_db, today):
    """AC-2: the preflight is a read, and a failed read means nothing is sent."""
    route = _queue_route(openalgo)
    set_order_mode(openalgo_db, "auto")

    result = gate.submit(cleared_context())

    assert route.call_count == 0
    assert result.status == APPROVAL_GATE_UNAVAILABLE
    row = journal.list_approvals(today)[0]
    assert row.status == APPROVAL_GATE_UNAVAILABLE and row.defect is True


def test_a_bypassed_gate_engages_the_kill_switch(gate, journal, openalgo, execution_settings, today):
    """AC-3: an order that reached a broker without a click stops the desk."""
    _queue_route(openalgo, {"status": "success", "orderid": "24090100000041"})

    result = gate.submit(cleared_context())

    assert result.status == APPROVAL_GATE_BYPASSED
    assert execution_settings.kill_switch_path.exists()
    assert "bypassed" in execution_settings.kill_switch_path.read_text(encoding="utf-8")
    row = journal.list_approvals(today)[0]
    assert row.status == APPROVAL_GATE_BYPASSED and row.defect is True
    assert row.broker_order_id == "24090100000041"


def test_a_failed_submission_is_journalled_not_retried(gate, journal, openalgo, today):
    route = _queue_route(openalgo, {"status": "error", "message": "broker session expired"})
    result = gate.submit(cleared_context())
    assert route.call_count == 1
    assert result.status == APPROVAL_SUBMIT_FAILED
    assert journal.list_approvals(today)[0].detail.endswith("broker session expired'")


def test_an_unpriceable_band_never_reaches_the_wire(gate, journal, openalgo, today):
    route = _queue_route(openalgo)
    result = gate.submit(cleared_context(proposal=cleared_proposal(
        entry_price_low=192.01, entry_price_high=192.04
    )))
    assert route.call_count == 0
    assert result.status == APPROVAL_UNPRICEABLE
    assert journal.list_approvals(today)[0].defect is True


def test_settlement_is_idempotent(gate, journal, openalgo, today):
    """AC-11: the second write is refused by the schema and read as already-settled."""
    _queue_route(openalgo)
    submitted = gate.submit(cleared_context())
    resolution = Resolution(
        approval_id=submitted.approval_id,
        tick_id="tick-0001",
        status=APPROVAL_APPROVED,
        detail="approved by amit",
        approved_by="amit",
        broker_order_id="24090100000041",
        order_status="open",
        wait_seconds=12.0,
    )

    assert settle_approval(journal, resolution, "1" * 32) is True
    assert settle_approval(journal, resolution, "1" * 32) is False

    rows = journal.list_approvals(today)
    assert [row.status for row in rows] == [APPROVAL_PENDING, APPROVAL_APPROVED]
    assert len(journal.orders_for_approval(submitted.approval_id)) == 1


def test_settling_an_unknown_approval_is_a_no_op(journal):
    assert settle_approval(
        journal,
        Resolution(approval_id="nope", tick_id="t", status=APPROVAL_APPROVED),
        "1" * 32,
    ) is False


def test_a_rejection_writes_no_order_row(gate, journal, openalgo, today):
    _queue_route(openalgo)
    submitted = gate.submit(cleared_context())
    settle_approval(
        journal,
        Resolution(
            approval_id=submitted.approval_id,
            tick_id="tick-0001",
            status="rejected",
            detail="rejected by amit: strike too far OTM",
            approved_by="amit",
            withdrawal=WITHDRAWAL_NOT_QUEUED,
        ),
        "1" * 32,
    )
    assert journal.orders_for_approval(submitted.approval_id) == []
```

## 6. The watcher

### `strike_desk/tests/test_approval_watcher.py`

```python
"""Every way an approval ends, and the two follow-up passes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from strike_desk.approval_gate import (
    WITHDRAWAL_NOT_QUEUED,
    WITHDRAWAL_PERMITTED,
    WITHDRAWAL_REFUSED,
)
from strike_desk.approval_watcher import ApprovalWatcher
from strike_desk.journal import (
    APPROVAL_APPROVED,
    APPROVAL_EXPIRED,
    APPROVAL_LATE,
    APPROVAL_REJECTED,
    APPROVAL_WITHDRAWN,
)

from .approval_fixtures import (
    approve_pending,
    cleared_context,
    delete_pending,
    reject_pending,
)


@pytest.fixture
def queued(gate, journal, openalgo, openalgo_db):
    """One real pending order in the mirror, and one real pending approval in the journal."""
    from .approval_fixtures import queue_pending_order

    pending_order_id = queue_pending_order(openalgo_db)
    openalgo.post("/api/v1/placeorder").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "mode": "semi_auto", "pending_order_id": pending_order_id},
        )
    )
    result = gate.submit(cleared_context())
    return result.approval_id, pending_order_id


@pytest.fixture
def watcher(execution_settings, journal, mirror, execution_client):
    """A watcher whose resume always fails, so the fallback path is what is under test."""
    resumed: list[tuple[str, dict]] = []

    def _resume(tick_id: str, resolution: dict) -> bool:
        resumed.append((tick_id, resolution))
        return False

    watcher = ApprovalWatcher(execution_settings, journal, mirror, execution_client, _resume)
    watcher.resumed = resumed  # type: ignore[attr-defined]
    return watcher


def _status_route(openalgo, status="open", average_price=0.0):
    return openalgo.post("/api/v1/orderstatus").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "orderid": "24090100000041",
                    "order_status": status,
                    "average_price": average_price,
                    "quantity": 150,
                },
            },
        )
    )


def test_a_waiting_approval_is_left_alone(watcher, queued, journal, today):
    assert watcher.poll_once() == 0
    assert [row.status for row in journal.list_approvals(today)] == ["pending"]


def test_an_approval_settles_with_its_order(watcher, queued, journal, openalgo, openalgo_db, today):
    """AC-6: identity, timestamp and the order read back, in one poll."""
    approval_id, pending_order_id = queued
    approve_pending(openalgo_db, pending_order_id, by="amit")
    _status_route(openalgo, status="open")

    assert watcher.poll_once() == 1

    rows = {row.status: row for row in journal.list_approvals(today)}
    settled = rows[APPROVAL_APPROVED]
    assert settled.approved_by == "amit"
    assert settled.resolved_at_ist and settled.resolved_at_ist.endswith("IST")
    assert settled.wait_seconds >= 0
    orders = journal.orders_for_approval(approval_id)
    assert [order.order_status for order in orders] == ["open"]
    assert orders[0].broker_order_id == "24090100000041"
    assert orders[0].quantity == settled.quantity
    assert watcher.resumed[0][0] == "tick-0001"  # the graph was asked first


def test_a_rejection_keeps_the_reason(watcher, queued, journal, openalgo_db, today):
    approval_id, pending_order_id = queued
    reject_pending(openalgo_db, pending_order_id, reason="strike too far OTM")

    watcher.poll_once()

    rows = {row.status: row for row in journal.list_approvals(today)}
    assert "strike too far OTM" in rows[APPROVAL_REJECTED].detail
    assert journal.orders_for_approval(approval_id) == []


def test_a_deleted_pending_row_settles_as_withdrawn(watcher, queued, journal, openalgo_db, today):
    _, pending_order_id = queued
    delete_pending(openalgo_db, pending_order_id)
    watcher.poll_once()
    assert {row.status for row in journal.list_approvals(today)} == {"pending", APPROVAL_WITHDRAWN}


def test_the_deadline_expires_the_intent(watcher, queued, journal, execution_settings, today, caplog):
    """AC-8: nothing to cancel, so the escalation is the CRITICAL line."""
    import logging

    from freezegun import freeze_time

    _, pending_order_id = queued
    later = datetime.now(tz=UTC) + timedelta(
        seconds=execution_settings.approval_deadline_seconds + 1
    )
    with freeze_time(later), caplog.at_level(logging.CRITICAL):
        watcher.poll_once()

    row = {r.status: r for r in journal.list_approvals(today)}[APPROVAL_EXPIRED]
    assert row.withdrawal == WITHDRAWAL_NOT_QUEUED
    assert row.defect is False
    assert f"Reject pending order {pending_order_id}" in caplog.text


def test_the_kill_switch_expires_an_outstanding_intent(watcher, queued, journal, execution_settings, today):
    """AC-14: an intent this desk queued is this desk's to disown."""
    execution_settings.kill_switch_path.write_text("engaged by the trader", encoding="utf-8")
    watcher.poll_once()
    row = {r.status: r for r in journal.list_approvals(today)}[APPROVAL_EXPIRED]
    assert "kill switch" in row.detail


def test_a_late_click_is_caught_and_withdrawn(
    watcher, queued, journal, openalgo, openalgo_db, execution_settings, today, caplog
):
    """AC-9: expired first, then approved anyway — two terminal rows, both true."""
    import logging

    from freezegun import freeze_time

    approval_id, pending_order_id = queued
    later = datetime.now(tz=UTC) + timedelta(
        seconds=execution_settings.approval_deadline_seconds + 1
    )
    with freeze_time(later):
        watcher.poll_once()

    approve_pending(openalgo_db, pending_order_id, by="amit")
    _status_route(openalgo, status="open")
    openalgo.post("/api/v1/cancelorder").mock(
        return_value=httpx.Response(200, json={"status": "success", "orderid": "24090100000041"})
    )

    with caplog.at_level(logging.CRITICAL):
        watcher.poll_once()

    rows = {row.status: row for row in journal.list_approvals(today)}
    assert set(rows) == {"pending", APPROVAL_EXPIRED, APPROVAL_LATE}
    assert rows[APPROVAL_LATE].defect is True
    assert rows[APPROVAL_LATE].withdrawal == WITHDRAWAL_PERMITTED
    assert "LATE APPROVAL" in caplog.text
    assert journal.orders_for_approval(approval_id)[0].broker_order_id == "24090100000041"


def test_a_late_click_records_a_refused_withdrawal(
    watcher, queued, journal, openalgo, openalgo_db, execution_settings, today
):
    """Live semi-auto refuses the cancel; the desk records the refusal rather than claiming one."""
    from freezegun import freeze_time

    _, pending_order_id = queued
    with freeze_time(datetime.now(tz=UTC) + timedelta(seconds=400)):
        watcher.poll_once()
    approve_pending(openalgo_db, pending_order_id)
    _status_route(openalgo, status="open")
    openalgo.post("/api/v1/cancelorder").mock(
        return_value=httpx.Response(
            403, json={"status": "error", "message": "not allowed in Semi-Auto mode"}
        )
    )

    watcher.poll_once()

    rows = {row.status: row for row in journal.list_approvals(today)}
    assert rows[APPROVAL_LATE].withdrawal == WITHDRAWAL_REFUSED


def test_an_order_is_followed_to_its_fill(watcher, queued, journal, openalgo, openalgo_db):
    approval_id, pending_order_id = queued
    approve_pending(openalgo_db, pending_order_id)
    _status_route(openalgo, status="open")
    watcher.poll_once()

    _status_route(openalgo, status="complete", average_price=191.85)
    watcher.poll_once()
    watcher.poll_once()  # a terminal order is not watched again

    orders = journal.orders_for_approval(approval_id)
    assert [order.order_status for order in orders] == ["open", "complete"]
    assert orders[-1].average_price == pytest.approx(191.85)


def test_an_unfilled_order_is_cancelled_at_the_fill_deadline(
    watcher, queued, journal, openalgo, openalgo_db, execution_settings
):
    from freezegun import freeze_time

    approval_id, pending_order_id = queued
    approve_pending(openalgo_db, pending_order_id)
    _status_route(openalgo, status="open")
    watcher.poll_once()

    cancel = openalgo.post("/api/v1/cancelorder").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    with freeze_time(
        datetime.now(tz=UTC) + timedelta(seconds=execution_settings.fill_deadline_seconds + 1)
    ):
        watcher.poll_once()

    assert cancel.call_count == 1
    assert journal.orders_for_approval(approval_id)[-1].order_status == "cancelled"


def test_an_unreadable_mirror_settles_nothing(watcher, queued, journal, openalgo_db, today):
    """A gate you cannot see is not a gate you may guess about."""
    openalgo_db.unlink()
    assert watcher.poll_once() == 0
    assert [row.status for row in journal.list_approvals(today)] == ["pending"]


def test_an_unreadable_queue_past_the_deadline_is_shouted_about(
    watcher, queued, journal, openalgo_db, execution_settings, today, caplog
):
    """AC-13: settling blind would release the hold, so the escalation is the log line."""
    import logging

    from freezegun import freeze_time

    _, pending_order_id = queued
    openalgo_db.unlink()
    later = datetime.now(tz=UTC) + timedelta(
        seconds=execution_settings.approval_deadline_seconds + 1
    )
    with freeze_time(later), caplog.at_level(logging.CRITICAL):
        assert watcher.poll_once() == 0

    assert f"APPROVAL QUEUE UNREADABLE: pending order {pending_order_id}" in caplog.text
    assert [row.status for row in journal.list_approvals(today)] == ["pending"]
```

## 7. The tick, end to end

### `strike_desk/tests/test_tick_approval.py`

```python
"""From a cleared intent to a settled approval, through the real graph."""

from __future__ import annotations

import httpx
import pytest

from strike_desk.approval_watcher import ApprovalWatcher
from strike_desk.graph import (
    OUTCOME_DECLINE,
    OUTCOME_ENTER,
    OUTCOME_HOLD,
    REASON_APPROVAL_GATE_UNAVAILABLE,
    REASON_APPROVAL_PENDING,
)
from strike_desk.journal import APPROVAL_APPROVED, APPROVAL_PENDING
from strike_desk.specialists import ROLE_REGIME, ROLE_STRATEGIST

from .approval_fixtures import (
    USER,
    approve_pending,
    proposed_payload,
    queue_pending_order,
    regime_payload,
    set_order_mode,
)
from .conftest import StubSpecialist


@pytest.fixture
def entering_desk(execution_runner, execution_deps, registry, openalgo, openalgo_db, rich_account):
    """A desk whose specialists always produce the recorded contract, and a queue to put it in."""
    registry.register(StubSpecialist(role=ROLE_REGIME, payload=regime_payload()))
    registry.register(StubSpecialist(role=ROLE_STRATEGIST, payload=proposed_payload()))
    pending_order_id = queue_pending_order(openalgo_db)
    openalgo.post("/api/v1/placeorder").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "mode": "semi_auto", "pending_order_id": pending_order_id},
        )
    )
    openalgo.post("/api/v1/orderstatus").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"order_status": "open", "average_price": 0.0, "quantity": 150},
            },
        )
    )
    return execution_runner, pending_order_id


def test_an_enter_reaches_the_queue_after_its_decision_row(entering_desk, journal, today):
    """AC-1 and AC-12: the decision is journalled first, and the approval names it."""
    runner, pending_order_id = entering_desk

    tick_id = runner.run_tick("manual")

    decision = journal.list_decisions(today)[0]
    assert decision.outcome == OUTCOME_ENTER and decision.tick_id == tick_id
    approval = journal.list_approvals(today)[0]
    assert approval.status == APPROVAL_PENDING
    assert approval.tick_id == tick_id
    assert approval.pending_order_id == pending_order_id
    assert approval.id > 0 and approval.created_at_utc >= decision.created_at_utc
    assert approval.verdict_id and journal.risk_verdict(approval.verdict_id) is not None
    assert approval.proposal_id and journal.proposal(approval.proposal_id) is not None


def test_the_next_tick_holds_while_the_intent_waits(entering_desk, journal, today):
    """AC-10: no specialist, no token, no second intent."""
    runner, _ = entering_desk
    runner.run_tick("manual")
    reads_before = len(journal.list_regime_reads(today))

    runner.run_tick("manual")

    decisions = journal.list_decisions(today)
    assert decisions[0].outcome == OUTCOME_HOLD
    assert decisions[0].reason_code == REASON_APPROVAL_PENDING
    assert decisions[0].token_cost_micros == 0
    assert len(journal.list_regime_reads(today)) == reads_before
    assert len(journal.list_approvals(today)) == 1


def test_the_graph_settles_its_own_approval(
    entering_desk, journal, execution_settings, mirror, execution_client, openalgo_db, today
):
    """AC-6 and AC-14: the watcher resumes the suspended tick, which writes the terminal rows."""
    runner, pending_order_id = entering_desk
    runner.run_tick("manual")
    approve_pending(openalgo_db, pending_order_id, by=USER)

    watcher = ApprovalWatcher(
        execution_settings, journal, mirror, execution_client, runner.resume_approval
    )
    assert watcher.poll_once() == 1

    rows = {row.status: row for row in journal.list_approvals(today)}
    assert set(rows) == {APPROVAL_PENDING, APPROVAL_APPROVED}
    assert rows[APPROVAL_APPROVED].approved_by == USER
    approval_id = rows[APPROVAL_APPROVED].approval_id
    assert len(journal.orders_for_approval(approval_id)) == 1

    # A second poll finds nothing outstanding and settles nothing twice.
    assert watcher.poll_once() == 0
    assert len(journal.list_approvals(today)) == 2


def test_a_stale_intent_holds_as_a_defect(entering_desk, journal, execution_settings, today):
    """AC-13: past its deadline and still unsettled is a defect, not a routine hold."""
    from datetime import UTC, datetime, timedelta

    from freezegun import freeze_time

    from strike_desk.decline_taxonomy import describe
    from strike_desk.graph import REASON_APPROVAL_QUEUE_STALE

    runner, _ = entering_desk
    runner.run_tick("manual")

    later = datetime.now(tz=UTC) + timedelta(
        seconds=execution_settings.approval_deadline_seconds + 1
    )
    with freeze_time(later):
        runner.run_tick("manual")

    decision = journal.list_decisions(today)[0]
    assert decision.outcome == OUTCOME_HOLD
    assert decision.reason_code == REASON_APPROVAL_QUEUE_STALE
    assert describe(REASON_APPROVAL_QUEUE_STALE).disposition == "defect"


def test_a_submission_that_raises_does_not_poison_the_tick(
    entering_desk, execution_deps, journal, today, monkeypatch
):
    """One decision row, one terminal approval row, and no false journal-unwritable alarm."""
    from strike_desk.errors import JournalWriteError
    from strike_desk.journal import APPROVAL_SUBMIT_FAILED

    runner, _ = entering_desk

    def _explode(_context):
        raise JournalWriteError("disk full")

    monkeypatch.setattr(execution_deps.gate, "submit", _explode)

    tick_id = runner.run_tick("manual")

    decisions = journal.list_decisions(today)
    assert [d.tick_id for d in decisions] == [tick_id]
    assert decisions[0].outcome == OUTCOME_ENTER
    assert [row.status for row in journal.list_approvals(today)] == [APPROVAL_SUBMIT_FAILED]


def test_auto_mode_declines_before_a_token_is_spent(entering_desk, journal, openalgo_db, today):
    """AC-2: the gate check lives in plan, so a broken gate costs nothing."""
    runner, _ = entering_desk
    set_order_mode(openalgo_db, "auto")

    runner.run_tick("manual")

    decision = journal.list_decisions(today)[0]
    assert decision.outcome == OUTCOME_DECLINE
    assert decision.reason_code == REASON_APPROVAL_GATE_UNAVAILABLE
    assert journal.list_regime_reads(today) == []
    assert journal.list_approvals(today) == []


def test_execution_disabled_stops_at_the_intent(
    entering_desk, execution_deps, journal, today, monkeypatch
):
    """The iteration-05 posture is one setting away and still reachable."""
    runner, _ = entering_desk
    monkeypatch.setattr(execution_deps.settings, "execution_enabled", False)

    runner.run_tick("manual")

    assert journal.list_decisions(today)[0].outcome == OUTCOME_ENTER
    assert journal.list_approvals(today) == []


def test_the_trace_carries_the_submission(entering_desk, journal, today):
    """AC-15: the submission is on the tick's own trace, with its numbers on it."""
    runner, pending_order_id = entering_desk
    tick_id = runner.run_tick("manual")

    decision = journal.list_decisions(today)[0]
    assert decision.tick_id == tick_id
    spans = {span.name: span for span in journal.spans_for_trace(decision.trace_id)}
    assert "tick.submit" in spans
    attributes = spans["tick.submit"].attributes_json
    assert '"approval.status": "pending"' in attributes
    assert f'"approval.pending_order_id": {pending_order_id}' in attributes
    assert '"approval.limit_price": 192.0' in attributes
```

## 8. The guardrails, and the frozen set

These are the tests that must fail loudly, and they are the ones CI runs first. Each is a
property of the system rather than a behaviour of a function: no autonomous order, no path
outside the whitelist, no second intent, no write to OpenAlgo's database, no duplicate
settlement.

### `strike_desk/tests/test_guardrails_execution.py`

```python
"""The five structural guarantees of UC-06. A red test here blocks the build."""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

import strike_desk
from strike_desk.execution_client import EXECUTION_PATHS
from strike_desk.openalgo_client import READ_ONLY_PATHS

from .approval_fixtures import cleared_context

SOURCE = Path(strike_desk.__file__).parent


def test_the_two_clients_have_disjoint_whitelists():
    """AC-5: the code that reasons cannot reach the code that places."""
    assert EXECUTION_PATHS == {
        "/api/v1/placeorder",
        "/api/v1/orderstatus",
        "/api/v1/cancelorder",
    }
    assert READ_ONLY_PATHS.isdisjoint(EXECUTION_PATHS)
    assert not any("order" in path for path in READ_ONLY_PATHS)


@pytest.mark.parametrize("module", ["regime_analyst", "options_strategist", "mcp_toolbox"])
def test_no_agent_module_imports_the_execution_edge(module):
    """AC-5: an agent that cannot import the client cannot place by hallucination."""
    tree = ast.parse((SOURCE / f"{module}.py").read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden = {"execution_client", "approval_gate", "approval_watcher", "openalgo_mirror"}
    assert not {name.split(".")[-1] for name in imported} & forbidden


def test_a_response_without_a_queue_receipt_stops_the_desk(
    gate, journal, openalgo, execution_settings, today
):
    """AC-3: the audit property of the MVP is zero autonomous live orders."""
    openalgo.post("/api/v1/placeorder").mock(
        return_value=httpx.Response(200, json={"status": "success", "orderid": "24090100000041"})
    )

    gate.submit(cleared_context())

    assert execution_settings.kill_switch_path.exists()
    assert journal.list_approvals(today)[0].defect is True


def test_openalgos_database_is_never_written(mirror, openalgo_db):
    """AC-13: proved against a real file, by trying."""
    import sqlite3

    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    before = sqlite3.connect(openalgo_db).execute(
        "SELECT order_mode FROM api_keys"
    ).fetchone()
    with pytest.raises(OperationalError):
        with mirror._session_scope() as session:  # noqa: SLF001
            session.execute(text("DELETE FROM pending_orders"))
            session.commit()
    after = sqlite3.connect(openalgo_db).execute("SELECT order_mode FROM api_keys").fetchone()
    assert before == after


def test_the_append_only_triggers_cover_the_new_tables(journal, gate, openalgo):
    """AC-11: approvals and orders are as immutable as decisions."""
    import sqlite3

    openalgo.post("/api/v1/placeorder").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "mode": "semi_auto", "pending_order_id": 41}
        )
    )
    gate.submit(cleared_context())

    connection = sqlite3.connect(journal.path)
    try:
        for statement in (
            "UPDATE approvals SET status = 'approved'",
            "DELETE FROM approvals",
            "UPDATE orders SET order_status = 'complete'",
            "DELETE FROM orders",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement)
    finally:
        connection.close()


def test_one_intent_at_a_time_is_a_query_not_a_flag(journal, gate, openalgo):
    """AC-10: restart-proof, because it is answered from the journal."""
    openalgo.post("/api/v1/placeorder").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "mode": "semi_auto", "pending_order_id": 41}
        )
    )
    gate.submit(cleared_context())
    assert len(journal.open_approvals()) == 1
```

### `strike_desk/tests/regression/approval_scenarios.json`

The frozen set: each case is a queue state and the settlement it must produce. It is the file
to add a line to when a new way for an approval to end is discovered, and the file that fails
when a settlement quietly changes shape.

```json
[
  {
    "name": "approved-promptly",
    "pending_status": "approved",
    "approved_by": "amit",
    "broker_order_id": "24090100000041",
    "order_status": "open",
    "seconds_elapsed": 20,
    "kill_switch": false,
    "expect_status": "approved",
    "expect_withdrawal": "not-attempted",
    "expect_defect": false,
    "expect_order_rows": 1
  },
  {
    "name": "rejected-with-a-reason",
    "pending_status": "rejected",
    "rejected_reason": "strike too far OTM",
    "seconds_elapsed": 45,
    "kill_switch": false,
    "expect_status": "rejected",
    "expect_withdrawal": "not-queued",
    "expect_defect": false,
    "expect_order_rows": 0
  },
  {
    "name": "still-waiting",
    "pending_status": "pending",
    "seconds_elapsed": 30,
    "kill_switch": false,
    "expect_status": null,
    "expect_order_rows": 0
  },
  {
    "name": "expired-unanswered",
    "pending_status": "pending",
    "seconds_elapsed": 301,
    "kill_switch": false,
    "expect_status": "expired",
    "expect_withdrawal": "not-queued",
    "expect_defect": false,
    "expect_order_rows": 0
  },
  {
    "name": "killed-while-waiting",
    "pending_status": "pending",
    "seconds_elapsed": 30,
    "kill_switch": true,
    "expect_status": "expired",
    "expect_withdrawal": "not-queued",
    "expect_defect": false,
    "expect_order_rows": 0
  },
  {
    "name": "deleted-by-the-trader",
    "pending_status": "deleted",
    "seconds_elapsed": 30,
    "kill_switch": false,
    "expect_status": "withdrawn",
    "expect_withdrawal": "not-queued",
    "expect_defect": false,
    "expect_order_rows": 0
  }
]
```

### `strike_desk/tests/regression/test_approval_scenarios.py`

```python
"""The frozen approval set: a queue state in, one settlement out."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from freezegun import freeze_time

from strike_desk.approval_watcher import ApprovalWatcher

from ..approval_fixtures import (
    approve_pending,
    cleared_context,
    delete_pending,
    queue_pending_order,
    reject_pending,
)

CASES = json.loads((Path(__file__).parent / "approval_scenarios.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_scenario(
    case, gate, journal, mirror, execution_client, execution_settings, openalgo, openalgo_db, today
):
    pending_order_id = queue_pending_order(openalgo_db)
    openalgo.post("/api/v1/placeorder").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "mode": "semi_auto", "pending_order_id": pending_order_id},
        )
    )
    openalgo.post("/api/v1/orderstatus").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "order_status": case.get("order_status", "open"),
                    "average_price": 0.0,
                },
            },
        )
    )
    approval_id = gate.submit(cleared_context()).approval_id

    if case["pending_status"] == "approved":
        approve_pending(
            openalgo_db,
            pending_order_id,
            by=case.get("approved_by", "amit"),
            broker_order_id=case.get("broker_order_id"),
        )
    elif case["pending_status"] == "rejected":
        reject_pending(openalgo_db, pending_order_id, reason=case.get("rejected_reason", ""))
    elif case["pending_status"] == "deleted":
        delete_pending(openalgo_db, pending_order_id)

    if case["kill_switch"]:
        execution_settings.kill_switch_path.write_text("frozen case", encoding="utf-8")

    watcher = ApprovalWatcher(
        execution_settings, journal, mirror, execution_client, lambda _tick, _payload: False
    )
    with freeze_time(datetime.now(tz=UTC) + timedelta(seconds=case["seconds_elapsed"])):
        watcher.poll_once()

    terminal = [row for row in journal.list_approvals(today) if row.status != "pending"]
    if case["expect_status"] is None:
        assert terminal == []
        return
    assert [row.status for row in terminal] == [case["expect_status"]]
    assert terminal[0].withdrawal == case["expect_withdrawal"]
    assert bool(terminal[0].defect) is case["expect_defect"]
    assert len(journal.orders_for_approval(approval_id)) == case["expect_order_rows"]
```

### `strike_desk/tests/test_journal_migration.py` — additions

```python
def test_schema_six_adds_two_tables_and_touches_no_row(legacy_five_journal):
    """The iteration-05 database gains two tables and loses nothing."""
    before = legacy_five_journal.count_decisions(TRADING_DAY)
    journal = Journal(legacy_five_journal.path)
    try:
        journal.create_schema()
        assert journal.count_decisions(TRADING_DAY) == before
        assert journal.open_approvals() == []
    finally:
        journal.close()

    connection = sqlite3.connect(legacy_five_journal.path)
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"approvals", "orders"} <= tables
        indexes = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'uq_%'"
        )}
        assert {"uq_approvals_state", "uq_orders_state"} <= indexes
    finally:
        connection.close()
```

`legacy_five_journal` follows the pattern iterations 03 to 05 established in the same file: a
journal built at the previous schema and populated with rows the previous release would have
written.

## 9. Running it

```bash
cd strike_desk
uv sync --group dev
uv run ruff check .

# the gate CI runs first — these must never be skipped
uv run pytest tests/test_guardrails.py tests/test_guardrails_regime.py \
              tests/test_guardrails_execution.py tests/regression -q

uv run pytest --cov=strike_desk --cov-report=term-missing --cov-fail-under=80
```

`.github/workflows/strike-desk-ci.yml` needs the new guardrail file in the gate step, and
`approval_watcher.py` and `execution_client.py` in the paths that matter — the reasoning-plane
eval job is deliberately *not* triggered by this slice, because nothing here touches a prompt:

```yaml
      - name: Guardrail and regression gate
        run: |
          uv run pytest tests/test_guardrails.py tests/test_guardrails_regime.py \
                        tests/test_guardrails_execution.py tests/regression -q
```

Coverage intent is unchanged at 80% overall, with the new modules well above it: the mirror,
the client and the gate are covered branch by branch, and `approval_watcher.py` is covered by
the frozen set plus the watcher file. `service.py` and `__main__.py` stay in the coverage
omit list for the reason iteration 01 gave — their lifecycle is proved by hand on the host,
in MT-23 and MT-24.

## 10. What each test holds

| Test | Covers | Backstops |
| --- | --- | --- |
| `test_tick_approval::test_an_enter_reaches_the_queue_after_its_decision_row` | AC-1, AC-12 | MT-09 |
| `test_openalgo_mirror::test_semi_auto_is_the_only_healthy_mode` | AC-2 | MT-18 |
| `test_approval_gate::test_auto_mode_stops_the_submission_before_the_wire` | AC-2 | MT-18 |
| `test_tick_approval::test_auto_mode_declines_before_a_token_is_spent` | AC-2 | MT-18 |
| `test_guardrails_execution::test_a_response_without_a_queue_receipt_stops_the_desk` | AC-3 | MT-19 |
| `test_execution_client::test_a_placement_that_reached_the_broker_is_not_queued` | AC-3 | MT-19 |
| `test_approval_gate::test_the_limit_price_is_the_top_of_the_band_on_the_grid` | AC-4 | MT-01 |
| `test_approval_gate::test_a_band_with_no_tick_in_it_is_refused` | AC-4 | MT-02 |
| `test_approval_gate::test_the_quantity_comes_from_the_verdict_not_the_proposal` | AC-4 | MT-03 |
| `test_approval_gate::test_an_unusable_intent_raises_before_anything_is_built` | AC-4 | MT-04 |
| `test_execution_client::test_only_three_paths_are_reachable` | AC-5 | MT-05 |
| `test_guardrails_execution::test_the_two_clients_have_disjoint_whitelists` | AC-5 | MT-05 |
| `test_guardrails_execution::test_no_agent_module_imports_the_execution_edge` | AC-5 | MT-05 |
| `test_approval_watcher::test_an_approval_settles_with_its_order` | AC-6 | MT-12 |
| `test_tick_approval::test_the_graph_settles_its_own_approval` | AC-6, AC-14 | MT-12, MT-21 |
| `test_approval_watcher::test_an_order_is_followed_to_its_fill` | AC-6 | MT-16 |
| `test_approval_watcher::test_an_unfilled_order_is_cancelled_at_the_fill_deadline` | AC-6 | MT-17 |
| `test_approval_watcher::test_a_rejection_keeps_the_reason` | AC-7 | MT-13 |
| `test_approval_gate::test_a_rejection_writes_no_order_row` | AC-7 | MT-13 |
| `test_approval_watcher::test_the_deadline_expires_the_intent` | AC-8 | MT-14 |
| `test_approval_watcher::test_a_late_click_is_caught_and_withdrawn` | AC-9 | MT-15 |
| `test_approval_watcher::test_a_late_click_records_a_refused_withdrawal` | AC-9 | MT-25 |
| `test_tick_approval::test_the_next_tick_holds_while_the_intent_waits` | AC-10 | MT-11 |
| `test_guardrails_execution::test_one_intent_at_a_time_is_a_query_not_a_flag` | AC-10 | MT-11 |
| `test_approval_gate::test_settlement_is_idempotent` | AC-11 | MT-08 |
| `test_guardrails_execution::test_the_append_only_triggers_cover_the_new_tables` | AC-11 | MT-08 |
| `test_journal_migration::test_schema_six_adds_two_tables_and_touches_no_row` | AC-11 | MT-08 |
| `test_openalgo_mirror::test_the_connection_cannot_write` | AC-13 | MT-06 |
| `test_guardrails_execution::test_openalgos_database_is_never_written` | AC-13 | MT-06 |
| `test_openalgo_mirror::test_a_missing_database_is_unhealthy_not_a_crash` | AC-13 | MT-07 |
| `test_approval_watcher::test_an_unreadable_mirror_settles_nothing` | AC-13 | MT-07 |
| `test_approval_watcher::test_an_unreadable_queue_past_the_deadline_is_shouted_about` | AC-13 | MT-26 |
| `test_tick_approval::test_a_stale_intent_holds_as_a_defect` | AC-13 | MT-26 |
| `test_tick_approval::test_a_submission_that_raises_does_not_poison_the_tick` | AC-1, AC-11 | MT-04 |
| `test_approval_watcher::test_the_kill_switch_expires_an_outstanding_intent` | AC-14 | MT-20 |
| `test_tick_approval::test_the_trace_carries_the_submission` | AC-15 | MT-22 |
| `regression/test_approval_scenarios` | AC-6 to AC-9, AC-14 | MT-12 to MT-20 |

## 11. Limitations

1. **The mirror's column names are pinned by a manual test, not by CI.** The fixture builds
   `pending_orders` and `api_keys` from the mirror's own models, so a name that drifts upstream
   passes here and fails on the host. MT-23 is the check that catches it, and it belongs in the
   deployment run every time.
2. **No test proves a real order was queued by a real OpenAlgo.** `respx` answers the
   `placeorder` route with the shape `services/order_router_service.py` returns; that shape is
   read from the source rather than observed. MT-09 observes it.
3. **The suite cannot prove a click.** Approval, rejection and deletion are simulated by
   writing the row OpenAlgo's own routes write. That is the same row by construction, and it is
   still a simulation — which is why Block B of the manual cases exists.
4. **Re-registering a respx route assumes replacement.** Several cases mock
   `/api/v1/orderstatus` twice in one test — `open`, then `complete` — which relies on `respx`
   updating a route registered with an identical pattern rather than appending a second one.
   Confirm it once with a throwaway test (`route = router.post(p).mock(a)`, re-register with
   `b`, assert the second body comes back); if your version appends, capture the route object
   and call `.mock()` on it instead. The failure mode is loud either way, which is why this is
   a note rather than a defence in the code.
5. **Timing is frozen, not waited for.** `freeze_time` moves the clock past a deadline
   instantly, so the suite proves the arithmetic of expiry rather than the scheduler's cadence.
   The five-second poll is observed once, by hand, in MT-12.

---
**Sources**

*Repo files:* `040_iterations/iteration-06/01_use_case.md` · `040_iterations/iteration-06/02_implementation_guide.md` · `040_iterations/iteration-05/04_test_automation.md` · `strike_desk/tests/conftest.py` · `strike_desk/pyproject.toml` · `.github/workflows/strike-desk-ci.yml` · `services/order_router_service.py` · `services/cancel_order_service.py`

*Web (accessed 2026-09-01):*
- [respx — mocking HTTPX with route patterns](https://lundberg.github.io/respx/api/)
- [pytest — parametrize and fixtures](https://docs.pytest.org/en/stable/how-to/parametrize.html)
- [freezegun — controlling the clock in tests](https://github.com/spulec/freezegun)
