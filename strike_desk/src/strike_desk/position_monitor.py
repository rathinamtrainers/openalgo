"""The Position Monitor: from fill to flat, deterministically.

No model call, no MCP tool call, and no import of the reasoning plane appears anywhere in this
module or in anything it imports. The levels were chosen by an agent at proposal time; holding
the position to them is arithmetic on a thread.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from opentelemetry.trace import Span

from .config import IST, Settings
from .errors import (
    AlreadyJournalled,
    JournalWriteError,
    LevelsUnavailable,
    OpenAlgoError,
    StrikeDeskError,
)
from .exit_executor import ExitExecutor, exit_path_status
from .journal import (
    EXIT_FEED_BLACKOUT,
    EXIT_FILLED,
    EXIT_LEVELS_UNAVAILABLE,
    EXIT_MANUAL,
    EXIT_SUBMITTED,
    ORDER_TERMINAL,
    POSITION_ADOPTED,
    POSITION_ARMED,
    POSITION_EXITING,
    POSITION_FLAT,
    POSITION_ORPHANED,
    POSITION_STOOD_DOWN,
    Journal,
)
from .levels import ExitLevels, ExitTrigger, evaluate, resolve_time_stop
from .observability import get_tracer
from .openalgo_client import OpenAlgoClient
from .openalgo_mirror import OpenAlgoMirror
from .price_feed import SOURCE_NONE, SOURCE_QUOTES, PriceFeed, Tick
from .session import engage_kill_switch

logger = logging.getLogger(__name__)


@dataclass
class ManagedPosition:
    """The monitor's working set for one position. Rebuilt from the journal on restart."""

    position_id: str
    order_id: str | None
    approval_id: str | None
    proposal_id: str | None
    tick_id: str | None
    trace_id: str
    trading_day: str
    symbol: str
    exchange: str
    product: str
    quantity: int
    entry_price: float
    levels: ExitLevels
    theta_per_day: float | None
    attempts: int = 0


def _trading_day(now_utc: datetime) -> str:
    return now_utc.astimezone(IST).date().isoformat()


def _as_float(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value


class PositionMonitor:
    """One thread that holds at most one position to its levels."""

    def __init__(
        self,
        settings: Settings,
        journal: Journal,
        client: OpenAlgoClient,
        executor: ExitExecutor,
        mirror: OpenAlgoMirror,
    ) -> None:
        self._settings = settings
        self._journal = journal
        self._client = client
        self._executor = executor
        self._mirror = mirror
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._feed: PriceFeed | None = None
        self._position: ManagedPosition | None = None
        self._last_reconcile = 0.0
        self._blackout_since: datetime | None = None
        self._pending_adoptions: list[str] = []
        self._heartbeat_utc = datetime.now(UTC)

    @property
    def mirror(self) -> OpenAlgoMirror:
        return self._mirror

    @property
    def heartbeat_utc(self) -> datetime:
        return self._heartbeat_utc

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="position-monitor", daemon=True)
        self._thread.start()
        logger.info("position monitor started (poll=%.1fs)", self._settings.monitor_poll_seconds)

    def close(self) -> None:
        """Stop watching. The position, if any, stays open — auto square-off is the backstop."""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=10.0)
            if thread.is_alive():
                logger.warning("position monitor did not stop within 10s")
        self._thread = None
        self._close_feed()

    def notify_fill(self, order_id: str) -> None:
        """Called by the approval watcher the moment an entry order reaches 'complete'."""
        with self._lock:
            if order_id not in self._pending_adoptions:
                self._pending_adoptions.append(order_id)
        self._wake.set()

    def snapshot(self) -> dict[str, Any]:
        """What the operator view prints. Safe to call from any thread."""
        with self._lock:
            position = self._position
        if position is None:
            return {"managed": False}
        tick = self._feed.last_tick() if self._feed is not None else None
        now = datetime.now(tz=UTC)
        return {
            "managed": True,
            "position_id": position.position_id,
            "symbol": position.symbol,
            "exchange": position.exchange,
            "quantity": position.quantity,
            "entry_price": position.entry_price,
            "levels": position.levels.as_dict(),
            "last_price": tick.price if tick else None,
            "feed_source": tick.source if tick else SOURCE_NONE,
            "feed_age_ms": tick.age_ms(now) if tick else None,
            "attempts": position.attempts,
        }

    # --- the loop ----------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._cycle()
            except Exception:  # noqa: BLE001 — the monitor thread must survive anything
                logger.exception("position monitor cycle failed")
            self._wake.wait(timeout=self._settings.monitor_poll_seconds)
            self._wake.clear()

    def _cycle(self) -> None:
        self._heartbeat_utc = datetime.now(UTC)
        self._drain_adoptions()
        if self._position is None:
            self._adopt_unadopted_orders()
            return
        self._reconcile_if_due()
        if self._position is None:
            return
        self._evaluate()

    def _drain_adoptions(self) -> None:
        with self._lock:
            pending = list(self._pending_adoptions)
            self._pending_adoptions.clear()
        for order_id in pending:
            if self._position is not None:
                logger.error(
                    "order %s filled while position %s is still managed — not adopting a second",
                    order_id, self._position.position_id,
                )
                continue
            self._adopt(order_id)

    def _adopt_unadopted_orders(self) -> None:
        """Startup and restart safety: a filled order nobody is watching is adopted here."""
        day = _trading_day(datetime.now(tz=UTC))
        try:
            orphans = self._journal.unadopted_orders(day)
        except StrikeDeskError:
            logger.exception("could not query unadopted orders")
            return
        for order in orphans:
            self._adopt(order.order_id)
            if self._position is not None:
                return

    # --- adoption ----------------------------------------------------------

    def _adopt(self, order_id: str) -> None:
        tracer = get_tracer()
        with tracer.start_as_current_span("strike_desk.monitor.adopt") as span:
            span.set_attribute("order.id", order_id)
            try:
                self._adopt_inner(order_id, span)
            except LevelsUnavailable as exc:
                logger.exception("levels unresolvable for order %s", order_id)
                span.set_attribute("adopt.defect", str(exc))
                self._exit_unmanageable(order_id, str(exc))
            except (StrikeDeskError, OpenAlgoError):
                logger.exception("could not adopt order %s", order_id)

    def _adopt_inner(self, order_id: str, span: Span) -> None:
        orders = list(self._journal.watchable_orders(_trading_day(datetime.now(tz=UTC))))
        order = self._journal.position_for_order(order_id)
        if order is not None:
            logger.info("order %s is already adopted as position %s", order_id, order.position_id)
            return
        del orders  # the watchable list is a liveness aid only; adoption reads the order row

        record = self._journal.order_row(order_id)
        if record is None:
            raise LevelsUnavailable(f"no orders row for {order_id}")
        approval = self._journal.approval_row(record.approval_id, "approved")
        if approval is None:
            approval = self._journal.approval_row(record.approval_id, "auto-approved")
        proposal_id = approval.proposal_id if approval is not None else None
        proposal = self._journal.proposal(proposal_id) if proposal_id else None
        if proposal is None:
            raise LevelsUnavailable(f"no proposal behind order {order_id}")

        now = datetime.now(tz=UTC)
        day = _trading_day(now)
        time_stop, time_stop_reason = resolve_time_stop(
            proposal.time_stop_ist,
            self._settings.session_exit_deadline_time,
            date.fromisoformat(day),
        )
        levels = ExitLevels(
            stop_price=float(proposal.stop_price or 0.0),
            target_price=float(proposal.target_price or 0.0),
            time_stop_utc=time_stop,
            time_stop_reason=time_stop_reason,
        )

        quantity, entry_price = self._broker_position(record.symbol)
        if quantity <= 0:
            logger.warning(
                "order %s is complete but the broker reports no position in %s — nothing to adopt",
                order_id, record.symbol,
            )
            return

        position = ManagedPosition(
            position_id=str(uuid.uuid4()),
            order_id=record.order_id,
            approval_id=record.approval_id,
            proposal_id=proposal.proposal_id,
            tick_id=record.tick_id,
            trace_id=format(span.get_span_context().trace_id, "032x"),
            trading_day=day,
            symbol=record.symbol,
            exchange=record.exchange,
            product=record.product,
            quantity=quantity,
            entry_price=entry_price or float(record.average_price or 0.0),
            levels=levels,
            theta_per_day=proposal.theta_per_day,
        )
        self._write_state(position, POSITION_ADOPTED, detail=f"adopted from order {order_id}")
        span.set_attribute("position.id", position.position_id)
        span.set_attribute("position.symbol", position.symbol)
        span.set_attribute("position.quantity", position.quantity)
        span.set_attribute("levels.stop", levels.stop_price)
        span.set_attribute("levels.target", levels.target_price)
        span.set_attribute("levels.time_stop", levels.time_stop_utc.isoformat())

        feed = PriceFeed(self._settings, position.symbol, position.exchange)
        feed.start()
        armed = feed.wait_for_first_tick(timeout=self._settings.feed_stale_seconds)
        with self._lock:
            self._feed = feed
            self._position = position
        self._blackout_since = None if armed else datetime.now(tz=UTC)
        self._write_state(
            position,
            POSITION_ARMED,
            detail=(
                "armed on a live feed" if armed else "armed without a first tick — polling quotes"
            ),
        )
        logger.info(
            "managing %s x %s at %.2f — stop %.2f target %.2f time-stop %s",
            position.quantity, position.symbol, position.entry_price,
            levels.stop_price, levels.target_price,
            levels.time_stop_utc.astimezone(IST).strftime("%H:%M IST"),
        )

    def _broker_position(self, symbol: str) -> tuple[int, float]:
        """The broker's truth about our symbol: net quantity and average entry price."""
        try:
            book = self._client.positionbook()
        except OpenAlgoError:
            logger.exception("could not read the position book during adoption")
            return 0, 0.0
        for entry in book:
            if str(entry.get("symbol", "")).strip().upper() != symbol.strip().upper():
                continue
            quantity = _as_float(entry.get("quantity")) or 0.0
            average = _as_float(entry.get("average_price")) or 0.0
            return int(quantity), average
        return 0, 0.0

    def _exit_unmanageable(self, order_id: str, detail: str) -> None:
        """A filled order whose levels cannot be resolved is closed at market immediately."""
        record = self._journal.order_row(order_id)
        if record is None:
            return
        quantity, entry_price = self._broker_position(record.symbol)
        if quantity <= 0:
            return
        now = datetime.now(tz=UTC)
        position = ManagedPosition(
            position_id=str(uuid.uuid4()),
            order_id=record.order_id,
            approval_id=record.approval_id,
            proposal_id=None,
            tick_id=record.tick_id,
            trace_id=format(0, "032x"),
            trading_day=_trading_day(now),
            symbol=record.symbol,
            exchange=record.exchange,
            product=record.product,
            quantity=quantity,
            entry_price=entry_price,
            levels=ExitLevels(0.05, 1e9, now, "time-stop"),
            theta_per_day=None,
        )
        self._write_state(position, POSITION_ADOPTED, detail=detail, defect=True)
        with self._lock:
            self._position = position
        self._fire_exit(ExitTrigger(EXIT_LEVELS_UNAVAILABLE, None, None), tick=None)

    # --- watching ----------------------------------------------------------

    def _current_tick(self) -> Tick | None:
        """The freshest observation available, falling back to a REST quote when stale."""
        feed = self._feed
        position = self._position
        if feed is None or position is None:
            return None
        now = datetime.now(tz=UTC)
        tick = feed.last_tick()
        if tick is not None and tick.age_ms(now) <= self._settings.feed_stale_seconds * 1000:
            self._blackout_since = None
            return tick
        try:
            quote = self._client.quotes(position.symbol, position.exchange)
        except OpenAlgoError:
            logger.warning("quote fallback failed for %s", position.symbol)
            if self._blackout_since is None:
                self._blackout_since = now
            return tick
        price = _as_float(quote.get("ltp"))
        if price is None or price <= 0:
            if self._blackout_since is None:
                self._blackout_since = now
            return tick
        self._blackout_since = None
        return Tick(price=price, source=SOURCE_QUOTES, received_at_utc=now)

    def _evaluate(self) -> None:
        position = self._position
        if position is None:
            return
        now = datetime.now(tz=UTC)
        tick = self._current_tick()
        if self._blackout_since is not None:
            blackout_for = (now - self._blackout_since).total_seconds()
            if blackout_for >= self._settings.feed_blackout_seconds:
                logger.critical(
                    "no usable price for %s in %.0fs — exiting at market",
                    position.symbol, blackout_for,
                )
                self._fire_exit(ExitTrigger(EXIT_FEED_BLACKOUT, None, None), tick)
                return
        trigger = evaluate(position.levels, tick.price if tick else None, now)
        if trigger is None:
            return
        self._fire_exit(trigger, tick)

    def _reconcile_if_due(self) -> None:
        now = datetime.now(tz=UTC).timestamp()
        if now - self._last_reconcile < self._settings.reconcile_interval_seconds:
            return
        self._last_reconcile = now
        position = self._position
        if position is None:
            return
        with get_tracer().start_as_current_span("strike_desk.monitor.reconcile") as span:
            quantity, _price = self._broker_position(position.symbol)
            span.set_attribute("position.id", position.position_id)
            span.set_attribute("broker.quantity", quantity)
            if quantity > 0:
                if quantity != position.quantity:
                    logger.warning(
                        "position %s quantity moved %d -> %d at the broker; adopting the broker",
                        position.position_id, position.quantity, quantity,
                    )
                    position.quantity = quantity
                return
            logger.info(
                "position %s is flat at the broker and the desk did not close it — standing down",
                position.position_id,
            )
            self._write_state(
                position, POSITION_STOOD_DOWN,
                detail="closed outside the desk; reconciled from the position book",
                exit_reason=EXIT_MANUAL,
            )
            self._stand_down()

    # --- exiting -----------------------------------------------------------

    def _fire_exit(self, trigger: ExitTrigger, tick: Tick | None) -> None:
        position = self._position
        if position is None:
            return
        triggered_at = datetime.now(tz=UTC)
        self._write_state(
            position, POSITION_EXITING,
            detail=f"{trigger.reason} at {trigger.observed_price}",
            exit_reason=trigger.reason,
        )
        with get_tracer().start_as_current_span("strike_desk.monitor.exit") as span:
            span.set_attribute("position.id", position.position_id)
            span.set_attribute("exit.reason", trigger.reason)
            span.set_attribute("exit.observed_price", trigger.observed_price or 0.0)
            max_attempts = self._settings.exit_max_attempts
            for attempt_number in range(position.attempts + 1, max_attempts + 1):
                position.attempts = attempt_number
                attempt = self._executor.fire(position.symbol, position.exchange, position.quantity)
                latency_ms = int(
                    ((attempt.submitted_at_utc or datetime.now(tz=UTC)) - triggered_at
                     ).total_seconds() * 1000
                )
                span.set_attribute("exit.attempt", attempt_number)
                span.set_attribute("exit.latency_ms", latency_ms)
                span.set_attribute("exit.path", attempt.path)
                span.set_attribute("exit.status", attempt.status)
                exit_price, broker_order_id = self._settle_attempt(attempt, position)
                self._record_exit(
                    position, trigger, attempt, attempt_number, triggered_at, latency_ms,
                    tick, exit_price, broker_order_id,
                )
                if latency_ms > self._settings.exit_latency_budget_ms:
                    logger.warning(
                        "exit latency %dms exceeded the %dms budget for position %s",
                        latency_ms, self._settings.exit_latency_budget_ms, position.position_id,
                    )
                if attempt.status == EXIT_SUBMITTED:
                    self._finish(position, trigger, exit_price)
                    return
                self._stop.wait(self._settings.exit_retry_seconds)
            self._escalate(position, trigger)

    def _settle_attempt(
        self, attempt: Any, position: ManagedPosition
    ) -> tuple[float | None, str | None]:
        """Follow a submitted exit to a fill, so slippage and P&L are real numbers."""
        if attempt.status != EXIT_SUBMITTED or not attempt.broker_order_id:
            return None, attempt.broker_order_id
        deadline = time.monotonic() + self._settings.fill_deadline_seconds
        while time.monotonic() < deadline and not self._stop.is_set():
            try:
                data = self._execution_status(attempt.broker_order_id)
            except OpenAlgoError:
                logger.exception("could not read exit order status")
                return None, attempt.broker_order_id
            status = str(data.get("order_status", "")).lower()
            if status in ORDER_TERMINAL:
                return _as_float(data.get("average_price")), attempt.broker_order_id
            self._stop.wait(1.0)
        return None, attempt.broker_order_id

    def _execution_status(self, broker_order_id: str) -> dict[str, Any]:
        strategy = f"{self._settings.order_strategy_prefix}-{self._settings.index_symbol}"
        return self._executor._execution.order_status(broker_order_id, strategy)  # noqa: SLF001

    def _record_exit(
        self,
        position: ManagedPosition,
        trigger: ExitTrigger,
        attempt: Any,
        attempt_number: int,
        triggered_at: datetime,
        latency_ms: int,
        tick: Tick | None,
        exit_price: float | None,
        broker_order_id: str | None,
    ) -> None:
        now = datetime.now(tz=UTC)
        slippage = None
        realised = None
        if exit_price is not None:
            if trigger.level_price is not None:
                slippage = round(exit_price - trigger.level_price, 4)
            realised = round((exit_price - position.entry_price) * position.quantity, 2)
        try:
            self._journal.record_exit(
                exit_id=str(uuid.uuid4()),
                position_id=position.position_id,
                attempt=attempt_number,
                reason=trigger.reason,
                path=attempt.path,
                status=EXIT_FILLED if exit_price is not None else attempt.status,
                trace_id=position.trace_id,
                trading_day=position.trading_day,
                triggered_at_utc=triggered_at,
                submitted_at_utc=attempt.submitted_at_utc,
                latency_ms=latency_ms,
                round_trip_ms=attempt.round_trip_ms,
                symbol=position.symbol,
                exchange=position.exchange,
                quantity=position.quantity,
                level_price=trigger.level_price,
                observed_price=trigger.observed_price,
                feed_source=tick.source if tick else SOURCE_NONE,
                feed_age_ms=tick.age_ms(now) if tick else 0,
                broker_order_id=broker_order_id,
                exit_price=exit_price,
                slippage=slippage,
                realised_pnl=realised,
                detail=attempt.detail,
                raw_json=json.dumps(attempt.raw, default=str, sort_keys=True)[:8000],
            )
        except AlreadyJournalled:
            logger.info("exit attempt %d for %s was already journalled",
                        attempt_number, position.position_id)
        except JournalWriteError:
            logger.critical(
                "could not journal exit attempt %d for %s — the order was still sent",
                attempt_number, position.position_id,
            )

    def _finish(
        self, position: ManagedPosition, trigger: ExitTrigger, exit_price: float | None
    ) -> None:
        realised = (
            round((exit_price - position.entry_price) * position.quantity, 2)
            if exit_price is not None
            else None
        )
        self._write_state(
            position, POSITION_FLAT,
            detail=f"exited on {trigger.reason}",
            exit_reason=trigger.reason,
            realised_pnl=realised,
        )
        logger.info(
            "position %s is flat on %s (realised %s)",
            position.position_id, trigger.reason,
            "unknown" if realised is None else f"{realised:.2f}",
        )
        self._stand_down()

    def _escalate(self, position: ManagedPosition, trigger: ExitTrigger) -> None:
        """Every rung failed. Say so loudly, stop trading, and leave the backstop to work."""
        logger.critical(
            "EXIT FAILED after %d attempts: %d x %s is still open on a %s trigger — close it by "
            "hand now; OpenAlgo auto square-off remains the backstop",
            position.attempts, position.quantity, position.symbol, trigger.reason,
        )
        self._write_state(
            position, POSITION_ORPHANED,
            detail=f"exit failed after {position.attempts} attempts on {trigger.reason}",
            exit_reason=trigger.reason,
            defect=True,
        )
        engage_kill_switch(
            self._settings,
            f"exit failed for {position.symbol}: no new intents until it is resolved",
        )
        self._stand_down()

    # --- shared ------------------------------------------------------------

    def _write_state(
        self,
        position: ManagedPosition,
        state: str,
        *,
        detail: str,
        exit_reason: str | None = None,
        realised_pnl: float | None = None,
        defect: bool = False,
    ) -> None:
        try:
            self._journal.record_position_state(
                position_id=position.position_id,
                state=state,
                order_id=position.order_id,
                approval_id=position.approval_id,
                proposal_id=position.proposal_id,
                tick_id=position.tick_id,
                trace_id=position.trace_id,
                created_at_utc=datetime.now(tz=UTC),
                trading_day=position.trading_day,
                symbol=position.symbol,
                exchange=position.exchange,
                product=position.product,
                quantity=position.quantity,
                entry_price=position.entry_price,
                stop_price=position.levels.stop_price,
                target_price=position.levels.target_price,
                time_stop_utc=position.levels.time_stop_utc,
                theta_per_day=position.theta_per_day,
                exit_reason=exit_reason,
                realised_pnl=realised_pnl,
                detail=detail[:2000],
                defect=defect,
            )
        except AlreadyJournalled:
            logger.info("position %s was already at %s", position.position_id, state)
        except JournalWriteError:
            logger.critical(
                "could not journal position %s at state %s", position.position_id, state
            )

    def _close_feed(self) -> None:
        feed = self._feed
        if feed is not None:
            feed.close()
        self._feed = None

    def _stand_down(self) -> None:
        self._close_feed()
        with self._lock:
            self._position = None
        self._blackout_since = None

    def exit_path_ok(self) -> bool:
        """Used by the tick's plan gate. Reads the mirror; never raises."""
        return exit_path_status(self._mirror).ok
