"""A read-only window onto OpenAlgo's own database.

Strike Desk reaches OpenAlgo through its published HTTP and MCP surfaces everywhere else.
The Action Center is the one exception, and not by choice: its routes are session-guarded
browser endpoints, so the approval verdict, the approver's identity, the rejection reason and
the broker order id are readable only from the table OpenAlgo writes them to.

The connection is opened ``mode=ro``. Nothing here can write, and the tests prove it by
trying.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Boolean, Integer, String, Text, create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import NullPool

from .config import Settings
from .errors import MirrorUnavailable

logger = logging.getLogger(__name__)

ORDER_MODE_SEMI_AUTO = "semi_auto"
ORDER_MODE_AUTO = "auto"

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"


class MirrorBase(DeclarativeBase):
    """Declarative base for OpenAlgo's tables. Strike Desk never creates or alters them."""


class ApiKeyRow(MirrorBase):
    """Only the two columns the gate needs from ``api_keys``."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    order_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)


class PendingOrderRow(MirrorBase):
    """Only the columns the watcher needs from ``pending_orders``."""

    __tablename__ = "pending_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    api_type: Mapped[str] = mapped_column(String(50), nullable=False)
    order_data: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at_ist: Mapped[str | None] = mapped_column(String(50), nullable=True)
    approved_at_ist: Mapped[str | None] = mapped_column(String(50), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rejected_at_ist: Mapped[str | None] = mapped_column(String(50), nullable=True)
    rejected_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    broker_status: Mapped[str | None] = mapped_column(String(20), nullable=True)


class PlatformSettingsRow(MirrorBase):
    """OpenAlgo's single-row settings table. Only the analyze-mode flag is read."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analyze_mode: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


@dataclass(frozen=True)
class PendingOrder:
    """One Action Center row, detached from its session and safe to pass between threads."""

    pending_order_id: int
    user_id: str
    api_type: str
    status: str
    created_at_ist: str | None
    approved_at_ist: str | None
    approved_by: str | None
    rejected_at_ist: str | None
    rejected_by: str | None
    rejected_reason: str | None
    broker_order_id: str | None
    broker_status: str | None

    @property
    def resolved(self) -> bool:
        return self.status in {APPROVED, REJECTED}

    @property
    def resolved_at_ist(self) -> str | None:
        if self.status == APPROVED:
            return self.approved_at_ist
        if self.status == REJECTED:
            return self.rejected_at_ist
        return None


@dataclass(frozen=True)
class GateHealth:
    """Whether an order may be submitted at all, and why not when it may not."""

    ok: bool
    order_mode: str | None
    detail: str


class OpenAlgoMirror:
    """Read-only reader over OpenAlgo's database. One instance per process."""

    def __init__(self, settings: Settings) -> None:
        self._path: Path = settings.openalgo_db_path
        self._user = (settings.openalgo_user or "").strip()
        url = f"sqlite:///file:{self._path.as_posix()}?mode=ro&uri=true"
        self._engine = create_engine(url, poolclass=NullPool, future=True)
        self._sessionmaker = sessionmaker(bind=self._engine, expire_on_commit=False)

    @property
    def path(self) -> Path:
        return self._path

    @contextmanager
    def _session_scope(self) -> Iterator[Session]:
        """A read-only session, closed on every path including the error path."""
        session = self._sessionmaker()
        try:
            yield session
        finally:
            session.close()

    def order_mode(self) -> str:
        """The configured user's order mode. Raises rather than guessing."""
        if not self._user:
            raise MirrorUnavailable("STRIKE_DESK_OPENALGO_USER is not configured")
        if not self._path.exists():
            raise MirrorUnavailable(f"{self._path} does not exist")
        try:
            with self._session_scope() as session:
                statement = select(ApiKeyRow.order_mode).where(ApiKeyRow.user_id == self._user)
                found = session.execute(statement).scalars().first()
        except SQLAlchemyError as exc:
            raise MirrorUnavailable(
                f"{self._path}: could not read api_keys ({exc.__class__.__name__})"
            ) from exc
        if found is None:
            raise MirrorUnavailable(f"no api_keys row for user {self._user!r}")
        return str(found)

    def analyze_mode(self) -> bool:
        """True when OpenAlgo is in analyze (sandbox) mode. Raises rather than guessing."""
        if not self._path.exists():
            raise MirrorUnavailable(f"{self._path} does not exist")
        try:
            with self._session_scope() as session:
                statement = select(PlatformSettingsRow.analyze_mode).limit(1)
                found = session.execute(statement).scalars().first()
        except SQLAlchemyError as exc:
            raise MirrorUnavailable(
                f"{self._path}: could not read settings ({exc.__class__.__name__})"
            ) from exc
        return bool(found)

    def health(self, expected: str = ORDER_MODE_SEMI_AUTO) -> GateHealth:
        """Whether OpenAlgo's order mode matches the mode this desk is configured for."""
        try:
            mode = self.order_mode()
        except MirrorUnavailable as exc:
            return GateHealth(False, None, str(exc))
        if mode != expected:
            if expected == ORDER_MODE_AUTO:
                return GateHealth(
                    False,
                    mode,
                    f"OpenAlgo order mode is {mode!r}, not {ORDER_MODE_AUTO!r}: "
                    "an unattended entry would be queued for a click nobody will give",
                )
            return GateHealth(
                False,
                mode,
                f"OpenAlgo order mode is {mode!r}, not {ORDER_MODE_SEMI_AUTO!r}: "
                "an order would reach the broker without a human approval",
            )
        if expected == ORDER_MODE_AUTO:
            return GateHealth(True, mode, "auto order mode is active — unattended entries place directly")
        return GateHealth(True, mode, "semi-auto approval gate is active")

    def pending_order(self, pending_order_id: int) -> PendingOrder | None:
        """One Action Center row by id, or None when the trader has deleted it."""
        try:
            with self._session_scope() as session:
                row = session.get(PendingOrderRow, int(pending_order_id))
                if row is None:
                    return None
                return PendingOrder(
                    pending_order_id=int(row.id),
                    user_id=str(row.user_id),
                    api_type=str(row.api_type),
                    status=str(row.status or PENDING),
                    created_at_ist=row.created_at_ist,
                    approved_at_ist=row.approved_at_ist,
                    approved_by=row.approved_by,
                    rejected_at_ist=row.rejected_at_ist,
                    rejected_by=row.rejected_by,
                    rejected_reason=row.rejected_reason,
                    broker_order_id=row.broker_order_id,
                    broker_status=row.broker_status,
                )
        except SQLAlchemyError as exc:
            raise MirrorUnavailable(
                f"{self._path}: could not read pending_orders ({exc.__class__.__name__})"
            ) from exc

    def close(self) -> None:
        """Dispose the engine — no descriptor is left open against OpenAlgo's database."""
        self._engine.dispose()
