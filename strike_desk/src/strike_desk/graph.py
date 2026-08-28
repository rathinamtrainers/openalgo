"""The supervisor decision tick as a LangGraph state graph."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from opentelemetry.trace import Status, StatusCode
from pydantic import ValidationError

from .book_state import read_book_state
from .config import IST, Settings
from .decline_taxonomy import describe, render
from .errors import BookStateUnavailable, SpecialistTimeout, SpecialistUnavailable
from .grounding import ProposalSubmission
from .journal import SCHEMA_VERSION, Journal
from .observability import JournalSpanProcessor, get_tracer
from .openalgo_client import OpenAlgoClient
from .options_strategist import (
    STATUS_DEGRADED as STRATEGY_DEGRADED,
)
from .options_strategist import (
    STATUS_INVALID as STRATEGY_INVALID,
)
from .options_strategist import (
    STATUS_NO_CONTRACT as STRATEGY_NO_CONTRACT,
)
from .options_strategist import (
    STATUS_UNGROUNDED as STRATEGY_UNGROUNDED,
)
from .options_strategist import (
    build_proposal_row,
)
from .playbook import Playbook
from .prompt_registry import PromptRegistry
from .regime_analyst import (
    STATUS_DEGRADED,
    STATUS_OK,
    STATUS_UNGROUNDED,
    build_regime_read_row,
)
from .risk_officer import (
    LIMIT_PROPOSAL_SHAPE,
    SIZEABLE_LIMITS,
    UNIT_RUPEES,
    VERDICT_HOLD,
    VERDICT_REDUCE,
    VERDICT_VETO,
    RiskLimits,
    SessionAssessment,
    assess_session,
    build_verdict_row,
    held,
)
from .risk_officer import (
    adjudicate as adjudicate_proposal,
)
from .specialists import (
    ROLE_REGIME,
    ROLE_RISK,
    ROLE_STRATEGIST,
    SpecialistRegistry,
    SpecialistRequest,
    SpecialistResult,
)

logger = logging.getLogger(__name__)

OUTCOME_ENTER = "enter"
OUTCOME_DECLINE = "decline"
OUTCOME_HOLD = "hold"

REASON_POSITION_OPEN = "position-open"
REASON_DATA_QUALITY = "data-quality"
REASON_SPECIALIST_UNAVAILABLE = "specialist-unavailable"
REASON_SPECIALIST_TIMEOUT = "specialist-timeout"
REASON_REGIME_NOT_TRADEABLE = "regime-not-tradeable"
REASON_REGIME_LOW_CONFIDENCE = "regime-low-confidence"
REASON_REGIME_UNGROUNDED = "regime-ungrounded"
REASON_TICK_TIMEOUT = "tick-timeout"
REASON_INTERNAL_ERROR = "internal-error"
REASON_NO_VIABLE_CONTRACT = "no-viable-contract"
REASON_PROPOSAL_UNGROUNDED = "proposal-ungrounded"
REASON_PROPOSAL_INVALID = "proposal-invalid"
REASON_RISK_SESSION_STOPPED = "risk-session-stopped"
REASON_RISK_INPUT_UNAVAILABLE = "risk-input-unavailable"
REASON_RISK_VETO = "risk-veto"
REASON_RISK_CLEARED = "risk-cleared"

TRADEABLE_REGIMES = frozenset({"trending", "range-bound"})


class TickState(TypedDict, total=False):
    tick_id: str
    trace_id: str
    trigger: str
    trading_day: str
    started_monotonic: float
    deadline_monotonic: float
    book: dict[str, Any] | None
    book_error: str | None
    budget_exceeded: bool
    regime_label: str | None
    regime_confidence: float | None
    regime_rationale: str | None
    regime_data_error: str | None
    specialist_error: dict[str, str] | None
    model_versions: list[str]
    token_cost_micros: int
    outcome: str
    reason_code: str
    reason_text: str
    proposal: dict[str, Any] | None
    proposal_status: str | None
    proposal_detail: str | None
    proposal_violations: list[str] | None
    proposal_id: str | None
    session_stop: dict[str, Any] | None
    risk: dict[str, Any] | None


@dataclass
class TickDeps:
    settings: Settings
    client: OpenAlgoClient
    journal: Journal
    registry: SpecialistRegistry
    prompts: PromptRegistry
    span_processor: JournalSpanProcessor
    checkpointer: Any


def _rationale(state: TickState) -> str | None:
    """The analyst's own sentence, when this tick had one."""
    text = (state.get("regime_rationale") or "").strip()
    return text or None


def _decide_outcome(state: TickState, settings: Settings) -> tuple[str, str, str]:
    """The complete decision table. Returns (outcome, reason_code, reason_text).

    Every branch names its reason code and lets the taxonomy write the sentence, so the
    wording of a verdict lives in exactly one place and is pinned by a golden test.
    """
    cap = settings.reason_text_max_chars

    if state.get("budget_exceeded"):
        return (
            OUTCOME_DECLINE,
            REASON_TICK_TIMEOUT,
            render(
                REASON_TICK_TIMEOUT,
                max_chars=cap,
                budget=f"{settings.tick_budget_seconds:.0f}",
            ),
        )

    book_error = state.get("book_error")
    if book_error:
        return (
            OUTCOME_DECLINE,
            REASON_DATA_QUALITY,
            render(REASON_DATA_QUALITY, max_chars=cap, variant="book", detail=book_error),
        )

    book = state.get("book") or {}
    positions = book.get("open_positions") or []
    if positions:
        return (
            OUTCOME_HOLD,
            REASON_POSITION_OPEN,
            render(
                REASON_POSITION_OPEN,
                max_chars=cap,
                count=len(positions),
                index=settings.index_symbol,
                symbols=", ".join(str(position.get("symbol", "?")) for position in positions),
            ),
        )

    stop = state.get("session_stop")
    if stop:
        return (
            OUTCOME_DECLINE,
            REASON_RISK_SESSION_STOPPED,
            render(
                REASON_RISK_SESSION_STOPPED,
                max_chars=cap,
                configured=f"Rs {float(stop.get('configured') or 0.0):,.0f}",
                observed=f"Rs {float(stop.get('observed') or 0.0):,.0f}",
            ),
        )

    regime_data_error = state.get("regime_data_error")
    if regime_data_error:
        return (
            OUTCOME_DECLINE,
            REASON_DATA_QUALITY,
            render(REASON_DATA_QUALITY, max_chars=cap, variant="regime", detail=regime_data_error),
        )

    error = state.get("specialist_error")
    if error:
        role = error.get("role", "?")
        detail = error.get("detail", "")
        kind = error.get("kind")
        if kind == "timeout":
            return (
                OUTCOME_DECLINE,
                REASON_SPECIALIST_TIMEOUT,
                render(REASON_SPECIALIST_TIMEOUT, max_chars=cap, role=role, detail=detail),
            )
        if kind == "ungrounded":
            return (
                OUTCOME_DECLINE,
                REASON_REGIME_UNGROUNDED,
                render(REASON_REGIME_UNGROUNDED, max_chars=cap, detail=detail),
            )
        return (
            OUTCOME_DECLINE,
            REASON_SPECIALIST_UNAVAILABLE,
            render(REASON_SPECIALIST_UNAVAILABLE, max_chars=cap, role=role, detail=detail),
        )

    if state.get("regime_label") is None:
        return (
            OUTCOME_DECLINE,
            REASON_SPECIALIST_UNAVAILABLE,
            render(
                REASON_SPECIALIST_UNAVAILABLE,
                max_chars=cap,
                role=ROLE_REGIME,
                detail="no specialist registered for this role",
            ),
        )

    label = state.get("regime_label") or "unknown"
    confidence = state.get("regime_confidence") or 0.0
    if label not in TRADEABLE_REGIMES:
        return (
            OUTCOME_DECLINE,
            REASON_REGIME_NOT_TRADEABLE,
            render(
                REASON_REGIME_NOT_TRADEABLE,
                max_chars=cap,
                rationale=_rationale(state),
                label=label,
            ),
        )
    if confidence < settings.min_regime_confidence:
        return (
            OUTCOME_DECLINE,
            REASON_REGIME_LOW_CONFIDENCE,
            render(
                REASON_REGIME_LOW_CONFIDENCE,
                max_chars=cap,
                rationale=_rationale(state),
                label=label,
                confidence=f"{confidence:.2f}",
                floor=f"{settings.min_regime_confidence:.2f}",
            ),
        )

    if label not in settings.directional_regime_set:
        return (
            OUTCOME_DECLINE,
            REASON_NO_VIABLE_CONTRACT,
            render(
                REASON_NO_VIABLE_CONTRACT,
                max_chars=cap,
                variant="non_directional",
                rationale=_rationale(state),
                label=label,
            ),
        )

    status = state.get("proposal_status")
    if status is None:
        return (
            OUTCOME_DECLINE,
            REASON_SPECIALIST_UNAVAILABLE,
            render(
                REASON_SPECIALIST_UNAVAILABLE,
                max_chars=cap,
                variant="no_strategist",
                rationale=_rationale(state),
                label=label,
                confidence=f"{confidence:.2f}",
                role=ROLE_STRATEGIST,
            ),
        )

    proposal = state.get("proposal") or {}
    detail = state.get("proposal_detail") or ""
    if status == STRATEGY_UNGROUNDED:
        return (
            OUTCOME_DECLINE,
            REASON_PROPOSAL_UNGROUNDED,
            render(REASON_PROPOSAL_UNGROUNDED, max_chars=cap, detail=detail),
        )
    if status == STRATEGY_INVALID:
        violations = state.get("proposal_violations") or []
        return (
            OUTCOME_DECLINE,
            REASON_PROPOSAL_INVALID,
            render(
                REASON_PROPOSAL_INVALID,
                max_chars=cap,
                symbol=str(proposal.get("symbol", "the contract")),
                count=len(violations),
                detail="; ".join(violations[:2]),
            ),
        )
    if status == STRATEGY_DEGRADED:
        return (
            OUTCOME_DECLINE,
            REASON_DATA_QUALITY,
            render(REASON_DATA_QUALITY, max_chars=cap, variant="chain", detail=detail),
        )
    if status == STRATEGY_NO_CONTRACT:
        return (
            OUTCOME_DECLINE,
            REASON_NO_VIABLE_CONTRACT,
            render(
                REASON_NO_VIABLE_CONTRACT,
                max_chars=cap,
                index=settings.index_symbol,
                expiry=str(proposal.get("expiry", "current")),
                detail=detail,
            ),
        )

    # A proposal that passed every check. There is still no risk officer to adjudicate it,
    # and a proposal alone is never an entry — AC-11.
    risk = state.get("risk")
    if risk is None:
        return (
            OUTCOME_DECLINE,
            REASON_SPECIALIST_UNAVAILABLE,
            render(
                REASON_SPECIALIST_UNAVAILABLE,
                max_chars=cap,
                variant="no_risk",
                symbol=str(proposal.get("symbol", "a contract")),
                entry=f"{float(proposal.get('entry_price_high') or 0.0):.2f}",
                role=ROLE_RISK,
            ),
        )

    tripped = risk.get("tripped") or {}
    limit = str(tripped.get("limit", "a limit"))
    configured = _money(tripped.get("configured"), tripped.get("unit"))
    observed = _money(tripped.get("observed"), tripped.get("unit"))
    symbol = str(proposal.get("symbol", "the contract"))

    if risk["verdict"] == VERDICT_HOLD:
        return (
            OUTCOME_DECLINE,
            REASON_RISK_INPUT_UNAVAILABLE,
            render(
                REASON_RISK_INPUT_UNAVAILABLE,
                max_chars=cap,
                limit=limit,
                detail=str(risk.get("detail", "")),
            ),
        )
    if risk["verdict"] == VERDICT_VETO:
        variant = "default"
        if risk.get("lots_requested") and risk.get("lots_cleared") == 0:
            variant = "sized_out" if limit in SIZEABLE_LIMITS else "default"
        if "no longer passes the playbook" in str(risk.get("detail", "")):
            variant = "playbook_disagreed"
        return (
            OUTCOME_DECLINE,
            REASON_RISK_VETO,
            render(
                REASON_RISK_VETO,
                max_chars=cap,
                variant=variant,
                symbol=symbol,
                limit=limit,
                configured=configured,
                observed=observed,
                lots=risk.get("lots_cleared", 0),
                detail=str(risk.get("detail", "")),
            ),
        )

    reduced = risk["verdict"] == VERDICT_REDUCE
    return (
        OUTCOME_ENTER,
        REASON_RISK_CLEARED,
        render(
            REASON_RISK_CLEARED,
            max_chars=cap,
            variant="reduced" if reduced else "default",
            symbol=symbol,
            lots=risk.get("lots_cleared", 0),
            requested=risk.get("lots_requested", 0),
            entry=f"{float(proposal.get('entry_price_high') or 0.0):.2f}",
            stop=f"{float(proposal.get('stop_price') or 0.0):.2f}",
            risk=f"Rs {float(risk.get('max_loss_at_stop') or 0.0):,.0f}",
            base=f"Rs {float(risk.get('capital_base') or 0.0):,.0f}",
            limit=limit,
            configured=configured,
        ),
    )


def _record_read(deps: TickDeps, state: TickState, result: SpecialistResult) -> dict[str, Any]:
    """Append the read, then translate its status into tick state."""
    payload = dict(result.payload)
    deps.journal.record_regime_read(
        **build_regime_read_row(
            payload,
            settings=deps.settings,
            prompts=deps.prompts,
            tick_id=state["tick_id"],
            trace_id=state["trace_id"],
            trading_day=state["trading_day"],
            source="tick",
        )
    )

    spent: dict[str, Any] = {
        "model_versions": [result.model_version] if result.model_version else [],
        "token_cost_micros": int(result.token_cost_micros),
    }
    status = str(payload.get("status", STATUS_DEGRADED))

    if status == STATUS_OK:
        try:
            label = str(payload["label"]).strip().lower()
            confidence = float(payload["confidence"])
        except (KeyError, TypeError, ValueError):
            # A specialist that answers with junk is unavailable, not low-confidence.
            return {
                **spent,
                "specialist_error": {
                    "role": ROLE_REGIME,
                    "kind": "unavailable",
                    "detail": "payload lacked a usable label/confidence",
                },
            }
        return {
            **spent,
            "regime_label": label,
            "regime_confidence": confidence,
            "regime_rationale": str(payload.get("rationale", "")),
        }
    if status == STATUS_UNGROUNDED:
        return {
            **spent,
            "specialist_error": {
                "role": ROLE_REGIME,
                "kind": "ungrounded",
                "detail": str(payload.get("defect", "ungrounded submission")),
            },
        }
    return {**spent, "regime_data_error": str(payload.get("defect", "the read was degraded"))}


def _wants_a_contract(state: TickState, settings: Settings) -> bool:
    """AC-6: the strategist is reached only from a clean, tradeable, directional read."""
    if state.get("budget_exceeded") or state.get("book_error"):
        return False
    if state.get("regime_data_error") or state.get("specialist_error"):
        return False
    label = state.get("regime_label")
    confidence = state.get("regime_confidence") or 0.0
    if label is None or label not in TRADEABLE_REGIMES:
        return False
    if confidence < settings.min_regime_confidence:
        return False
    return label in settings.directional_regime_set


def _record_proposal(deps: TickDeps, state: TickState, result: SpecialistResult) -> dict[str, Any]:
    """Append the proposal, then translate its status into tick state."""
    payload = dict(result.payload)
    proposal_id = deps.journal.record_proposal(
        **build_proposal_row(
            payload,
            settings=deps.settings,
            prompts=deps.prompts,
            tick_id=state["tick_id"],
            trace_id=state["trace_id"],
            trading_day=state["trading_day"],
            source="tick",
            regime_label=state.get("regime_label"),
            regime_confidence=state.get("regime_confidence"),
        )
    )
    return {
        "model_versions": [
            *(state.get("model_versions") or []),
            *([result.model_version] if result.model_version else []),
        ],
        "token_cost_micros": int(state.get("token_cost_micros") or 0)
        + int(result.token_cost_micros),
        "proposal": payload.get("proposal"),
        "proposal_status": str(payload.get("status", STRATEGY_DEGRADED)),
        "proposal_detail": str(payload.get("defect") or payload.get("rationale") or ""),
        "proposal_violations": list(payload.get("violations") or []),
        "proposal_id": proposal_id,
    }


def _latch_session_stop(
    deps: TickDeps, state: TickState, assessment: SessionAssessment, limits: RiskLimits
) -> None:
    """Write exactly one session-stop verdict for the day. The latch is a row, not a flag."""
    verdict = held(
        assessment.check.limit,
        assessment.detail,
        base=assessment.capital_base,
    )
    row = build_verdict_row(
        verdict,
        limits=limits,
        tick_id=state["tick_id"],
        trace_id=state["trace_id"],
        trading_day=state["trading_day"],
        index_symbol=deps.settings.index_symbol,
        proposal_id=None,
        symbol=None,
        latency_us=0,
    )
    row.update(
        {
            "created_at_utc": datetime.now(tz=UTC),
            "verdict": VERDICT_VETO,
            "session_stop": True,
            "tripped_limit": assessment.check.limit,
            "configured_value": assessment.check.configured,
            "observed_value": assessment.check.observed,
            "limit_unit": assessment.check.unit,
            "checks_json": json.dumps([assessment.check.as_dict()], sort_keys=True),
        }
    )
    deps.journal.record_risk_verdict(**row)


def _money(value: Any, unit: Any) -> str:
    """Format a limit's value the way its unit wants to be read."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "unspecified"
    return f"Rs {number:,.0f}" if unit == UNIT_RUPEES else f"{number:g}"


def build_tick_graph(deps: TickDeps) -> Any:
    """Compile the supervisor tick graph. One compiled graph per process."""
    tracer = get_tracer()

    def _over_budget(state: TickState) -> bool:
        return time.monotonic() >= state["deadline_monotonic"]

    def plan(state: TickState) -> dict[str, Any]:
        with tracer.start_as_current_span("tick.plan") as span:
            span.set_attribute("strike_desk.tick_id", state["tick_id"])
            if _over_budget(state):
                span.set_attribute("tick.budget_exceeded", True)
                return {"budget_exceeded": True}
            try:
                book = read_book_state(
                    deps.client,
                    deps.journal,
                    deps.settings,
                    state["trading_day"],
                    datetime.now(tz=UTC),
                )
            except BookStateUnavailable as exc:
                span.set_attribute("book.error", str(exc))
                span.set_status(Status(StatusCode.ERROR, "book state unavailable"))
                return {"book": None, "book_error": str(exc)}
            span.set_attribute("book.flat", book.flat)
            span.set_attribute("book.open_positions", len(book.open_positions))
            span.set_attribute("book.decisions_today", book.decisions_today)
            span.set_attribute("book.available_cash", book.available_cash)
            snapshot = book.as_dict()
            limits = RiskLimits.from_settings(deps.settings)
            with tracer.start_as_current_span("risk.session") as risk_span:
                assessment = assess_session(snapshot, limits)
                risk_span.set_attribute("risk.limits_artifact", limits.artifact)
                risk_span.set_attribute("risk.capital_base", assessment.capital_base)
                risk_span.set_attribute("risk.daily_loss_configured", assessment.check.configured)
                risk_span.set_attribute("risk.daily_loss_observed", assessment.check.observed)
                latched = deps.journal.session_stopped(state["trading_day"])
                risk_span.set_attribute("risk.session_latched", latched)
                risk_span.set_attribute("risk.session_stopped", assessment.stopped or latched)
                if not (assessment.stopped or latched):
                    return {"book": snapshot}
                if assessment.stopped and not latched:
                    _latch_session_stop(deps, state, assessment, limits)
            return {
                "book": snapshot,
                "session_stop": {
                    "configured": assessment.check.configured,
                    "observed": assessment.check.observed,
                    "detail": assessment.detail,
                },
            }

    def route_after_plan(state: TickState) -> str:
        if state.get("budget_exceeded") or state.get("book_error"):
            return "decide"
        book = state.get("book") or {}
        if book.get("open_positions"):
            return "decide"
        return "decide" if state.get("session_stop") else "consult"

    def consult(state: TickState) -> dict[str, Any]:
        with tracer.start_as_current_span("tick.consult") as span:
            span.set_attribute("specialist.role", ROLE_REGIME)
            roles = ",".join(deps.registry.registered_roles())
            span.set_attribute("specialist.registered_roles", roles)
            if _over_budget(state):
                span.set_attribute("tick.budget_exceeded", True)
                return {"budget_exceeded": True}

            request = SpecialistRequest(
                tick_id=state["tick_id"],
                index_symbol=deps.settings.index_symbol,
                as_of=datetime.now(tz=UTC),
                book=state.get("book") or {},
            )
            try:
                result = deps.registry.consult(
                    ROLE_REGIME, request, deps.settings.specialist_timeout_seconds
                )
            except SpecialistTimeout as exc:
                span.set_attribute("specialist.outcome", "timeout")
                span.set_status(Status(StatusCode.ERROR, "specialist timeout"))
                return {
                    "specialist_error": {
                        "role": exc.role,
                        "kind": "timeout",
                        "detail": exc.detail,
                    }
                }
            except SpecialistUnavailable as exc:
                span.set_attribute("specialist.outcome", "unavailable")
                return {
                    "specialist_error": {
                        "role": exc.role,
                        "kind": "unavailable",
                        "detail": exc.detail,
                    }
                }

            update = _record_read(deps, state, result)
            span.set_attribute("specialist.outcome", str(result.payload.get("status", "unknown")))
            span.set_attribute("regime.label", str(result.payload.get("label") or "none"))
            span.set_attribute("regime.read_id", str(result.payload.get("read_id", "")))
            span.set_attribute("regime.token_cost_micros", int(result.token_cost_micros))
            return update

    def route_after_consult(state: TickState) -> str:
        return "propose" if _wants_a_contract(state, deps.settings) else "decide"

    def propose(state: TickState) -> dict[str, Any]:
        with tracer.start_as_current_span("tick.propose") as span:
            span.set_attribute("specialist.role", ROLE_STRATEGIST)
            if _over_budget(state):
                span.set_attribute("tick.budget_exceeded", True)
                return {"budget_exceeded": True}

            request = SpecialistRequest(
                tick_id=state["tick_id"],
                index_symbol=deps.settings.index_symbol,
                as_of=datetime.now(tz=UTC),
                book={
                    **(state.get("book") or {}),
                    "regime": {
                        "label": state.get("regime_label"),
                        "confidence": state.get("regime_confidence"),
                        "rationale": state.get("regime_rationale"),
                    },
                },
            )
            try:
                result = deps.registry.consult(
                    ROLE_STRATEGIST, request, deps.settings.strategist_timeout_seconds
                )
            except SpecialistTimeout as exc:
                span.set_attribute("specialist.outcome", "timeout")
                span.set_status(Status(StatusCode.ERROR, "specialist timeout"))
                return {
                    "specialist_error": {
                        "role": exc.role,
                        "kind": "timeout",
                        "detail": exc.detail,
                    }
                }
            except SpecialistUnavailable as exc:
                span.set_attribute("specialist.outcome", "unavailable")
                return {
                    "specialist_error": {
                        "role": exc.role,
                        "kind": "unavailable",
                        "detail": exc.detail,
                    }
                }

            update = _record_proposal(deps, state, result)
            span.set_attribute("specialist.outcome", str(result.payload.get("status", "unknown")))
            span.set_attribute(
                "strategy.symbol",
                str((result.payload.get("proposal") or {}).get("symbol") or "none"),
            )
            span.set_attribute(
                "strategy.playbook_verdict", str(result.payload.get("playbook_verdict"))
            )
            span.set_attribute("strategy.token_cost_micros", int(result.token_cost_micros))
            return update

    def route_after_propose(state: TickState) -> str:
        return "adjudicate" if state.get("proposal_status") == "proposed" else "decide"

    def adjudicate(state: TickState) -> dict[str, Any]:
        with tracer.start_as_current_span("tick.adjudicate") as span:
            limits = RiskLimits.from_settings(deps.settings)
            span.set_attribute("risk.limits_artifact", limits.artifact)
            payload = state.get("proposal") or {}
            started = time.perf_counter()
            try:
                proposal = ProposalSubmission.model_validate(payload)
            except ValidationError as exc:
                verdict = held(
                    LIMIT_PROPOSAL_SHAPE,
                    f"the proposal could not be read as a submission: {exc.error_count()} field(s)",
                )
            else:
                verdict = adjudicate_proposal(
                    proposal,
                    state.get("book") or {},
                    limits,
                    Playbook.from_settings(deps.settings),
                    now_ist=datetime.now(tz=IST),
                    entries_today=deps.journal.count_entries(state["trading_day"]),
                )
            latency_us = int((time.perf_counter() - started) * 1_000_000)

            row = build_verdict_row(
                verdict,
                limits=limits,
                tick_id=state["tick_id"],
                trace_id=state["trace_id"],
                trading_day=state["trading_day"],
                index_symbol=deps.settings.index_symbol,
                proposal_id=state.get("proposal_id"),
                symbol=str(payload.get("symbol") or "") or None,
                latency_us=latency_us,
            )
            row["created_at_utc"] = datetime.now(tz=UTC)
            deps.journal.record_risk_verdict(**row)

            span.set_attribute("risk.verdict", verdict.verdict)
            span.set_attribute("risk.limit", verdict.tripped.limit if verdict.tripped else "none")
            span.set_attribute(
                "risk.configured", verdict.tripped.configured if verdict.tripped else 0.0
            )
            span.set_attribute(
                "risk.observed", verdict.tripped.observed if verdict.tripped else 0.0
            )
            span.set_attribute("risk.capital_base", verdict.capital_base)
            span.set_attribute("risk.lots_requested", verdict.lots_requested)
            span.set_attribute("risk.lots_cleared", verdict.lots_cleared)
            span.set_attribute("risk.latency_us", latency_us)
            if verdict.verdict == VERDICT_HOLD:
                span.set_status(Status(StatusCode.ERROR, "risk input unavailable"))
            return {"risk": verdict.as_dict()}

    def decide(state: TickState) -> dict[str, Any]:
        with tracer.start_as_current_span("tick.decide") as span:
            if _over_budget(state) and not state.get("budget_exceeded"):
                state = {**state, "budget_exceeded": True}
            outcome, reason_code, reason_text = _decide_outcome(state, deps.settings)
            entry = describe(reason_code)
            span.set_attribute("decision.outcome", outcome)
            span.set_attribute("decision.reason_code", reason_code)
            span.set_attribute("decision.reason_category", entry.category)
            span.set_attribute("decision.reason_disposition", entry.disposition)
            return {
                "outcome": outcome,
                "reason_code": reason_code,
                "reason_text": reason_text,
                "budget_exceeded": bool(state.get("budget_exceeded")),
            }

    def persist(state: TickState) -> dict[str, Any]:
        with tracer.start_as_current_span("tick.persist") as span:
            latency_ms = int((time.monotonic() - state["started_monotonic"]) * 1000)
            trace_complete = not deps.span_processor.had_failure(state["trace_id"])
            models = [version for version in (state.get("model_versions") or []) if version]
            entry = describe(state["reason_code"])
            deps.journal.record_decision(
                tick_id=state["tick_id"],
                trace_id=state["trace_id"],
                created_at_utc=datetime.now(tz=UTC),
                trading_day=state["trading_day"],
                index_symbol=deps.settings.index_symbol,
                trigger=state["trigger"],
                outcome=state["outcome"],
                reason_code=state["reason_code"],
                reason_text=state["reason_text"],
                reason_category=entry.category,
                reason_disposition=entry.disposition,
                regime_label=state.get("regime_label"),
                regime_confidence=state.get("regime_confidence"),
                book_state_json=json.dumps(state.get("book"), default=str, sort_keys=True),
                prompt_set_version=deps.prompts.set_version,
                model_version=",".join(models) if models else "none",
                token_cost_micros=int(state.get("token_cost_micros") or 0),
                latency_ms=latency_ms,
                trace_complete=trace_complete,
                schema_version=SCHEMA_VERSION,
            )
            span.set_attribute("journal.latency_ms", latency_ms)
            span.set_attribute("journal.trace_complete", trace_complete)
            logger.info(
                "tick %s -> %s/%s (%s/%s) in %dms",
                state["tick_id"],
                state["outcome"],
                state["reason_code"],
                entry.category,
                entry.disposition,
                latency_ms,
            )
            return {}

    builder = StateGraph(TickState)
    builder.add_node("plan", plan)
    builder.add_node("consult", consult)
    builder.add_node("propose", propose)
    builder.add_node("adjudicate", adjudicate)
    builder.add_node("decide", decide)
    builder.add_node("persist", persist)
    builder.add_edge(START, "plan")
    builder.add_conditional_edges(
        "plan", route_after_plan, {"consult": "consult", "decide": "decide"}
    )
    builder.add_conditional_edges(
        "consult", route_after_consult, {"propose": "propose", "decide": "decide"}
    )
    builder.add_conditional_edges(
        "propose", route_after_propose, {"adjudicate": "adjudicate", "decide": "decide"}
    )
    builder.add_edge("adjudicate", "decide")
    builder.add_edge("decide", "persist")
    builder.add_edge("persist", END)
    return builder.compile(checkpointer=deps.checkpointer)
