"""Registry of Strike Desk / Hands books shown on My Strategies.

One OpenAlgo instance, one list. Seeded with the live NIFTY books plus the
parked BANKNIFTY row. Status is derived at read time — this table is identity
only, not a second book of record.
"""

import os
from typing import Any

from sqlalchemy import Boolean, Column, Integer, String, Text, create_engine, desc
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import NullPool

from utils.logging import get_logger

logger = get_logger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL and "sqlite" in DATABASE_URL:
    engine = create_engine(
        DATABASE_URL, poolclass=NullPool, connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(DATABASE_URL, pool_size=50, max_overflow=100, pool_timeout=10)

db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
Base = declarative_base()
Base.query = db_session.query_property()

# NIFTY-only slice. Do not add Crude or Live Auto here.
SEED_BOOKS = (
    {
        "book_id": "analyzer-nifty",
        "name": "Strike Desk NIFTY Analyzer",
        "kind": "analyzer",
        "underlying": "NIFTY",
        "execution_target": "openalgo",
        "tt_sid": None,
        "tt_template_id": None,
        "parked": False,
        "notes": "15-min regime ATM PE buyer. Fills in OpenAlgo Analyze. No Tradetron SID.",
        "sort_order": 5,
    },
    {
        "book_id": "qs-nifty",
        "name": "Strike Desk QuadShield NIFTY Hands",
        "kind": "strikeshield",
        "underlying": "NIFTY",
        "execution_target": "tradetron-hands",
        "tt_sid": 999328734,
        "tt_template_id": 999159502,
        "parked": False,
        "notes": "Mind: StrikeShield. Fills: Tradetron Hands. Live Offline.",
        "sort_order": 10,
    },
    {
        "book_id": "ema120-nifty",
        "name": "Strike Desk EMA120 NIFTY",
        "kind": "overlay",
        "underlying": "NIFTY",
        "execution_target": "tradetron-hands",
        "tt_sid": 999308846,
        "tt_template_id": 999159334,
        "parked": False,
        "notes": "LLM overlay fan-out (NIFTY50 _el). Waiting until the mind accepts a buy.",
        "sort_order": 20,
    },
    {
        "book_id": "ema120-bn",
        "name": "PARKED — EMA120 BANKNIFTY",
        "kind": "overlay",
        "underlying": "BANKNIFTY",
        "execution_target": "none",
        "tt_sid": 999308850,
        "tt_template_id": 999159338,
        "parked": True,
        "notes": "Parked. Pause the Tradetron row. Do not redeploy.",
        "sort_order": 90,
    },
)


class StrategyBook(Base):
    __tablename__ = "strategy_books"

    book_id = Column(String(40), primary_key=True)
    name = Column(String(160), nullable=False)
    kind = Column(String(32), nullable=False)
    underlying = Column(String(40), nullable=False)
    execution_target = Column(String(32), nullable=False)
    tt_sid = Column(Integer, nullable=True)
    tt_template_id = Column(Integer, nullable=True)
    parked = Column(Boolean, nullable=False, default=False)
    notes = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=100)


class StrategyEvent(Base):
    """Append-only observed events for books that do not have their own journal."""

    __tablename__ = "strategy_events"

    id = Column(Integer, primary_key=True)
    event_id = Column(String(96), unique=True, nullable=False)
    book_id = Column(String(40), nullable=False, index=True)
    created_at_utc = Column(String(40), nullable=False)
    trading_day = Column(String(10), nullable=False, index=True)
    kind = Column(String(24), nullable=False)
    title = Column(String(200), nullable=False)
    outcome = Column(String(24), nullable=True)
    reason_code = Column(String(64), nullable=True)
    explanation = Column(Text, nullable=False)
    detail = Column(Text, nullable=True)


def init_db():
    from database.db_init_helper import init_db_with_logging

    init_db_with_logging(Base, engine, "Strategy Books DB", logger)
    _seed_if_empty()
    ensure_missing_books()


def ensure_strategy_books_tables_exists():
    init_db()


def _seed_if_empty() -> None:
    try:
        if db_session.query(StrategyBook).first() is not None:
            return
        for row in SEED_BOOKS:
            db_session.add(StrategyBook(**row))
        db_session.commit()
        logger.info("Seeded %d strategy books", len(SEED_BOOKS))
    except Exception:
        logger.exception("strategy_books seed failed")
        db_session.rollback()


def ensure_missing_books() -> None:
    """Insert seed rows that are absent. Never updates an existing book."""
    try:
        existing = {row.book_id for row in db_session.query(StrategyBook.book_id).all()}
        added = 0
        for row in SEED_BOOKS:
            if row["book_id"] in existing:
                continue
            db_session.add(StrategyBook(**row))
            added += 1
        if added:
            db_session.commit()
            logger.info("Added %d missing strategy books", added)
    except Exception:
        logger.exception("strategy_books ensure_missing failed")
        db_session.rollback()


def _serialize(row: StrategyBook) -> dict[str, Any]:
    return {
        "id": row.book_id,
        "name": row.name,
        "kind": row.kind,
        "underlying": row.underlying,
        "execution_target": row.execution_target,
        "tt_sid": row.tt_sid,
        "tt_template_id": row.tt_template_id,
        "parked": bool(row.parked),
        "notes": row.notes,
        "sort_order": row.sort_order,
    }


def list_books() -> list[dict[str, Any]]:
    try:
        rows = db_session.query(StrategyBook).order_by(StrategyBook.sort_order, StrategyBook.name).all()
        return [_serialize(r) for r in rows]
    except Exception:
        logger.exception("list_books failed")
        return []


def get_book(book_id: str) -> dict[str, Any] | None:
    try:
        row = db_session.query(StrategyBook).filter_by(book_id=book_id).first()
        return _serialize(row) if row is not None else None
    except Exception:
        logger.exception("get_book failed")
        return None


def _serialize_event(row: StrategyEvent) -> dict[str, Any]:
    return {
        "event_id": row.event_id,
        "book_id": row.book_id,
        "created_at_utc": row.created_at_utc,
        "trading_day": row.trading_day,
        "kind": row.kind,
        "title": row.title,
        "outcome": row.outcome,
        "reason_code": row.reason_code,
        "explanation": row.explanation,
        "detail": row.detail,
    }


def append_event(fields: dict[str, Any]) -> bool:
    """Insert one event. Returns False if the id already exists or the write fails."""
    try:
        if db_session.query(StrategyEvent).filter_by(event_id=fields["event_id"]).first():
            return False
        db_session.add(StrategyEvent(**fields))
        db_session.commit()
        return True
    except Exception:
        logger.exception("append strategy event failed")
        db_session.rollback()
        return False


def list_events(book_id: str, limit: int = 200) -> list[dict[str, Any]]:
    try:
        rows = (
            db_session.query(StrategyEvent)
            .filter_by(book_id=book_id)
            .order_by(desc(StrategyEvent.created_at_utc), desc(StrategyEvent.id))
            .limit(limit)
            .all()
        )
        return [_serialize_event(row) for row in rows]
    except Exception:
        logger.exception("list_events failed")
        return []


def latest_event(book_id: str) -> dict[str, Any] | None:
    rows = list_events(book_id, limit=1)
    return rows[0] if rows else None
