"""The tick runner: single-flight, gated, budgeted, and fail-closed."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import UTC, datetime

from opentelemetry.trace import Status, StatusCode

from .config import IST
from .errors import JournalWriteError
from .graph import OUTCOME_DECLINE, REASON_INTERNAL_ERROR, TickDeps, build_tick_graph
from .journal import SCHEMA_VERSION
from .observability import get_tracer
from .session import OVERLAP, SessionGate

logger = logging.getLogger(__name__)


class TickRunner:
    """Runs exactly one decision tick per accepted trigger."""

    def __init__(self, deps: TickDeps, gate: SessionGate) -> None:
        self._deps = deps
        self._gate = gate
        self._graph = build_tick_graph(deps)
        self._lock = threading.Lock()
        self._tracer = get_tracer()

    def run_tick(self, trigger: str) -> str | None:
        """Return the tick id, or None when the trigger was skipped."""
        if not self._lock.acquire(blocking=False):
            self._skipped(trigger, OVERLAP, "a tick was already in flight")
            return None
        try:
            return self._run_locked(trigger)
        finally:
            self._lock.release()
            self._touch_heartbeat()

    def _skipped(self, trigger: str, blocked_by: str, detail: str) -> None:
        with self._tracer.start_as_current_span("strike_desk.tick") as root:
            root.set_attribute("strike_desk.trigger", trigger)
            root.set_attribute("strike_desk.index", self._deps.settings.index_symbol)
            root.set_attribute("tick.skipped", True)
            root.set_attribute("gate.allowed", False)
            root.set_attribute("gate.blocked_by", blocked_by)
            root.set_attribute("gate.detail", detail)
        logger.info("trigger %s skipped: %s (%s)", trigger, blocked_by, detail)

    def _touch_heartbeat(self) -> None:
        try:
            path = self._deps.settings.heartbeat_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(datetime.now(tz=UTC).isoformat(), encoding="utf-8")
        except OSError:
            logger.exception("could not update the heartbeat file")

    def _run_locked(self, trigger: str) -> str | None:
        settings = self._deps.settings
        tick_id = str(uuid.uuid4())
        started = time.monotonic()
        now_ist = datetime.now(tz=IST)
        trading_day = now_ist.date().isoformat()

        with self._tracer.start_as_current_span("strike_desk.tick") as root:
            trace_id = format(root.get_span_context().trace_id, "032x")
            root.set_attribute("strike_desk.tick_id", tick_id)
            root.set_attribute("strike_desk.trigger", trigger)
            root.set_attribute("strike_desk.index", settings.index_symbol)
            root.set_attribute("strike_desk.trading_day", trading_day)
            root.set_attribute("strike_desk.prompt_set_version", self._deps.prompts.set_version)

            verdict = self._gate.evaluate(now_ist)
            root.set_attribute("gate.allowed", verdict.allowed)
            if not verdict.allowed:
                root.set_attribute("tick.skipped", True)
                root.set_attribute("gate.blocked_by", verdict.blocked_by or "unknown")
                root.set_attribute("gate.detail", verdict.detail)
                logger.info("tick skipped: %s (%s)", verdict.blocked_by, verdict.detail)
                return None

            root.set_attribute("tick.skipped", False)
            state = {
                "tick_id": tick_id,
                "trace_id": trace_id,
                "trigger": trigger,
                "trading_day": trading_day,
                "started_monotonic": started,
                "deadline_monotonic": started + settings.tick_budget_seconds,
                "model_versions": [],
                # token_cost_micros is a usage counter (micros), not a credential
                "token_cost_micros": 0,  # nosec B105
            }
            config = {"configurable": {"thread_id": tick_id}, "recursion_limit": 12}

            try:
                final = self._graph.invoke(state, config)
            except JournalWriteError:
                root.set_status(Status(StatusCode.ERROR, "journal write failed"))
                logger.exception("tick %s failed closed — journal unwritable", tick_id)
                raise
            except Exception as exc:  # noqa: BLE001 — a crashed tick is still a decision
                root.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                logger.exception("tick %s raised unexpectedly", tick_id)
                self._journal_internal_error(tick_id, trace_id, trigger, trading_day, started, exc)
                return tick_id

            root.set_attribute("decision.outcome", final["outcome"])
            root.set_attribute("decision.reason_code", final["reason_code"])
            self._deps.span_processor.forget(trace_id)
            return tick_id

    def _journal_internal_error(
        self,
        tick_id: str,
        trace_id: str,
        trigger: str,
        trading_day: str,
        started: float,
        exc: BaseException,
    ) -> None:
        self._deps.journal.record_decision(
            tick_id=tick_id,
            trace_id=trace_id,
            created_at_utc=datetime.now(tz=UTC),
            trading_day=trading_day,
            index_symbol=self._deps.settings.index_symbol,
            trigger=trigger,
            outcome=OUTCOME_DECLINE,
            reason_code=REASON_INTERNAL_ERROR,
            reason_text=(
                f"Declined: the tick raised {type(exc).__name__} before assembling a decision. "
                "The desk stays out when it cannot reason."
            ),
            regime_label=None,
            regime_confidence=None,
            book_state_json=json.dumps(None),
            prompt_set_version=self._deps.prompts.set_version,
            model_version="none",
            token_cost_micros=0,
            latency_ms=int((time.monotonic() - started) * 1000),
            trace_complete=not self._deps.span_processor.had_failure(trace_id),
            schema_version=SCHEMA_VERSION,
        )
