"""Append-only SQLite journal: ``decisions``, ``traces``, ``regime_reads``,
``proposals``, ``risk_verdicts``, ``approvals``, ``orders``, ``positions`` and ``exits``."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    DDL,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    inspect,
    select,
)
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import NullPool

from .errors import AlreadyJournalled, JournalWriteError

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 7

POSITION_ADOPTED = "adopted"
POSITION_ARMED = "armed"
POSITION_EXITING = "exiting"
POSITION_FLAT = "flat"
POSITION_STOOD_DOWN = "stood-down"
POSITION_ORPHANED = "orphaned"

POSITION_LIVE = frozenset({POSITION_ADOPTED, POSITION_ARMED, POSITION_EXITING})
POSITION_TERMINAL = frozenset({POSITION_FLAT, POSITION_STOOD_DOWN, POSITION_ORPHANED})

EXIT_STOP = "stop"
EXIT_TARGET = "target"
EXIT_TIME_STOP = "time-stop"
EXIT_SESSION_DEADLINE = "session-deadline"
EXIT_FEED_BLACKOUT = "feed-blackout"
EXIT_LEVELS_UNAVAILABLE = "levels-unavailable"
EXIT_MANUAL = "manual"
EXIT_SQUARE_OFF = "square-off"

EXIT_REASONS = frozenset(
    {
        EXIT_STOP,
        EXIT_TARGET,
        EXIT_TIME_STOP,
        EXIT_SESSION_DEADLINE,
        EXIT_FEED_BLACKOUT,
        EXIT_LEVELS_UNAVAILABLE,
        EXIT_MANUAL,
        EXIT_SQUARE_OFF,
    }
)

EXIT_SUBMITTED = "submitted"
EXIT_FILLED = "filled"
EXIT_REFUSED = "refused"
EXIT_GATED = "gated"
EXIT_FAILED = "failed"

APPROVAL_PENDING = "pending"
APPROVAL_AUTO_APPROVED = "auto-approved"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"
APPROVAL_EXPIRED = "expired"
APPROVAL_LATE = "late-approval"
APPROVAL_WITHDRAWN = "withdrawn"
APPROVAL_GATE_UNAVAILABLE = "gate-unavailable"
APPROVAL_GATE_BYPASSED = "gate-bypassed"
APPROVAL_SUBMIT_FAILED = "submit-failed"
APPROVAL_UNPRICEABLE = "unpriceable-band"

APPROVAL_TERMINAL = frozenset(
    {
        APPROVAL_APPROVED,
        APPROVAL_AUTO_APPROVED,
        APPROVAL_REJECTED,
        APPROVAL_EXPIRED,
        APPROVAL_LATE,
        APPROVAL_WITHDRAWN,
        APPROVAL_GATE_UNAVAILABLE,
        APPROVAL_GATE_BYPASSED,
        APPROVAL_SUBMIT_FAILED,
        APPROVAL_UNPRICEABLE,
    }
)
#: Statuses that mean a human should go and look at something.
APPROVAL_DEFECTS = frozenset(
    {
        APPROVAL_LATE,
        APPROVAL_GATE_UNAVAILABLE,
        APPROVAL_GATE_BYPASSED,
        APPROVAL_SUBMIT_FAILED,
        APPROVAL_UNPRICEABLE,
    }
)
#: Order statuses OpenAlgo's normalised order book uses that need no further watching.
ORDER_TERMINAL = frozenset({"complete", "rejected", "cancelled"})


class Base(DeclarativeBase):
    """Declarative base for the Strike Desk journal."""


class Decision(Base):
    """One row per completed tick. Never updated, never deleted."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trading_day: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    index_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(48), index=True, nullable=False)
    reason_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Written from the taxonomy, nullable because a journal from an earlier release has
    # rows that predate these columns and an append-only table can never be backfilled.
    reason_category: Mapped[str | None] = mapped_column(String(24), index=True, nullable=True)
    reason_disposition: Mapped[str | None] = mapped_column(String(16), nullable=True)
    regime_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    regime_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    book_state_json: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_set_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    token_cost_micros: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    trace_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=SCHEMA_VERSION)


class TraceSpan(Base):
    """One row per finished OpenTelemetry span. Never updated, never deleted."""

    __tablename__ = "traces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    span_id: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attributes_json: Mapped[str] = mapped_column(Text, nullable=False)


class RegimeRead(Base):
    """One row per regime classification attempt. Never updated, never deleted."""

    __tablename__ = "regime_reads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    read_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    tick_id: Mapped[str] = mapped_column(String(48), index=True, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trading_day: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    index_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)  # tick | cli
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    defect: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_cost_micros: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_name: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_set_version: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=SCHEMA_VERSION)


class Proposal(Base):
    """One row per contract proposal attempt. Never updated, never deleted."""

    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    tick_id: Mapped[str] = mapped_column(String(48), index=True, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trading_day: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    index_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)  # tick | cli
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    regime_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    regime_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- the contract, null on every status except `proposed` ------------------
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    expiry: Mapped[str | None] = mapped_column(String(10), nullable=True)
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(2), nullable=True)
    lots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lot_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entry_price_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_price_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    theta_per_day: Mapped[float | None] = mapped_column(Float, nullable=True)
    implied_volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    open_interest: Mapped[int | None] = mapped_column(Integer, nullable=True)
    breakeven: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_stop_ist: Mapped[str | None] = mapped_column(String(5), nullable=True)

    # --- the reasoning, the verdict and the receipts ---------------------------
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    playbook_artifact: Mapped[str] = mapped_column(String(32), nullable=False)
    playbook_verdict: Mapped[str] = mapped_column(String(16), nullable=False)  # pass | fail | n/a
    violations_json: Mapped[str] = mapped_column(Text, nullable=False)
    defect: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_tool_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_cost_micros: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_name: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_set_version: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=SCHEMA_VERSION)


class RiskVerdictRow(Base):
    """One row per adjudication. Never updated, never deleted."""

    __tablename__ = "risk_verdicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    verdict_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    tick_id: Mapped[str] = mapped_column(String(48), index=True, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    proposal_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trading_day: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    index_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verdict: Mapped[str] = mapped_column(String(8), index=True, nullable=False)
    tripped_limit: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    configured_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    observed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    limit_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    capital_base: Mapped[float] = mapped_column(Float, nullable=False)
    lots_requested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lots_cleared: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    premium_at_risk: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_loss_at_stop: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    session_stop: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    checks_json: Mapped[str] = mapped_column(Text, nullable=False)
    limits_artifact: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    latency_us: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=SCHEMA_VERSION)


class ApprovalRow(Base):
    """One row per state of one human approval. Never updated, never deleted."""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    approval_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    tick_id: Mapped[str] = mapped_column(String(48), index=True, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    tick_trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    proposal_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    verdict_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trading_day: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    index_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(8), nullable=False)
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    lots: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    limit_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    band_low: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    band_high: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pending_order_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    strategy_tag: Mapped[str] = mapped_column(String(32), nullable=False)
    deadline_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    wait_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolved_at_ist: Mapped[str | None] = mapped_column(String(50), nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    withdrawal: Mapped[str | None] = mapped_column(String(24), nullable=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    defect: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=SCHEMA_VERSION)

    __table_args__ = (Index("uq_approvals_state", "approval_id", "status", unique=True),)


class OrderRow(Base):
    """One row per observed state of one placed order. Never updated, never deleted."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    approval_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    tick_id: Mapped[str] = mapped_column(String(48), index=True, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trading_day: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    pending_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    broker_order_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(8), nullable=False)
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    price_type: Mapped[str] = mapped_column(String(8), nullable=False)
    limit_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    lots: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order_status: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    average_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=SCHEMA_VERSION)

    __table_args__ = (Index("uq_orders_state", "approval_id", "order_status", unique=True),)


class PositionRow(Base):
    """One row per state of one managed position. Never updated, never deleted."""

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    state: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    proposal_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    tick_id: Mapped[str | None] = mapped_column(String(48), index=True, nullable=True)
    trace_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trading_day: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_stop_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    theta_per_day: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    realised_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    defect: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=SCHEMA_VERSION)

    __table_args__ = (UniqueConstraint("position_id", "state", name="uq_positions_state"),)


class ExitRow(Base):
    """One row per exit attempt. Never updated, never deleted."""

    __tablename__ = "exits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exit_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    position_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reason: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    path: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    trading_day: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    triggered_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    round_trip_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    level_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    observed_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    feed_source: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    feed_age_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    broker_order_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    slippage: Mapped[float | None] = mapped_column(Float, nullable=True)
    realised_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=SCHEMA_VERSION)

    __table_args__ = (UniqueConstraint("position_id", "attempt", name="uq_exits_attempt"),)


# Append-only enforcement lives in the database, not in application discipline.
for _table in (
    Decision.__table__,
    TraceSpan.__table__,
    RegimeRead.__table__,
    Proposal.__table__,
    RiskVerdictRow.__table__,
    ApprovalRow.__table__,
    OrderRow.__table__,
    PositionRow.__table__,
    ExitRow.__table__,
):
    for _operation in ("UPDATE", "DELETE"):
        event.listen(
            _table,
            "after_create",
            DDL(
                f"CREATE TRIGGER IF NOT EXISTS {_table.name}_no_{_operation.lower()} "
                f"BEFORE {_operation} ON {_table.name} "
                f"BEGIN SELECT RAISE(ABORT, '{_table.name} is append-only'); END;"
            ),
        )

# Columns added after a table shipped. Always nullable and never given a default, so an
# older binary can still write rows and a newer one can still read the older rows.
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("decisions", "reason_category", "VARCHAR(24)"),
    ("decisions", "reason_disposition", "VARCHAR(16)"),
)


def _configure_connection(dbapi_connection: Any, _record: Any) -> None:
    """Apply the SQLite pragmas an audit journal needs on every fresh connection."""
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=FULL")
    finally:
        cursor.close()


class Journal:
    """Repository over the append-only journal database."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._engine = create_engine(f"sqlite:///{db_path}", poolclass=NullPool, future=True)
        event.listen(self._engine, "connect", _configure_connection)
        self._sessionmaker = sessionmaker(bind=self._engine, expire_on_commit=False)

    @property
    def path(self) -> Path:
        return self._path

    def create_schema(self) -> None:
        """Create tables and triggers if absent, then add any column this release added."""
        try:
            Base.metadata.create_all(self._engine)
        except OperationalError as exc:
            if "already exists" not in str(exc).lower():
                raise
            logger.info("journal schema already present during create_all")
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """Widen an existing table in place. No row is read, rewritten or deleted."""
        with self._engine.begin() as connection:
            for table, column, column_type in ADDED_COLUMNS:
                # Every name interpolated below comes from ADDED_COLUMNS, a module
                # constant; no caller-supplied value ever reaches this SQL.
                rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
                present = {str(row[1]) for row in rows}
                if not present or column in present:
                    continue
                connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
                logger.info("journal migration: added %s.%s", table, column)

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        """A session that commits on success and is closed on every path."""
        session = self._sessionmaker()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def record_decision(self, **fields: Any) -> str:
        """Append one decision row. Raises JournalWriteError so the tick fails closed."""
        try:
            with self.session_scope() as session:
                session.add(Decision(**fields))
        except SQLAlchemyError as exc:
            raise JournalWriteError(f"could not append decision: {exc.__class__.__name__}") from exc
        return str(fields["tick_id"])

    def record_span(self, **fields: Any) -> None:
        """Append one trace span row."""
        try:
            with self.session_scope() as session:
                session.add(TraceSpan(**fields))
        except SQLAlchemyError as exc:
            raise JournalWriteError(f"could not append span: {exc.__class__.__name__}") from exc

    def record_regime_read(self, **fields: Any) -> str:
        """Append one regime read. Raises JournalWriteError so the tick fails closed."""
        try:
            with self.session_scope() as session:
                session.add(RegimeRead(**fields))
        except SQLAlchemyError as exc:
            raise JournalWriteError(
                f"could not append regime read: {exc.__class__.__name__}"
            ) from exc
        return str(fields["read_id"])

    def record_proposal(self, **fields: Any) -> str:
        """Append one proposal. Raises JournalWriteError so the tick fails closed."""
        try:
            with self.session_scope() as session:
                session.add(Proposal(**fields))
        except SQLAlchemyError as exc:
            raise JournalWriteError(f"could not append proposal: {exc.__class__.__name__}") from exc
        return str(fields["proposal_id"])

    def record_risk_verdict(self, **fields: Any) -> str:
        """Append one verdict. Raises JournalWriteError so the tick fails closed."""
        try:
            with self.session_scope() as session:
                session.add(RiskVerdictRow(**fields))
        except SQLAlchemyError as exc:
            raise JournalWriteError(
                f"could not append risk verdict: {exc.__class__.__name__}"
            ) from exc
        return str(fields["verdict_id"])

    def list_risk_verdicts(self, trading_day: str, limit: int = 200) -> Sequence[RiskVerdictRow]:
        with self.session_scope() as session:
            statement = (
                select(RiskVerdictRow)
                .where(RiskVerdictRow.trading_day == trading_day)
                .order_by(RiskVerdictRow.created_at_utc.asc())
                .limit(limit)
            )
            return list(session.execute(statement).scalars())

    def recent_risk_days(self, days: int) -> list[str]:
        """The most recent trading days holding at least one verdict, oldest first."""
        with self.session_scope() as session:
            statement = (
                select(RiskVerdictRow.trading_day)
                .distinct()
                .order_by(RiskVerdictRow.trading_day.desc())
                .limit(days)
            )
            found = [str(row) for row in session.execute(statement).scalars()]
        return sorted(found)

    def session_stopped(self, trading_day: str) -> bool:
        """True once a session-stop verdict has been latched for the day."""
        with self.session_scope() as session:
            statement = (
                select(RiskVerdictRow.id)
                .where(
                    RiskVerdictRow.trading_day == trading_day,
                    RiskVerdictRow.session_stop.is_(True),
                )
                .limit(1)
            )
            return session.execute(statement).first() is not None

    def count_entries(self, trading_day: str) -> int:
        """Entries journalled today — the observed value behind max-trades-per-day."""
        with self.session_scope() as session:
            statement = (
                select(func.count())
                .select_from(Decision)
                .where(Decision.trading_day == trading_day, Decision.outcome == "enter")
            )
            return int(session.execute(statement).scalar_one())

    def list_proposals(self, trading_day: str, limit: int = 200) -> Sequence[Proposal]:
        with self.session_scope() as session:
            statement = (
                select(Proposal)
                .where(Proposal.trading_day == trading_day)
                .order_by(Proposal.created_at_utc.asc())
                .limit(limit)
            )
            return list(session.execute(statement).scalars())

    def recent_proposal_days(self, days: int) -> list[str]:
        """The most recent trading days that hold at least one proposal, oldest first."""
        with self.session_scope() as session:
            statement = (
                select(Proposal.trading_day)
                .distinct()
                .order_by(Proposal.trading_day.desc())
                .limit(days)
            )
            found = [str(row) for row in session.execute(statement).scalars()]
        return sorted(found)

    def count_decisions(self, trading_day: str) -> int:
        with self.session_scope() as session:
            statement = (
                select(func.count())
                .select_from(Decision)
                .where(Decision.trading_day == trading_day)
            )
            return int(session.execute(statement).scalar_one())

    def list_decisions(self, trading_day: str, limit: int = 100) -> Sequence[Decision]:
        with self.session_scope() as session:
            statement = (
                select(Decision)
                .where(Decision.trading_day == trading_day)
                .order_by(Decision.created_at_utc.desc())
                .limit(limit)
            )
            return list(session.execute(statement).scalars())

    def list_regime_reads(self, trading_day: str, limit: int = 100) -> Sequence[RegimeRead]:
        with self.session_scope() as session:
            statement = (
                select(RegimeRead)
                .where(RegimeRead.trading_day == trading_day)
                .order_by(RegimeRead.created_at_utc.desc())
                .limit(limit)
            )
            return list(session.execute(statement).scalars())

    def token_cost_micros(self, trading_day: str) -> int:
        """What the day's reads have cost, in USD micro-dollars."""
        with self.session_scope() as session:
            statement = select(func.coalesce(func.sum(RegimeRead.token_cost_micros), 0)).where(
                RegimeRead.trading_day == trading_day
            )
            return int(session.execute(statement).scalar_one())

    # --- reporting reads ----------------------------------------------------

    def decision_rollup(self, trading_day: str) -> list[dict[str, Any]]:
        """One row per (outcome, reason code, stored category) group, with its count."""
        with self.session_scope() as session:
            statement = (
                select(
                    Decision.outcome,
                    Decision.reason_code,
                    Decision.reason_category,
                    Decision.reason_disposition,
                    func.count().label("total"),
                )
                .where(Decision.trading_day == trading_day)
                .group_by(
                    Decision.outcome,
                    Decision.reason_code,
                    Decision.reason_category,
                    Decision.reason_disposition,
                )
            )
            return [
                {
                    "outcome": str(row.outcome),
                    "reason_code": str(row.reason_code),
                    "reason_category": row.reason_category,
                    "reason_disposition": row.reason_disposition,
                    "count": int(row.total),
                }
                for row in session.execute(statement)
            ]

    def decision_bounds(self, trading_day: str) -> tuple[datetime | None, datetime | None]:
        """When the day's first and last decisions were written."""
        with self.session_scope() as session:
            base = select(Decision.created_at_utc).where(Decision.trading_day == trading_day)
            first = session.execute(
                base.order_by(Decision.created_at_utc.asc()).limit(1)
            ).scalar_one_or_none()
            last = session.execute(
                base.order_by(Decision.created_at_utc.desc()).limit(1)
            ).scalar_one_or_none()
            return first, last

    def decision_cost_micros(self, trading_day: str) -> int:
        """What the day's decisions cost in tokens, in USD micro-dollars."""
        with self.session_scope() as session:
            statement = select(func.coalesce(func.sum(Decision.token_cost_micros), 0)).where(
                Decision.trading_day == trading_day
            )
            return int(session.execute(statement).scalar_one())

    def incomplete_trace_count(self, trading_day: str) -> int:
        """Decisions whose trace did not persist cleanly — a safety-system defect."""
        with self.session_scope() as session:
            statement = (
                select(func.count())
                .select_from(Decision)
                .where(Decision.trading_day == trading_day, Decision.trace_complete.is_(False))
            )
            return int(session.execute(statement).scalar_one())

    def regime_label_rollup(self, trading_day: str) -> dict[str, int]:
        """The labels the day's ticks actually read, counted."""
        with self.session_scope() as session:
            statement = (
                select(RegimeRead.label, func.count().label("total"))
                .where(
                    RegimeRead.trading_day == trading_day,
                    RegimeRead.source == "tick",
                    RegimeRead.label.is_not(None),
                )
                .group_by(RegimeRead.label)
                .order_by(func.count().desc())
            )
            return {str(row.label): int(row.total) for row in session.execute(statement)}

    def record_approval(self, **fields: Any) -> str:
        """Append one approval state. Raises AlreadyJournalled when that state already exists."""
        try:
            with self.session_scope() as session:
                session.add(ApprovalRow(**fields))
        except IntegrityError as exc:
            raise AlreadyJournalled(
                f"approval {fields.get('approval_id')} is already at status {fields.get('status')}"
            ) from exc
        except SQLAlchemyError as exc:
            raise JournalWriteError(f"could not append approval: {exc.__class__.__name__}") from exc
        return str(fields["approval_id"])

    def record_order(self, **fields: Any) -> str:
        """Append one order state. Raises AlreadyJournalled when that state already exists."""
        try:
            with self.session_scope() as session:
                session.add(OrderRow(**fields))
        except IntegrityError as exc:
            raise AlreadyJournalled(
                f"order for approval {fields.get('approval_id')} is already at status "
                f"{fields.get('order_status')}"
            ) from exc
        except SQLAlchemyError as exc:
            raise JournalWriteError(f"could not append order: {exc.__class__.__name__}") from exc
        return str(fields["order_id"])

    def approval_row(self, approval_id: str, status: str) -> ApprovalRow | None:
        with self.session_scope() as session:
            statement = select(ApprovalRow).where(
                ApprovalRow.approval_id == approval_id, ApprovalRow.status == status
            )
            return session.execute(statement).scalars().first()

    def open_approvals(self) -> Sequence[ApprovalRow]:
        """Pending approvals with no terminal row yet, oldest first."""
        with self.session_scope() as session:
            settled = select(ApprovalRow.approval_id).where(ApprovalRow.status != APPROVAL_PENDING)
            statement = (
                select(ApprovalRow)
                .where(
                    ApprovalRow.status == APPROVAL_PENDING,
                    ApprovalRow.approval_id.not_in(settled),
                )
                .order_by(ApprovalRow.created_at_utc.asc())
            )
            return list(session.execute(statement).scalars())

    def expired_awaiting_late_check(self, trading_day: str) -> Sequence[ApprovalRow]:
        """Expired approvals whose queued order was never withdrawn and may still be clicked."""
        with self.session_scope() as session:
            already_late = select(ApprovalRow.approval_id).where(
                ApprovalRow.status == APPROVAL_LATE
            )
            statement = select(ApprovalRow).where(
                ApprovalRow.trading_day == trading_day,
                ApprovalRow.status == APPROVAL_EXPIRED,
                ApprovalRow.pending_order_id.is_not(None),
                ApprovalRow.approval_id.not_in(already_late),
            )
            return list(session.execute(statement).scalars())

    def watchable_orders(self, trading_day: str) -> Sequence[OrderRow]:
        """The latest order row per approval today, where the order has not finished."""
        with self.session_scope() as session:
            latest = (
                select(func.max(OrderRow.id))
                .where(OrderRow.trading_day == trading_day)
                .group_by(OrderRow.approval_id)
                .scalar_subquery()
            )
            statement = select(OrderRow).where(
                OrderRow.id.in_(latest), OrderRow.order_status.not_in(ORDER_TERMINAL)
            )
            return list(session.execute(statement).scalars())

    def list_approvals(self, trading_day: str, limit: int = 100) -> Sequence[ApprovalRow]:
        with self.session_scope() as session:
            statement = (
                select(ApprovalRow)
                .where(ApprovalRow.trading_day == trading_day)
                .order_by(ApprovalRow.created_at_utc.asc())
                .limit(limit)
            )
            return list(session.execute(statement).scalars())

    def orders_for_approval(self, approval_id: str) -> Sequence[OrderRow]:
        with self.session_scope() as session:
            statement = (
                select(OrderRow)
                .where(OrderRow.approval_id == approval_id)
                .order_by(OrderRow.created_at_utc.asc())
            )
            return list(session.execute(statement).scalars())

    def proposal(self, proposal_id: str) -> Proposal | None:
        with self.session_scope() as session:
            statement = select(Proposal).where(Proposal.proposal_id == proposal_id)
            return session.execute(statement).scalars().first()

    def risk_verdict(self, verdict_id: str) -> RiskVerdictRow | None:
        with self.session_scope() as session:
            statement = select(RiskVerdictRow).where(RiskVerdictRow.verdict_id == verdict_id)
            return session.execute(statement).scalars().first()

    def regime_read_for_tick(self, tick_id: str) -> RegimeRead | None:
        with self.session_scope() as session:
            statement = select(RegimeRead).where(RegimeRead.tick_id == tick_id)
            return session.execute(statement).scalars().first()

    def recent_trading_days(self, limit: int = 5) -> list[str]:
        """The most recent days that hold at least one decision, newest first."""
        with self.session_scope() as session:
            statement = (
                select(Decision.trading_day)
                .group_by(Decision.trading_day)
                .order_by(Decision.trading_day.desc())
                .limit(limit)
            )
            return [str(day) for day in session.execute(statement).scalars()]

    def spans_for_trace(self, trace_id: str) -> Sequence[TraceSpan]:
        with self.session_scope() as session:
            statement = (
                select(TraceSpan)
                .where(TraceSpan.trace_id == trace_id)
                .order_by(TraceSpan.started_at_utc.asc())
            )
            return list(session.execute(statement).scalars())

    def table_names(self) -> list[str]:
        return list(inspect(self._engine).get_table_names())

    def order_row(self, order_id: str) -> OrderRow | None:
        with self.session_scope() as session:
            statement = select(OrderRow).where(OrderRow.order_id == order_id).limit(1)
            return session.execute(statement).scalars().first()

    def latest_approval(self) -> ApprovalRow | None:
        with self.session_scope() as session:
            statement = select(ApprovalRow).order_by(ApprovalRow.id.desc()).limit(1)
            return session.execute(statement).scalars().first()

    def record_position_state(self, **fields: Any) -> str:
        """Append one position state. Raises AlreadyJournalled when that state exists."""
        try:
            with self.session_scope() as session:
                session.add(PositionRow(**fields))
        except IntegrityError as exc:
            raise AlreadyJournalled(
                f"position {fields.get('position_id')} is already at state "
                f"{fields.get('state')}"
            ) from exc
        except SQLAlchemyError as exc:
            raise JournalWriteError(
                f"could not append position state: {exc.__class__.__name__}"
            ) from exc
        return str(fields["position_id"])

    def record_exit(self, **fields: Any) -> str:
        """Append one exit attempt. Raises AlreadyJournalled when that attempt exists."""
        try:
            with self.session_scope() as session:
                session.add(ExitRow(**fields))
        except IntegrityError as exc:
            raise AlreadyJournalled(
                f"position {fields.get('position_id')} already has attempt "
                f"{fields.get('attempt')}"
            ) from exc
        except SQLAlchemyError as exc:
            raise JournalWriteError(f"could not append exit: {exc.__class__.__name__}") from exc
        return str(fields["exit_id"])

    def live_position(self) -> PositionRow | None:
        """The most recent non-terminal position, or None when the book is flat."""
        with self.session_scope() as session:
            terminal = select(PositionRow.position_id).where(
                PositionRow.state.in_(tuple(POSITION_TERMINAL))
            )
            statement = (
                select(PositionRow)
                .where(PositionRow.position_id.not_in(terminal))
                .order_by(PositionRow.id.desc())
                .limit(1)
            )
            return session.execute(statement).scalars().first()

    def position_states(self, position_id: str) -> Sequence[PositionRow]:
        with self.session_scope() as session:
            statement = (
                select(PositionRow)
                .where(PositionRow.position_id == position_id)
                .order_by(PositionRow.id.asc())
            )
            return list(session.execute(statement).scalars())

    def position_for_order(self, order_id: str) -> PositionRow | None:
        with self.session_scope() as session:
            statement = (
                select(PositionRow).where(PositionRow.order_id == order_id).limit(1)
            )
            return session.execute(statement).scalars().first()

    def exits_for_position(self, position_id: str) -> Sequence[ExitRow]:
        with self.session_scope() as session:
            statement = (
                select(ExitRow)
                .where(ExitRow.position_id == position_id)
                .order_by(ExitRow.attempt.asc(), ExitRow.id.asc())
            )
            return list(session.execute(statement).scalars())

    def list_positions(self, trading_day: str, limit: int = 100) -> Sequence[PositionRow]:
        with self.session_scope() as session:
            statement = (
                select(PositionRow)
                .where(PositionRow.trading_day == trading_day)
                .order_by(PositionRow.created_at_utc.asc())
                .limit(limit)
            )
            return list(session.execute(statement).scalars())

    def unadopted_orders(self, trading_day: str) -> Sequence[OrderRow]:
        """Orders that reached 'complete' today and were never adopted by the monitor."""
        with self.session_scope() as session:
            adopted = select(PositionRow.order_id).where(PositionRow.order_id.is_not(None))
            statement = (
                select(OrderRow)
                .where(
                    OrderRow.trading_day == trading_day,
                    OrderRow.order_status == "complete",
                    OrderRow.order_id.not_in(adopted),
                )
                .order_by(OrderRow.created_at_utc.asc())
            )
            return list(session.execute(statement).scalars())

    def realised_pnl_for_day(self, trading_day: date | str) -> float:
        """Sum of realised P&L across every position that reached flat on this trading day."""
        day = trading_day.isoformat() if isinstance(trading_day, date) else trading_day
        with self.session_scope() as session:
            rows = session.execute(
                select(PositionRow.realised_pnl).where(
                    PositionRow.state == POSITION_FLAT,
                    PositionRow.trading_day == day,
                )
            ).scalars()
            return float(sum(value for value in rows if value is not None))

    def entry_count_for_day(self, trading_day: date | str) -> int:
        """How many entry orders were submitted on this trading day, in any final status."""
        day = trading_day.isoformat() if isinstance(trading_day, date) else trading_day
        with self.session_scope() as session:
            return int(
                session.execute(
                    select(func.count())
                    .select_from(OrderRow)
                    .where(OrderRow.trading_day == day, OrderRow.action == "BUY")
                ).scalar_one()
            )

    def close(self) -> None:
        """Dispose the engine — every connection released, no descriptor left open."""
        self._engine.dispose()
