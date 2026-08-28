"""The Regime Analyst: the desk's first agent, and the first model call in the product."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from opentelemetry.trace import Status, StatusCode
from pydantic import ValidationError

from .config import IST, Settings
from .errors import (
    EventCalendarInvalid,
    McpUnavailable,
    ModelCallFailed,
    SpecialistTimeout,
)
from .events import active_window, load_event_windows
from .grounding import (
    EvidenceLedger,
    RegimeSubmission,
    ToolObservation,
    validate_submission,
)
from .journal import SCHEMA_VERSION
from .mcp_toolbox import ToolSource
from .model_client import build_regime_model, cost_micros, token_usage
from .observability import get_tracer
from .prompt_registry import PromptRegistry
from .specialists import ROLE_REGIME, SpecialistRequest, SpecialistResult

logger = logging.getLogger(__name__)

PROMPT_NAME = "regime_analyst"
SUBMIT_TOOL_NAME = "submit_regime_read"

STATUS_OK = "ok"
STATUS_UNGROUNDED = "ungrounded"
STATUS_DEGRADED = "degraded"


def submit_tool() -> StructuredTool:
    """The schema the agent must fill. The loop interprets it; it is never executed."""

    def _intercepted(**_kwargs: Any) -> str:  # pragma: no cover - unreachable by design
        raise RuntimeError("submit_regime_read is interpreted by the agent loop")

    return StructuredTool.from_function(
        func=_intercepted,
        name=SUBMIT_TOOL_NAME,
        args_schema=RegimeSubmission,
        description="Submit the final regime classification. Call this exactly once, last.",
    )


@dataclass
class ReadOutcome:
    """Everything one read produced, whatever way it ended."""

    status: str
    label: str | None = None
    confidence: float | None = None
    rationale: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    defect: str | None = None
    tool_calls: int = 0
    tool_errors: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class RegimeAnalyst:
    """Reads the regime through OpenAlgo's tools, and cites what it read."""

    role = ROLE_REGIME

    def __init__(
        self,
        settings: Settings,
        prompts: PromptRegistry,
        tool_source: ToolSource,
        model: BaseChatModel,
    ) -> None:
        self._settings = settings
        self._prompts = prompts
        self._artifact = prompts.get(PROMPT_NAME)
        self._tool_source = tool_source
        self._model = model
        self._tracer = get_tracer()

    # -- entry point --------------------------------------------------------

    def run(self, request: SpecialistRequest) -> SpecialistResult:
        """Called by the registry, on a worker thread, under the registry's timeout."""
        started = time.monotonic()
        read_id = str(uuid.uuid4())
        with self._tracer.start_as_current_span("regime.read") as span:
            span.set_attribute("regime.read_id", read_id)
            span.set_attribute("strike_desk.tick_id", request.tick_id)
            span.set_attribute("strike_desk.index", request.index_symbol)
            span.set_attribute("prompt.name", self._artifact.name)
            span.set_attribute("prompt.version", self._artifact.version)
            span.set_attribute("model.id", self._settings.regime_model)

            outcome = self._read(request)
            latency_ms = int((time.monotonic() - started) * 1000)
            cost = cost_micros(self._settings, outcome.input_tokens, outcome.output_tokens)

            span.set_attribute("regime.status", outcome.status)
            span.set_attribute("regime.label", outcome.label or "none")
            span.set_attribute("regime.confidence", outcome.confidence or 0.0)
            span.set_attribute("regime.tool_calls", outcome.tool_calls)
            span.set_attribute("regime.tool_errors", outcome.tool_errors)
            span.set_attribute("regime.model_calls", outcome.model_calls)
            span.set_attribute("regime.token_cost_micros", cost)
            span.set_attribute("regime.latency_ms", latency_ms)
            if outcome.defect:
                span.set_attribute("regime.defect", outcome.defect)
                span.set_status(Status(StatusCode.ERROR, outcome.status))

            logger.info(
                "regime read %s -> %s/%s in %dms (%d tool calls, %d micros)",
                read_id,
                outcome.status,
                outcome.label or "-",
                latency_ms,
                outcome.tool_calls,
                cost,
            )
            return SpecialistResult(
                role=ROLE_REGIME,
                payload={
                    "read_id": read_id,
                    "status": outcome.status,
                    "label": outcome.label,
                    "confidence": outcome.confidence,
                    "rationale": outcome.rationale,
                    "evidence": outcome.evidence,
                    "calls": outcome.calls,
                    "defect": outcome.defect,
                    "tool_call_count": outcome.tool_calls,
                    "tool_error_count": outcome.tool_errors,
                    "model_calls": outcome.model_calls,
                    "input_tokens": outcome.input_tokens,
                    "output_tokens": outcome.output_tokens,
                    "latency_ms": latency_ms,
                    "prompt_name": self._artifact.name,
                    "prompt_version": self._artifact.version,
                    "prompt_digest": self._artifact.digest,
                },
                model_version=self._settings.regime_model if outcome.model_calls else None,
                prompt_version=self._artifact.version,
                token_cost_micros=cost,
            )

    def _read(self, request: SpecialistRequest) -> ReadOutcome:
        now_ist = datetime.now(tz=IST)
        try:
            windows = load_event_windows(self._settings.events_path)
        except EventCalendarInvalid as exc:
            return ReadOutcome(
                status=STATUS_DEGRADED,
                label="unknown",
                rationale="The event calendar could not be read, so the session state is unknown.",
                defect=str(exc),
            )

        window = active_window(windows, now_ist)
        if window is not None:
            ledger = EvidenceLedger()
            ledger.record_calendar(window.name, window.as_evidence()["value"])
            return ReadOutcome(
                status=STATUS_OK,
                label="event-driven",
                confidence=1.0,
                rationale=(
                    f"The configured event window {window.name!r} is active, "
                    "so this session is event-driven and the playbook stands aside."
                ),
                evidence=[window.as_evidence()],
            )

        self._tool_source.ensure_started()
        try:
            return self._tool_source.submit(
                lambda: self._agent(request, now_ist),
                timeout=self._settings.analyst_deadline_seconds,
            )
        except TimeoutError as exc:
            raise SpecialistTimeout(ROLE_REGIME, str(exc)) from exc

    # -- the loop -----------------------------------------------------------

    async def _agent(self, request: SpecialistRequest, now_ist: datetime) -> ReadOutcome:
        tools = self._tool_source.tools(ROLE_REGIME)
        by_name = {tool.name: tool for tool in tools}
        submit = submit_tool()
        ledger = EvidenceLedger()
        messages: list[Any] = [
            SystemMessage(content=self._artifact.content),
            HumanMessage(content=self._context(request, now_ist)),
        ]
        model_calls = input_tokens = output_tokens = 0
        submission_args: dict[str, Any] | None = None

        for round_index in range(1, self._settings.regime_max_rounds + 1):
            final = round_index == self._settings.regime_max_rounds
            bound = self._model.bind_tools(
                [submit] if final else [*tools, submit],
                tool_choice=({"type": "tool", "name": SUBMIT_TOOL_NAME} if final else "auto"),
            )
            with self._tracer.start_as_current_span("regime.model_call") as span:
                span.set_attribute("model.id", self._settings.regime_model)
                span.set_attribute("model.round", round_index)
                span.set_attribute("model.forced_submission", final)
                try:
                    answer = await bound.ainvoke(messages)
                except Exception as exc:  # noqa: BLE001 — one taxonomy for provider failures
                    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                    raise ModelCallFailed(f"{type(exc).__name__}: {exc}") from exc
                round_in, round_out = token_usage(answer)
                model_calls += 1
                input_tokens += round_in
                output_tokens += round_out
                span.set_attribute("model.input_tokens", round_in)
                span.set_attribute("model.output_tokens", round_out)
                span.set_attribute(
                    "model.stop_reason", str(answer.response_metadata.get("stop_reason", ""))
                )

            messages.append(answer)
            calls = list(getattr(answer, "tool_calls", []) or [])
            submission_args = next(
                (call["args"] for call in calls if call["name"] == SUBMIT_TOOL_NAME), None
            )
            if submission_args is not None:
                break

            if not calls:
                messages.append(
                    HumanMessage(
                        content=(
                            "Call a market tool, or call submit_regime_read with your answer. "
                            "Do not reply in prose."
                        )
                    )
                )
                continue

            for call in calls:
                await self._execute(call, by_name, ledger, messages)

        return self._finish(submission_args, ledger, model_calls, input_tokens, output_tokens)

    async def _execute(
        self,
        call: dict[str, Any],
        by_name: dict[str, BaseTool],
        ledger: EvidenceLedger,
        messages: list[Any],
    ) -> None:
        """Run one tool call, record it, and answer the model with its output."""
        name = str(call.get("name", ""))
        call_id = str(call.get("id", uuid.uuid4()))
        args = dict(call.get("args") or {})
        started = time.monotonic()

        with self._tracer.start_as_current_span("regime.tool_call") as span:
            span.set_attribute("tool.name", name)
            span.set_attribute("tool.args", json.dumps(args, default=str, sort_keys=True))
            tool = by_name.get(name)
            if tool is None:
                # Structural guardrail: nothing outside the whitelist is reachable, and an
                # attempt to reach past it is recorded rather than silently dropped.
                span.set_attribute("tool.rejected", True)
                span.set_status(Status(StatusCode.ERROR, "tool not in whitelist"))
                output, ok = f"Error: {name!r} is not an available tool.", False
            else:
                try:
                    output, ok = str(await tool.ainvoke(args)), True
                except Exception as exc:  # noqa: BLE001 — a bad call is data, not a crash
                    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                    output, ok = f"Error calling {name}: {type(exc).__name__}: {exc}", False
                if ok and output.lstrip().lower().startswith("error"):
                    # OpenAlgo's MCP tools report failure as an error string, not an exception.
                    ok = False

            output, truncated = self._truncate(output)
            latency_ms = int((time.monotonic() - started) * 1000)
            span.set_attribute("tool.ok", ok)
            span.set_attribute("tool.truncated", truncated)
            span.set_attribute("tool.output_chars", len(output))
            span.set_attribute("tool.latency_ms", latency_ms)
            span.set_attribute("tool.output", output)

        ledger.record(
            ToolObservation(
                call_id=call_id,
                tool=name,
                args=args,
                output=output,
                ok=ok,
                latency_ms=latency_ms,
                truncated=truncated,
            )
        )
        messages.append(ToolMessage(content=output, tool_call_id=call_id, name=name))

    def _truncate(self, output: str) -> tuple[str, bool]:
        cap = self._settings.regime_tool_output_chars
        if len(output) <= cap:
            return output, False
        return output[:cap] + "\n…[truncated]", True

    # -- finishing ----------------------------------------------------------

    def _finish(
        self,
        submission_args: dict[str, Any] | None,
        ledger: EvidenceLedger,
        model_calls: int,
        input_tokens: int,
        output_tokens: int,
    ) -> ReadOutcome:
        base = {
            "calls": ledger.summary(),
            "tool_calls": len(ledger.observations),
            "tool_errors": ledger.failed_calls(),
            "model_calls": model_calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }

        if ledger.successful_calls() == 0:
            return ReadOutcome(
                status=STATUS_DEGRADED,
                label="unknown",
                rationale="No market read succeeded this tick, so the regime is unknown.",
                defect="every tool call failed or none was made",
                **base,
            )

        if submission_args is None:
            return ReadOutcome(
                status=STATUS_DEGRADED,
                label="unknown",
                rationale="The analyst produced no classification within its round budget.",
                defect=f"no submission after {model_calls} model rounds",
                **base,
            )

        try:
            submission = RegimeSubmission.model_validate(submission_args)
        except ValidationError as exc:
            raw_label = submission_args.get("label")
            return ReadOutcome(
                status=STATUS_UNGROUNDED,
                label=str(raw_label) if raw_label else None,
                rationale=str(submission_args.get("rationale", ""))[:1000],
                defect=f"submission failed schema validation: {exc.error_count()} error(s)",
                **base,
            )

        defect = validate_submission(submission, ledger, self._settings.regime_rationale_max_chars)
        evidence = [item.model_dump() for item in submission.evidence]
        if defect is not None:
            return ReadOutcome(
                status=STATUS_UNGROUNDED,
                label=submission.label,
                confidence=submission.confidence,
                rationale=submission.rationale,
                evidence=evidence,
                defect=defect,
                **base,
            )

        return ReadOutcome(
            status=STATUS_OK,
            label=submission.label,
            confidence=round(submission.confidence, 2),
            rationale=submission.rationale,
            evidence=evidence,
            **base,
        )

    def _context(self, request: SpecialistRequest, now_ist: datetime) -> str:
        settings = self._settings
        book = request.book or {}
        positions = book.get("open_positions") or []
        return (
            f"As of {now_ist.strftime('%Y-%m-%d %H:%M:%S')} IST.\n"
            f"Index: {request.index_symbol} on {settings.index_spot_exchange}. "
            f"Options exchange: {settings.option_exchange}. "
            f"Volatility index: {settings.vix_symbol} on {settings.index_spot_exchange}.\n"
            "For open interest, resolve the current futures expiry with get_expiry_dates"
            f"(symbol='{request.index_symbol}', exchange='{settings.option_exchange}', "
            "instrument_type='futures') and quote that contract.\n"
            f"Book: {'flat' if not positions else f'{len(positions)} open position(s)'}. "
            f"Decisions journalled today: {book.get('decisions_today', 0)}.\n"
            f"Tool rounds available: {settings.regime_max_rounds}. "
            "The final round accepts only submit_regime_read."
        )


def build_regime_analyst(
    settings: Settings, prompts: PromptRegistry, tool_source: ToolSource
) -> RegimeAnalyst:
    """Compose the analyst with the configured model tier."""
    return RegimeAnalyst(
        settings=settings,
        prompts=prompts,
        tool_source=tool_source,
        model=build_regime_model(settings),
    )


def build_regime_read_row(
    payload: dict[str, Any],
    *,
    settings: Settings,
    prompts: PromptRegistry,
    tick_id: str,
    trace_id: str,
    trading_day: str,
    source: str,
) -> dict[str, Any]:
    """Turn a specialist payload into the ``regime_reads`` row both callers write."""
    return {
        "read_id": str(payload.get("read_id") or uuid.uuid4()),
        "tick_id": tick_id,
        "trace_id": trace_id,
        "created_at_utc": datetime.now(tz=UTC),
        "trading_day": trading_day,
        "index_symbol": settings.index_symbol,
        "source": source,
        "status": str(payload.get("status", STATUS_DEGRADED)),
        "label": payload.get("label"),
        "confidence": payload.get("confidence"),
        "rationale": str(payload.get("rationale", "")),
        "evidence_json": json.dumps(
            {"cited": payload.get("evidence") or [], "calls": payload.get("calls") or []},
            default=str,
            sort_keys=True,
        ),
        "defect": payload.get("defect"),
        "tool_call_count": int(payload.get("tool_call_count", 0)),
        "tool_error_count": int(payload.get("tool_error_count", 0)),
        "model_calls": int(payload.get("model_calls", 0)),
        "model_version": settings.regime_model if payload.get("model_calls") else "none",
        "input_tokens": int(payload.get("input_tokens", 0)),
        "output_tokens": int(payload.get("output_tokens", 0)),
        "token_cost_micros": cost_micros(
            settings,
            int(payload.get("input_tokens", 0)),
            int(payload.get("output_tokens", 0)),
        ),
        "prompt_name": str(payload.get("prompt_name", PROMPT_NAME)),
        "prompt_version": str(payload.get("prompt_version", "v0")),
        "prompt_digest": str(payload.get("prompt_digest", "")),
        "prompt_set_version": prompts.set_version,
        "latency_ms": int(payload.get("latency_ms", 0)),
        "schema_version": SCHEMA_VERSION,
    }


__all__ = [
    "PROMPT_NAME",
    "STATUS_DEGRADED",
    "STATUS_OK",
    "STATUS_UNGROUNDED",
    "SUBMIT_TOOL_NAME",
    "McpUnavailable",
    "RegimeAnalyst",
    "ReadOutcome",
    "build_regime_analyst",
    "build_regime_read_row",
    "submit_tool",
]
