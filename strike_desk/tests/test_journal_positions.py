"""The two new tables: append-only, unique-by-state, queryable."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from strike_desk.errors import AlreadyJournalled
from strike_desk.journal import POSITION_ADOPTED, POSITION_FLAT, SCHEMA_VERSION
from tests.position_fixtures import seed_chain, today


def base_row(position_id: str, state: str) -> dict:
    return {
        "position_id": position_id,
        "state": state,
        "order_id": None,
        "approval_id": None,
        "proposal_id": None,
        "tick_id": None,
        "trace_id": "0" * 32,
        "created_at_utc": datetime.now(tz=UTC),
        "trading_day": today(),
        "symbol": "NIFTY30SEP2625000CE",
        "exchange": "NFO",
        "product": "MIS",
        "quantity": 75,
        "entry_price": 102.5,
        "stop_price": 80.0,
        "target_price": 140.0,
        "time_stop_utc": datetime.now(tz=UTC),
        "theta_per_day": -4.2,
        "exit_reason": None,
        "realised_pnl": None,
        "detail": "test",
        "defect": False,
    }


def test_schema_version_is_seven(journal):
    assert SCHEMA_VERSION == 7
    assert {"positions", "exits"} <= set(journal.table_names())


def test_a_state_may_be_written_once(journal):
    position_id = str(uuid.uuid4())
    journal.record_position_state(**base_row(position_id, POSITION_ADOPTED))
    with pytest.raises(AlreadyJournalled):
        journal.record_position_state(**base_row(position_id, POSITION_ADOPTED))


def test_live_position_ignores_terminal_ones(journal):
    live = str(uuid.uuid4())
    done = str(uuid.uuid4())
    journal.record_position_state(**base_row(done, POSITION_ADOPTED))
    journal.record_position_state(**base_row(done, POSITION_FLAT))
    journal.record_position_state(**base_row(live, POSITION_ADOPTED))
    found = journal.live_position()
    assert found is not None and found.position_id == live


def test_unadopted_orders_finds_a_filled_order(journal):
    ids = seed_chain(journal)
    orphans = journal.unadopted_orders(today())
    assert [order.order_id for order in orphans] == [ids["order_id"]]

    journal.record_position_state(
        **{**base_row(str(uuid.uuid4()), POSITION_ADOPTED), "order_id": ids["order_id"]}
    )
    assert journal.unadopted_orders(today()) == []


def test_rows_cannot_be_updated_or_deleted(journal):
    position_id = str(uuid.uuid4())
    journal.record_position_state(**base_row(position_id, POSITION_ADOPTED))
    with journal.session_scope() as session:
        with pytest.raises(Exception, match="append-only"):
            session.execute(
                __import__("sqlalchemy").text("UPDATE positions SET quantity = 1")
            )
