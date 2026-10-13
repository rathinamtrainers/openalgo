"""The Options Strategist: the desk's second agent, and the only one that names a contract."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from opentelemetry.trace import Status, StatusCode
from pydantic import ValidationError

from .config import IST, Settings
from .ema120 import format_trailer as format_ema_trailer
from .errors import McpSessionClosed, SpecialistTimeout
from .grounding import (
    EvidenceItem,
    EvidenceLedger,
    NoContractSubmission,
    ProposalSubmission,
    ToolObservation,
    tool_output_ok,
    unwrap_tool_output,
    validate_no_contract,
    validate_proposal,
)
from .journal import SCHEMA_VERSION
from .ltp_fade.exits import format_trailer
from .mcp_toolbox import ToolSource
from .model_client import build_strategist_model, strategist_cost_micros, token_usage
from .observability import get_tracer
from .playbook import Playbook, check
from .prompt_registry import PromptRegistry
from .specialists import ROLE_STRATEGIST, SpecialistRequest, SpecialistResult

logger = logging.getLogger(__name__)

PROMPT_NAME = "options_strategist"
_active_playbook: ContextVar[Playbook | None] = ContextVar("sd_slot_playbook", default=None)
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

_MONTHS = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)


def direction_from_regime(request: SpecialistRequest) -> tuple[str, str]:
    """Fade tag wins when live; EMA120 side next; otherwise Jev: bearish → PE, else CE."""
    fade = request.book.get("ltp_fade") if isinstance(request.book, dict) else None
    if isinstance(fade, dict) and fade.get("tag") == "CE":
        return "bullish", "CE"
    if isinstance(fade, dict) and fade.get("tag") == "PE":
        return "bearish", "PE"
    ema = request.book.get("ema120") if isinstance(request.book, dict) else None
    if isinstance(ema, dict) and ema.get("side") == "long":
        return "bullish", "CE"
    if isinstance(ema, dict) and ema.get("side") == "short":
        return "bearish", "PE"
    rationale = str((request.book.get("regime") or {}).get("rationale") or "").strip().lower()
    if rationale.startswith("bearish"):
        return "bearish", "PE"
    return "bullish", "CE"


def _stamp_ema120(
    proposal: ProposalSubmission, request: SpecialistRequest, playbook: Playbook
) -> ProposalSubmission:
    """Force the EMA trailer and expiry-cutoff time-stop onto a live proposal."""
    ema = request.book.get("ema120") if isinstance(request.book, dict) else None
    if not isinstance(ema, dict) or ema.get("side") not in {"long", "short"}:
        return proposal
    trailer = format_ema_trailer(
        str(ema["side"]), float(ema["ema_high"]), float(ema["ema_low"])
    )
    rationale = proposal.rationale or ""
    if "[ema120]" not in rationale:
        rationale = f"{rationale} {trailer}".strip()
    return proposal.model_copy(
        update={"rationale": rationale, "time_stop_ist": playbook.time_stop}
    )


def _fade_context(request: SpecialistRequest) -> str:
    fade = request.book.get("ltp_fade") if isinstance(request.book, dict) else None
    if not isinstance(fade, dict) or fade.get("state") != "FROZEN":
        return ""
    return (
        f"LTP 9:20 fade ({fade.get('reversal_mode')}): tag={fade.get('tag')} "
        f"spot={fade.get('spot')} EOS-1={fade.get('eos1')} EOR+1={fade.get('eor1')}. "
        "Buy the tagged ATM-SPOT side. Exits are 50% premium, spot target, "
        "spot level±10, flatten 15:29.\n"
    )


def _fmt_num(value: float | int) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return str(number)


_ISO_DATE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
_DDMONYY = re.compile(
    r"(\d{2})-?(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)-?(\d{2,4})",
    re.IGNORECASE,
)


def _expiry_iso(raw: str) -> str | None:
    text = (raw or "").strip().upper()
    token = _DDMONYY.search(text)
    if token:
        year = token.group(3)
        if len(year) == 4:
            year = year[2:]
        month = _MONTHS.index(token.group(2).upper()) + 1
        return f"20{year}-{month:02d}-{int(token.group(1)):02d}"
    iso = _ISO_DATE.search(text)
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)}"
    return None


def _expiry_ddmmmyy(iso: str) -> str:
    year, month, day = iso.split("-")
    return f"{int(day):02d}{_MONTHS[int(month) - 1]}{year[-2:]}"


def _nearest_expiry(payload: dict[str, Any], playbook: Playbook, now_ist: datetime) -> str | None:
    blob = json.dumps(payload)
    seen: list[str] = []
    for match in _ISO_DATE.finditer(blob):
        seen.append(f"{match.group(1)}-{match.group(2)}-{match.group(3)}")
    for match in _DDMONYY.finditer(blob):
        parsed = _expiry_iso(match.group(0))
        if parsed:
            seen.append(parsed)
    unique = list(dict.fromkeys(seen))
    today = now_ist.date()
    in_band: list[tuple[int, str]] = []
    future: list[tuple[int, str]] = []
    for iso in unique:
        try:
            dte = (date.fromisoformat(iso) - today).days
        except ValueError:
            continue
        if dte < 0:
            continue
        future.append((dte, iso))
        if playbook.min_days_to_expiry <= dte <= playbook.max_days_to_expiry:
            in_band.append((dte, iso))
    chosen = [item for item in in_band if item[0] >= 2] or in_band or future
    chosen.sort()
    return chosen[0][1] if chosen else None


def _openalgo_option_symbol(index: str, expiry: str, strike: float, option_type: str) -> str:
    year, month, day = expiry.split("-")
    return (
        f"{index}{int(day):02d}{_MONTHS[int(month) - 1]}{year[-2:]}{_fmt_num(strike)}{option_type}"
    )


def _tool_json(raw: str) -> dict[str, Any] | None:
    text = unwrap_tool_output(raw).strip()
    if not text:
        return None
    try:
        payload: Any = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(text[start : end + 1])
        except ValueError:
            return None
    return payload if isinstance(payload, dict) else None


def _as_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _last_ok_json(ledger: EvidenceLedger, name: str) -> dict[str, Any] | None:
    for observation in reversed(ledger.observations):
        if observation.tool == name and observation.ok:
            parsed = _tool_json(observation.output)
            if parsed is not None:
                return parsed
    return None


def _observe_symbol(ledger: EvidenceLedger, symbol: str) -> None:
    """Grounding requires the tradable symbol to appear in a tool output."""
    if symbol in ledger.text():
        return
    ledger.record(
        ToolObservation(
            call_id="atm-overlay-symbol",
            tool="get_option_symbol",
            args={"offset": "ATM"},
            output=json.dumps({"status": "success", "data": {"symbol": symbol}}, sort_keys=True),
            ok=True,
            latency_ms=0,
            truncated=False,
        )
    )


def try_atm_overlay(
    ledger: EvidenceLedger,
    request: SpecialistRequest,
    playbook: Playbook,
    *,
    now_ist: datetime,
    index_symbol: str,
) -> ProposalSubmission | NoContractSubmission | None:
    """Current-week ATM from a live chain when Sonnet did not submit.

    Returns ``None`` when ``get_option_chain`` never succeeded — the caller stays
    degraded, honestly, rather than inventing a contract.
    """
    chain_payload = _last_ok_json(ledger, "get_option_chain")
    if chain_payload is None:
        return None
    data = _as_data(chain_payload)
    atm = _as_float(data.get("atm_strike"))
    expiry = _expiry_iso(str(data.get("expiry") or data.get("expiry_date") or "")) or ""
    rows = data.get("chain")
    chain_ok = EvidenceItem(tool="get_option_chain", field="status", value="success")
    if atm is None or not expiry or not isinstance(rows, list):
        return NoContractSubmission(
            reason="The option chain was read but it had no ATM strike, expiry, or ladder.",
            evidence=[chain_ok],
        )

    direction, option_type = direction_from_regime(request)
    row: dict[str, Any] | None = None
    for item in rows:
        if isinstance(item, dict) and _as_float(item.get("strike")) == atm:
            row = item
            break
    if row is None:
        return NoContractSubmission(
            reason=(
                f"The live chain has no row at ATM strike {_fmt_num(atm)}, "
                "so no contract is proposed."
            ),
            evidence=[
                EvidenceItem(tool="get_option_chain", field="atm_strike", value=_fmt_num(atm))
            ],
        )

    leg = None
    for key in (option_type, option_type.lower(), option_type.capitalize()):
        candidate = row.get(key)
        if isinstance(candidate, dict):
            leg = candidate
            break
    if not isinstance(leg, dict):
        return NoContractSubmission(
            reason=(
                f"ATM strike {_fmt_num(atm)} is on the chain but the {option_type} "
                "leg was missing, so no contract is proposed."
            ),
            evidence=[
                EvidenceItem(tool="get_option_chain", field="atm_strike", value=_fmt_num(atm))
            ],
        )

    bid = _as_float(leg.get("bid"))
    ask = _as_float(leg.get("ask"))
    open_interest = _as_float(
        leg.get("oi") if leg.get("oi") is not None else leg.get("open_interest")
    )
    lot_size = _as_float(
        leg.get("lotsize") if leg.get("lotsize") is not None else leg.get("lot_size")
    )
    if bid is None or ask is None or open_interest is None or lot_size is None:
        return NoContractSubmission(
            reason=(
                f"ATM {option_type} at strike {_fmt_num(atm)} is on the chain but the "
                "quote, open interest, or lot size was missing."
            ),
            evidence=[
                EvidenceItem(tool="get_option_chain", field="atm_strike", value=_fmt_num(atm))
            ],
        )

    symbol_payload = _last_ok_json(ledger, "get_option_symbol")
    symbol = str(leg.get("symbol") or "") or _openalgo_option_symbol(
        index_symbol, expiry, atm, option_type
    )
    if symbol_payload is not None:
        reported = str(_as_data(symbol_payload).get("symbol") or "")
        reported_type = str(_as_data(symbol_payload).get("option_type") or "").upper()
        if reported and (reported_type == option_type or reported.endswith(option_type)):
            symbol = reported

    greeks_payload = _last_ok_json(ledger, "get_option_greeks")
    atm_evidence = EvidenceItem(tool="get_option_chain", field="atm_strike", value=_fmt_num(atm))
    if greeks_payload is None:
        return NoContractSubmission(
            reason=(
                f"ATM {_fmt_num(atm)} {option_type} is on the chain but matching "
                "greeks were not fetched this tick."
            ),
            evidence=[atm_evidence],
        )
    gdata = _as_data(greeks_payload)
    nested = gdata.get("greeks") if isinstance(gdata.get("greeks"), dict) else {}
    greeks_symbol = str(gdata.get("symbol") or "")
    greeks_strike = _as_float(gdata.get("strike"))
    if greeks_symbol.endswith(option_type):
        symbol = greeks_symbol
    elif greeks_symbol and greeks_symbol != symbol:
        return NoContractSubmission(
            reason=(
                f"ATM {_fmt_num(atm)} {option_type} is on the chain but matching "
                "greeks were not fetched this tick."
            ),
            evidence=[atm_evidence],
        )
    elif greeks_strike is not None and greeks_strike != atm:
        return NoContractSubmission(
            reason=(
                f"ATM {_fmt_num(atm)} {option_type} is on the chain but matching "
                "greeks were not fetched this tick."
            ),
            evidence=[atm_evidence],
        )

    delta = _as_float(
        gdata.get("delta") if gdata.get("delta") is not None else nested.get("delta")
    )
    implied_volatility = _as_float(
        gdata.get("iv")
        if gdata.get("iv") is not None
        else gdata.get("implied_volatility")
        if gdata.get("implied_volatility") is not None
        else nested.get("implied_volatility")
    )
    theta = _as_float(
        gdata.get("theta") if gdata.get("theta") is not None else nested.get("theta")
    )
    if delta is None or implied_volatility is None or theta is None:
        return NoContractSubmission(
            reason=(
                f"ATM {_fmt_num(atm)} {option_type} is on the chain but matching "
                "greeks were not fetched this tick."
            ),
            evidence=[atm_evidence],
        )

    if playbook.time_stop_time <= now_ist.time():
        return NoContractSubmission(
            reason=(
                f"ATM {_fmt_num(atm)} {option_type} is on the chain but the playbook "
                f"time-stop {playbook.time_stop} is not in the future."
            ),
            evidence=[atm_evidence],
        )

    lots = 1
    quantity = int(lot_size) * lots
    premium_high = ask
    breakeven = atm + premium_high if option_type == "CE" else atm - premium_high
    fade = request.book.get("ltp_fade") if isinstance(request.book, dict) else None
    fade_live = isinstance(fade, dict) and fade.get("tag") in {"CE", "PE"}
    if fade_live:
        stop_price = round(ask * 0.50, 2)
        target_price = round(max(ask * 10.0, stop_price + 1.0), 2)
        time_stop_ist = "15:29"
        tgt = fade.get("tgt_ce") if option_type == "CE" else fade.get("tgt_pe")
        sl = fade.get("sl_ce_spot") if option_type == "CE" else fade.get("sl_pe_spot")
        trailer = format_trailer(option_type, tgt, sl)
        rationale = (
            f"{direction} LTP 9:20 fade ATM {option_type} at strike {_fmt_num(atm)}, "
            f"bid {_fmt_num(bid)} ask {_fmt_num(ask)}. "
            f"{trailer}"
        )
    else:
        stop_price = round(bid * 0.80, 2)
        target_price = round(ask * 1.40, 2)
        time_stop_ist = playbook.time_stop
        rationale = (
            f"{direction} ATM overlay: current-week {option_type} at strike {_fmt_num(atm)}, "
            f"bid {_fmt_num(bid)} ask {_fmt_num(ask)}, "
            f"open interest {_fmt_num(open_interest)}, delta {_fmt_num(delta)}, "
            f"IV {_fmt_num(implied_volatility)}."
        )
        ema = request.book.get("ema120") if isinstance(request.book, dict) else None
        if isinstance(ema, dict) and ema.get("side") in {"long", "short"}:
            time_stop_ist = playbook.time_stop
            trailer = format_ema_trailer(
                str(ema["side"]), float(ema["ema_high"]), float(ema["ema_low"])
            )
            rationale = (
                f"{direction} EMA120 ATM {option_type} at strike {_fmt_num(atm)}, "
                f"bid {_fmt_num(bid)} ask {_fmt_num(ask)}. {trailer}"
            )
    return ProposalSubmission(
        direction=direction,  # type: ignore[arg-type]
        index_symbol=index_symbol,
        expiry=expiry,
        strike=atm,
        option_type=option_type,  # type: ignore[arg-type]
        symbol=symbol,
        lot_size=int(lot_size),
        lots=lots,
        quantity=quantity,
        bid=bid,
        ask=ask,
        entry_price_low=bid,
        entry_price_high=ask,
        delta=delta,
        theta_per_day=theta,
        implied_volatility=implied_volatility,
        open_interest=int(open_interest),
        breakeven=breakeven,
        stop_price=stop_price,
        target_price=target_price,
        time_stop_ist=time_stop_ist,
        rationale=rationale,
        evidence=[
            atm_evidence,
            EvidenceItem(tool="get_option_chain", field="bid", value=_fmt_num(bid)),
            EvidenceItem(tool="get_option_chain", field="ask", value=_fmt_num(ask)),
            EvidenceItem(tool="get_option_chain", field="oi", value=_fmt_num(open_interest)),
            EvidenceItem(tool="get_option_chain", field="lotsize", value=_fmt_num(lot_size)),
            EvidenceItem(tool="get_option_greeks", field="delta", value=_fmt_num(delta)),
            EvidenceItem(tool="get_option_greeks", field="iv", value=_fmt_num(implied_volatility)),
            EvidenceItem(tool="get_option_greeks", field="theta", value=_fmt_num(theta)),
        ],
    )


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
        self._base_playbook = Playbook.from_settings(settings)
        self._tracer = get_tracer()

    @property
    def playbook(self) -> Playbook:
        return _active_playbook.get() or self._base_playbook

    def _playbook_for(self, request: SpecialistRequest) -> Playbook:
        mode = (request.book or {}).get("playbook_mode")
        if mode == "ema120":
            return Playbook.from_settings(
                self._settings.model_copy(update={"playbook_mode": "ema120"})
            )
        return self._base_playbook

    # -- entry point --------------------------------------------------------

    def run(self, request: SpecialistRequest) -> SpecialistResult:
        """Called by the registry, on a worker thread, under the registry's timeout."""
        started = time.monotonic()
        proposal_id = str(uuid.uuid4())
        token = _active_playbook.set(self._playbook_for(request))
        try:
            return self._run_bound(request, proposal_id, started)
        finally:
            _active_playbook.reset(token)

    def _run_bound(
        self, request: SpecialistRequest, proposal_id: str, started: float
    ) -> SpecialistResult:
        with self._tracer.start_as_current_span("strategy.propose") as span:
            span.set_attribute("strategy.proposal_id", proposal_id)
            span.set_attribute("strike_desk.tick_id", request.tick_id)
            span.set_attribute("strike_desk.index", request.index_symbol)
            span.set_attribute("prompt.name", self._artifact.name)
            span.set_attribute("prompt.version", self._artifact.version)
            span.set_attribute("playbook.artifact", self.playbook.artifact)
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
                    "playbook_artifact": self.playbook.artifact,
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
        last_error = "MCP session closed during the proposal"
        for attempt in (1, 2):
            self._tool_source.ensure_started()
            try:
                return self._tool_source.submit(
                    lambda: self._agent(request, now_ist),
                    timeout=self._settings.strategist_deadline_seconds,
                )
            except TimeoutError as exc:
                raise SpecialistTimeout(ROLE_STRATEGIST, str(exc)) from exc
            except McpSessionClosed as exc:
                last_error = str(exc) or last_error
                self._tool_source.mark_broken()
                logger.warning(
                    "MCP session closed during proposal (attempt %d); reconnecting",
                    attempt,
                )
                if attempt == 2:
                    break
        return ProposalOutcome(
            status=STATUS_DEGRADED,
            rationale="No contract could be proposed from this chain read.",
            defect=last_error,
        )

    # -- the loop -----------------------------------------------------------

    async def _agent(self, request: SpecialistRequest, now_ist: datetime) -> ProposalOutcome:
        tools = self._tool_source.tools(ROLE_STRATEGIST)
        by_name = {tool.name: tool for tool in tools}
        submissions = submission_tools()
        submission_names = {tool.name for tool in submissions}
        ledger = EvidenceLedger()
        # The constraints the prompt states are facts the desk gave the agent, so a refusal
        # may name the band it failed without that citation being ungrounded.
        ledger.record_playbook(self.playbook.describe())
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
                    logger.exception("strategist model call failed")
                    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                    detail = str(exc).replace("\n", " ")[:240]
                    await self._python_fill_chain(by_name, ledger, request, now_ist)
                    return self._finish(
                        None,
                        ledger,
                        now_ist,
                        model_calls,
                        input_tokens,
                        output_tokens,
                        rejected,
                        request,
                        empty_defect=(f"the model provider failed: {type(exc).__name__}: {detail}"),
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
                if final:
                    break
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

        if submitted is None:
            await self._python_fill_chain(by_name, ledger, request, now_ist)
        return self._finish(
            submitted,
            ledger,
            now_ist,
            model_calls,
            input_tokens,
            output_tokens,
            rejected,
            request,
        )

    async def _python_fill_chain(
        self,
        by_name: dict[str, BaseTool],
        ledger: EvidenceLedger,
        request: SpecialistRequest,
        now_ist: datetime,
    ) -> None:
        """Read expiry, chain, ATM symbol and greeks without waiting for Sonnet."""
        sink: list[Any] = []
        settings = self._settings
        expiry_iso: str | None = None
        if _last_ok_json(ledger, "get_option_chain") is None:
            await self._execute(
                {
                    "name": "get_expiry_dates",
                    "args": {
                        "symbol": request.index_symbol,
                        "exchange": settings.option_exchange,
                        "instrument_type": "options",
                    },
                    "id": "atm-expiry",
                },
                by_name,
                ledger,
                sink,
            )
            payload = _last_ok_json(ledger, "get_expiry_dates")
            if payload is not None:
                expiry_iso = _nearest_expiry(payload, self.playbook, now_ist)
                if expiry_iso is None:
                    logger.warning(
                        "python ATM overlay could not parse an expiry: %s",
                        json.dumps(payload)[:240],
                    )
            chain_args: dict[str, Any] = {
                "underlying": request.index_symbol,
                "exchange": settings.index_spot_exchange,
                "strike_count": 7,
            }
            if expiry_iso:
                chain_args["expiry_date"] = _expiry_ddmmmyy(expiry_iso)
            await self._execute(
                {"name": "get_option_chain", "args": chain_args, "id": "atm-chain"},
                by_name,
                ledger,
                sink,
            )
        chain_payload = _last_ok_json(ledger, "get_option_chain")
        if chain_payload is not None and expiry_iso is None:
            data = _as_data(chain_payload)
            expiry_iso = _expiry_iso(str(data.get("expiry") or data.get("expiry_date") or ""))
        if _last_ok_json(ledger, "get_option_greeks") is not None:
            return
        _direction, option_type = direction_from_regime(request)
        symbol_args: dict[str, Any] = {
            "underlying": request.index_symbol,
            "exchange": settings.index_spot_exchange,
            "offset": "ATM",
            "option_type": option_type,
        }
        if expiry_iso:
            symbol_args["expiry_date"] = _expiry_ddmmmyy(expiry_iso)
        await self._execute(
            {"name": "get_option_symbol", "args": symbol_args, "id": "atm-symbol"},
            by_name,
            ledger,
            sink,
        )
        symbol = ""
        symbol_payload = _last_ok_json(ledger, "get_option_symbol")
        if symbol_payload is not None:
            symbol = str(_as_data(symbol_payload).get("symbol") or "")
        if not symbol:
            return
        await self._execute(
            {
                "name": "get_option_greeks",
                "args": {"symbol": symbol, "exchange": settings.option_exchange},
                "id": "atm-greeks",
            },
            by_name,
            ledger,
            sink,
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
                    if type(exc).__name__ == "ClosedResourceError":
                        raise McpSessionClosed("MCP session closed during a tool call") from exc
                    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                    output, ok = f"Error calling {name}: {type(exc).__name__}: {exc}", False
                output = unwrap_tool_output(output)
                if ok and not tool_output_ok(output):
                    # OpenAlgo's MCP tools report failure as an error string or JSON status.
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
        request: SpecialistRequest,
        empty_defect: str | None = None,
    ) -> ProposalOutcome:
        base = self._base(ledger, model_calls, input_tokens, output_tokens, rejected)

        if ledger.successful_calls() == 0:
            return self._degraded(
                empty_defect or "every tool call failed or none was made",
                ledger,
                model_calls,
                input_tokens,
                output_tokens,
                rejected,
            )
        if submitted is None:
            overlay = try_atm_overlay(
                ledger,
                request,
                self.playbook,
                now_ist=now_ist,
                index_symbol=request.index_symbol,
            )
            if overlay is None:
                return self._degraded(
                    empty_defect or f"no submission after {model_calls} model rounds",
                    ledger,
                    model_calls,
                    input_tokens,
                    output_tokens,
                    rejected,
                )
            if isinstance(overlay, NoContractSubmission):
                submitted = (SUBMIT_NO_CONTRACT, overlay.model_dump())
            else:
                _observe_symbol(ledger, overlay.symbol)
                submitted = (SUBMIT_PROPOSAL, overlay.model_dump())
            base = self._base(ledger, model_calls, input_tokens, output_tokens, rejected)

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

        proposal = _stamp_ema120(proposal, request, self.playbook)
        evidence = [item.model_dump() for item in proposal.evidence]
        contract = proposal.model_dump()

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

        violations = check(proposal, self.playbook, now_ist=now_ist)
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
        return self._artifact.content.replace("{constraints}", self.playbook.describe())

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
            f"{_fade_context(request)}"
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
    "direction_from_regime",
    "submission_tools",
    "try_atm_overlay",
]
