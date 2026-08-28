"""The Options Strategist: the desk's second agent, and the only one that names a contract."""

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
from .errors import SpecialistTimeout
from .grounding import (
    EvidenceLedger,
    NoContractSubmission,
    ProposalSubmission,
    ToolObservation,
    validate_no_contract,
    validate_proposal,
)
from .journal import SCHEMA_VERSION
from .mcp_toolbox import ToolSource
from .model_client import build_strategist_model, strategist_cost_micros, token_usage
from .observability import get_tracer
from .playbook import Playbook, check
from .prompt_registry import PromptRegistry
from .specialists import ROLE_STRATEGIST, SpecialistRequest, SpecialistResult

logger = logging.getLogger(__name__)

PROMPT_NAME = "options_strategist"
SUBMIT_PROPOSAL = "submit_proposal"
SUBMIT_NO_CONTRACT = "submit_no_viable_contract"

STATUS_PROPOSED = "proposed"
STATUS_NO_CONTRACT = "no-contract"
STATUS_UNGROUNDED = "ungrounded"
STATUS_INVALID = "invalid"
STATUS_DEGRADED = "degraded"

VERDICT_PASS = "pass"  # nosec B105 — playbook verdict, not a credential
VERDICT_FAIL = "fail"
VERDICT_NA = "n/a"


def _interceptor(name: str, schema: type, description: str) -> StructuredTool:
    """A schema the agent must fill. The loop interprets it; it is never executed."""

    def _intercepted(**_kwargs: Any) -> str:  # pragma: no cover - unreachable by design
        raise RuntimeError(f"{name} is interpreted by the agent loop")

    return StructuredTool.from_function(
        func=_intercepted, name=name, args_schema=schema, description=description
    )


def submission_tools() -> list[StructuredTool]:
    """The two answers a proposal round is allowed to end with."""
    return [
        _interceptor(
            SUBMIT_PROPOSAL,
            ProposalSubmission,
            "Submit one contract proposal. Call this exactly once, last.",
        ),
        _interceptor(
            SUBMIT_NO_CONTRACT,
            NoContractSubmission,
            "Submit that no contract in the chain meets the playbook. "
            "This is a correct answer, not a failure. Call it exactly once, last.",
        ),
    ]


@dataclass
class ProposalOutcome:
    """Everything one proposal attempt produced, whatever way it ended."""

    status: str
    proposal: dict[str, Any] | None = None
    rationale: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    verdict: str = VERDICT_NA
    violations: list[str] = field(default_factory=list)
    defect: str | None = None
    tool_calls: int = 0
    tool_errors: int = 0
    rejected_tools: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class OptionsStrategist:
    """Turns a directional regime into one concrete contract, or into an honest refusal."""

    role = ROLE_STRATEGIST

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
        self._playbook = Playbook.from_settings(settings)
        self._tracer = get_tracer()

    @property
    def playbook(self) -> Playbook:
        return self._playbook

    # -- entry point --------------------------------------------------------

    def run(self, request: SpecialistRequest) -> SpecialistResult:
        """Called by the registry, on a worker thread, under the registry's timeout."""
        started = time.monotonic()
        proposal_id = str(uuid.uuid4())
        with self._tracer.start_as_current_span("strategy.propose") as span:
            span.set_attribute("strategy.proposal_id", proposal_id)
            span.set_attribute("strike_desk.tick_id", request.tick_id)
            span.set_attribute("strike_desk.index", request.index_symbol)
            span.set_attribute("prompt.name", self._artifact.name)
            span.set_attribute("prompt.version", self._artifact.version)
            span.set_attribute("playbook.artifact", self._playbook.artifact)
            span.set_attribute("model.id", self._settings.strategist_model)

            outcome = self._propose(request)
            latency_ms = int((time.monotonic() - started) * 1000)
            cost = strategist_cost_micros(
                self._settings, outcome.input_tokens, outcome.output_tokens
            )
            contract = outcome.proposal or {}

            span.set_attribute("strategy.status", outcome.status)
            span.set_attribute("strategy.symbol", str(contract.get("symbol") or "none"))
            span.set_attribute("strategy.playbook_verdict", outcome.verdict)
            span.set_attribute("strategy.violations", len(outcome.violations))
            span.set_attribute("strategy.tool_calls", outcome.tool_calls)
            span.set_attribute("strategy.tool_errors", outcome.tool_errors)
            span.set_attribute("strategy.rejected_tools", outcome.rejected_tools)
            span.set_attribute("strategy.model_calls", outcome.model_calls)
            span.set_attribute("strategy.token_cost_micros", cost)
            span.set_attribute("strategy.latency_ms", latency_ms)
            if outcome.defect:
                span.set_attribute("strategy.defect", outcome.defect)
                span.set_status(Status(StatusCode.ERROR, outcome.status))

            logger.info(
                "proposal %s -> %s/%s in %dms (%d tool calls, %d micros)",
                proposal_id,
                outcome.status,
                contract.get("symbol") or "-",
                latency_ms,
                outcome.tool_calls,
                cost,
            )
            return SpecialistResult(
                role=ROLE_STRATEGIST,
                payload={
                    "proposal_id": proposal_id,
                    "status": outcome.status,
                    "proposal": outcome.proposal,
                    "rationale": outcome.rationale,
                    "evidence": outcome.evidence,
                    "calls": outcome.calls,
                    "playbook_artifact": self._playbook.artifact,
                    "playbook_verdict": outcome.verdict,
                    "violations": outcome.violations,
                    "defect": outcome.defect,
                    "tool_call_count": outcome.tool_calls,
                    "tool_error_count": outcome.tool_errors,
                    "rejected_tool_count": outcome.rejected_tools,
                    "model_calls": outcome.model_calls,
                    "input_tokens": outcome.input_tokens,
                    "output_tokens": outcome.output_tokens,
                    "latency_ms": latency_ms,
                    "prompt_name": self._artifact.name,
                    "prompt_version": self._artifact.version,
                    "prompt_digest": self._artifact.digest,
                },
                model_version=self._settings.strategist_model if outcome.model_calls else None,
                prompt_version=self._artifact.version,
                token_cost_micros=cost,
            )

    def _propose(self, request: SpecialistRequest) -> ProposalOutcome:
        now_ist = datetime.now(tz=IST)
        self._tool_source.ensure_started()
        try:
            return self._tool_source.submit(
                lambda: self._agent(request, now_ist),
                timeout=self._settings.strategist_deadline_seconds,
            )
        except TimeoutError as exc:
            raise SpecialistTimeout(ROLE_STRATEGIST, str(exc)) from exc

    # -- the loop -----------------------------------------------------------

    async def _agent(self, request: SpecialistRequest, now_ist: datetime) -> ProposalOutcome:
        tools = self._tool_source.tools(ROLE_STRATEGIST)
        by_name = {tool.name: tool for tool in tools}
        submissions = submission_tools()
        submission_names = {tool.name for tool in submissions}
        ledger = EvidenceLedger()
        # The constraints the prompt states are facts the desk gave the agent, so a refusal
        # may name the band it failed without that citation being ungrounded.
        ledger.record_playbook(self._playbook.describe())
        messages: list[Any] = [
            SystemMessage(content=self._system_prompt()),
            HumanMessage(content=self._context(request, now_ist)),
        ]
        model_calls = input_tokens = output_tokens = rejected = 0
        submitted: tuple[str, dict[str, Any]] | None = None

        for round_index in range(1, self._settings.strategist_max_rounds + 1):
            final = round_index == self._settings.strategist_max_rounds
            bound = self._model.bind_tools(
                submissions if final else [*tools, *submissions],
                tool_choice="any" if final else "auto",
            )
            with self._tracer.start_as_current_span("strategy.model_call") as span:
                span.set_attribute("model.id", self._settings.strategist_model)
                span.set_attribute("model.round", round_index)
                span.set_attribute("model.forced_submission", final)
                try:
                    answer = await bound.ainvoke(messages)
                except Exception as exc:  # noqa: BLE001 — one taxonomy for provider failures
                    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                    return self._degraded(
                        f"the model provider failed: {type(exc).__name__}",
                        ledger,
                        model_calls,
                        input_tokens,
                        output_tokens,
                        rejected,
                    )
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
            submitted = next(
                (
                    (call["name"], call["args"])
                    for call in calls
                    if call["name"] in submission_names
                ),
                None,
            )
            if submitted is not None:
                break

            if not calls:
                messages.append(
                    HumanMessage(
                        content=(
                            "Call a market tool, or call submit_proposal or "
                            "submit_no_viable_contract. Do not reply in prose."
                        )
                    )
                )
                continue

            for call in calls:
                if await self._execute(call, by_name, ledger, messages):
                    rejected += 1

        return self._finish(
            submitted, ledger, now_ist, model_calls, input_tokens, output_tokens, rejected
        )

    async def _execute(
        self,
        call: dict[str, Any],
        by_name: dict[str, BaseTool],
        ledger: EvidenceLedger,
        messages: list[Any],
    ) -> bool:
        """Run one tool call, record it, answer the model. Returns True if it was rejected."""
        name = str(call.get("name", ""))
        call_id = str(call.get("id", uuid.uuid4()))
        args = dict(call.get("args") or {})
        started = time.monotonic()
        rejected = False

        with self._tracer.start_as_current_span("strategy.tool_call") as span:
            span.set_attribute("tool.name", name)
            span.set_attribute("tool.args", json.dumps(args, default=str, sort_keys=True))
            tool = by_name.get(name)
            if tool is None:
                # Structural guardrail: nothing outside the strategist's whitelist is
                # reachable, and an attempt to reach past it is counted, not dropped.
                rejected = True
                span.set_attribute("tool.rejected", True)
                span.set_status(Status(StatusCode.ERROR, "tool not in whitelist"))
                logger.warning("strategist attempted non-whitelisted tool %r", name)
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
        return rejected

    def _truncate(self, output: str) -> tuple[str, bool]:
        cap = self._settings.strategist_tool_output_chars
        if len(output) <= cap:
            return output, False
        return output[:cap] + "\n…[truncated]", True

    # -- finishing ----------------------------------------------------------

    def _base(
        self,
        ledger: EvidenceLedger,
        model_calls: int,
        input_tokens: int,
        output_tokens: int,
        rejected: int,
    ) -> dict[str, Any]:
        return {
            "calls": ledger.summary(),
            "tool_calls": len(ledger.observations),
            "tool_errors": ledger.failed_calls(),
            "rejected_tools": rejected,
            "model_calls": model_calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }

    def _degraded(
        self,
        defect: str,
        ledger: EvidenceLedger,
        model_calls: int,
        input_tokens: int,
        output_tokens: int,
        rejected: int,
    ) -> ProposalOutcome:
        return ProposalOutcome(
            status=STATUS_DEGRADED,
            rationale="No contract could be proposed from this chain read.",
            defect=defect,
            **self._base(ledger, model_calls, input_tokens, output_tokens, rejected),
        )

    def _finish(
        self,
        submitted: tuple[str, dict[str, Any]] | None,
        ledger: EvidenceLedger,
        now_ist: datetime,
        model_calls: int,
        input_tokens: int,
        output_tokens: int,
        rejected: int,
    ) -> ProposalOutcome:
        base = self._base(ledger, model_calls, input_tokens, output_tokens, rejected)

        if ledger.successful_calls() == 0:
            return self._degraded(
                "every tool call failed or none was made",
                ledger,
                model_calls,
                input_tokens,
                output_tokens,
                rejected,
            )
        if submitted is None:
            return self._degraded(
                f"no submission after {model_calls} model rounds",
                ledger,
                model_calls,
                input_tokens,
                output_tokens,
                rejected,
            )

        name, args = submitted
        cap = self._settings.proposal_rationale_max_chars
        if name == SUBMIT_NO_CONTRACT:
            try:
                refusal = NoContractSubmission.model_validate(args)
            except ValidationError as exc:
                return ProposalOutcome(
                    status=STATUS_UNGROUNDED,
                    defect=f"refusal failed schema validation: {exc.error_count()} error(s)",
                    **base,
                )
            defect = validate_no_contract(refusal, ledger, cap)
            evidence = [item.model_dump() for item in refusal.evidence]
            if defect is not None:
                return ProposalOutcome(
                    status=STATUS_UNGROUNDED,
                    rationale=refusal.reason,
                    evidence=evidence,
                    defect=defect,
                    **base,
                )
            return ProposalOutcome(
                status=STATUS_NO_CONTRACT,
                rationale=refusal.reason,
                evidence=evidence,
                verdict=VERDICT_NA,
                **base,
            )

        try:
            proposal = ProposalSubmission.model_validate(args)
        except ValidationError as exc:
            return ProposalOutcome(
                status=STATUS_UNGROUNDED,
                rationale=str(args.get("rationale", ""))[:2000],
                defect=f"proposal failed schema validation: {exc.error_count()} error(s)",
                **base,
            )

        evidence = [item.model_dump() for item in proposal.evidence]
        contract = proposal.model_dump(exclude={"rationale", "evidence"})

        defect = validate_proposal(proposal, ledger, cap)
        if defect is not None:
            return ProposalOutcome(
                status=STATUS_UNGROUNDED,
                proposal=contract,
                rationale=proposal.rationale,
                evidence=evidence,
                defect=defect,
                **base,
            )

        violations = check(proposal, self._playbook, now_ist=now_ist)
        if violations:
            return ProposalOutcome(
                status=STATUS_INVALID,
                proposal=contract,
                rationale=proposal.rationale,
                evidence=evidence,
                verdict=VERDICT_FAIL,
                violations=violations,
                defect=f"{len(violations)} playbook violation(s): {violations[0]}",
                **base,
            )

        return ProposalOutcome(
            status=STATUS_PROPOSED,
            proposal=contract,
            rationale=proposal.rationale,
            evidence=evidence,
            verdict=VERDICT_PASS,
            **base,
        )

    # -- the request --------------------------------------------------------

    def _system_prompt(self) -> str:
        return self._artifact.content.replace("{constraints}", self._playbook.describe())

    def _context(self, request: SpecialistRequest, now_ist: datetime) -> str:
        settings = self._settings
        regime = request.book.get("regime") or {}
        return (
            f"As of {now_ist.strftime('%Y-%m-%d %H:%M:%S')} IST.\n"
            f"Index: {request.index_symbol} on {settings.index_spot_exchange}. "
            f"Options exchange: {settings.option_exchange}.\n"
            f"Regime: {regime.get('label', 'unknown')} at "
            f"{regime.get('confidence', 0.0)} confidence. "
            f"Analyst said: {regime.get('rationale', '(nothing)')}\n"
            f"The book is flat; this would be the only open position.\n"
            f"Tool rounds available: {settings.strategist_max_rounds}. "
            "The final round accepts only a submission."
        )


def build_options_strategist(
    settings: Settings, prompts: PromptRegistry, tool_source: ToolSource
) -> OptionsStrategist:
    """Compose the strategist with the configured model tier."""
    return OptionsStrategist(
        settings=settings,
        prompts=prompts,
        tool_source=tool_source,
        model=build_strategist_model(settings),
    )


def build_proposal_row(
    payload: dict[str, Any],
    *,
    settings: Settings,
    prompts: PromptRegistry,
    tick_id: str,
    trace_id: str,
    trading_day: str,
    source: str,
    regime_label: str | None,
    regime_confidence: float | None,
) -> dict[str, Any]:
    """Turn a specialist payload into the ``proposals`` row the tick writes."""
    contract = payload.get("proposal") or {}
    return {
        "proposal_id": str(payload.get("proposal_id") or uuid.uuid4()),
        "tick_id": tick_id,
        "trace_id": trace_id,
        "created_at_utc": datetime.now(tz=UTC),
        "trading_day": trading_day,
        "index_symbol": settings.index_symbol,
        "source": source,
        "status": str(payload.get("status", STATUS_DEGRADED)),
        "regime_label": regime_label,
        "regime_confidence": regime_confidence,
        "direction": contract.get("direction"),
        "symbol": contract.get("symbol"),
        "expiry": contract.get("expiry"),
        "strike": contract.get("strike"),
        "option_type": contract.get("option_type"),
        "lots": contract.get("lots"),
        "lot_size": contract.get("lot_size"),
        "quantity": contract.get("quantity"),
        "entry_price_low": contract.get("entry_price_low"),
        "entry_price_high": contract.get("entry_price_high"),
        "delta": contract.get("delta"),
        "theta_per_day": contract.get("theta_per_day"),
        "implied_volatility": contract.get("implied_volatility"),
        "open_interest": contract.get("open_interest"),
        "breakeven": contract.get("breakeven"),
        "stop_price": contract.get("stop_price"),
        "target_price": contract.get("target_price"),
        "time_stop_ist": contract.get("time_stop_ist"),
        "rationale": str(payload.get("rationale", "")),
        "evidence_json": json.dumps(
            {"cited": payload.get("evidence") or [], "calls": payload.get("calls") or []},
            default=str,
            sort_keys=True,
        ),
        "playbook_artifact": str(payload.get("playbook_artifact", "unknown")),
        "playbook_verdict": str(payload.get("playbook_verdict", VERDICT_NA)),
        "violations_json": json.dumps(payload.get("violations") or [], sort_keys=True),
        "defect": payload.get("defect"),
        "tool_call_count": int(payload.get("tool_call_count", 0)),
        "tool_error_count": int(payload.get("tool_error_count", 0)),
        "rejected_tool_count": int(payload.get("rejected_tool_count", 0)),
        "model_calls": int(payload.get("model_calls", 0)),
        "model_version": settings.strategist_model if payload.get("model_calls") else "none",
        "input_tokens": int(payload.get("input_tokens", 0)),
        "output_tokens": int(payload.get("output_tokens", 0)),
        "token_cost_micros": strategist_cost_micros(
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
    "STATUS_INVALID",
    "STATUS_NO_CONTRACT",
    "STATUS_PROPOSED",
    "STATUS_UNGROUNDED",
    "SUBMIT_NO_CONTRACT",
    "SUBMIT_PROPOSAL",
    "VERDICT_FAIL",
    "VERDICT_NA",
    "VERDICT_PASS",
    "OptionsStrategist",
    "ProposalOutcome",
    "build_options_strategist",
    "build_proposal_row",
    "submission_tools",
]
