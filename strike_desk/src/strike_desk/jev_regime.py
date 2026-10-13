"""Regime read via Jev: MCP fetches the numbers, Jev votes the label and side."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from langchain_core.tools import BaseTool
from opentelemetry.trace import Status, StatusCode
from pydantic import ValidationError

from .config import IST, Settings
from .errors import EventCalendarInvalid, McpSessionClosed, ModelCallFailed, SpecialistTimeout
from .events import active_window, load_event_windows
from .grounding import (
    EvidenceItem,
    EvidenceLedger,
    RegimeSubmission,
    ToolObservation,
    tool_output_ok,
    unwrap_tool_output,
    validate_submission,
)
from .jev_client import decide, jev_cost_micros
from .mcp_toolbox import ToolSource
from .observability import get_tracer
from .prompt_registry import PromptRegistry
from .regime_analyst import PROMPT_NAME, STATUS_DEGRADED, STATUS_OK, STATUS_UNGROUNDED, ReadOutcome
from .specialists import ROLE_REGIME, SpecialistRequest, SpecialistResult
from .trend_strength import trend_snapshot as dmi_snapshot

logger = logging.getLogger(__name__)

_SessionClosed = McpSessionClosed


class JevRegimeAnalyst:
    """Same specialist port as RegimeAnalyst. Jev classifies; it does not call tools."""

    role = ROLE_REGIME

    def __init__(
        self, settings: Settings, prompts: PromptRegistry, tool_source: ToolSource
    ) -> None:
        self._settings = settings
        self._prompts = prompts
        self._artifact = prompts.get(PROMPT_NAME)
        self._tool_source = tool_source
        self._tracer = get_tracer()

    def run(self, request: SpecialistRequest) -> SpecialistResult:
        started = time.monotonic()
        read_id = str(uuid.uuid4())
        with self._tracer.start_as_current_span("regime.read") as span:
            span.set_attribute("regime.read_id", read_id)
            span.set_attribute("strike_desk.tick_id", request.tick_id)
            span.set_attribute("strike_desk.index", request.index_symbol)
            span.set_attribute("prompt.name", self._artifact.name)
            span.set_attribute("prompt.version", self._artifact.version)
            span.set_attribute("model.id", self._settings.jev_model)
            span.set_attribute("regime.provider", "jev")

            outcome = self._read(request)
            latency_ms = int((time.monotonic() - started) * 1000)
            cost = jev_cost_micros(self._settings, outcome.input_tokens, outcome.output_tokens)

            span.set_attribute("regime.status", outcome.status)
            span.set_attribute("regime.label", outcome.label or "none")
            span.set_attribute("regime.confidence", outcome.confidence or 0.0)
            span.set_attribute("regime.tool_calls", outcome.tool_calls)
            span.set_attribute("regime.model_calls", outcome.model_calls)
            span.set_attribute("regime.token_cost_micros", cost)
            span.set_attribute("regime.latency_ms", latency_ms)
            if outcome.defect:
                span.set_attribute("regime.defect", outcome.defect)
                span.set_status(Status(StatusCode.ERROR, outcome.status))

            logger.info(
                "regime read %s -> %s/%s via jev in %dms (%d tool calls, %d micros)",
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
                    "trend_snapshot": outcome.trend_snapshot,
                },
                model_version=self._settings.jev_model if outcome.model_calls else None,
                prompt_version=self._artifact.version,
                token_cost_micros=cost,
            )

    def _read(self, request: SpecialistRequest) -> ReadOutcome:
        now_ist = datetime_now()
        try:
            windows = load_event_windows(self._settings.events_path)
        except EventCalendarInvalid as exc:
            return ReadOutcome(
                status=STATUS_DEGRADED,
                label="unknown",
                rationale="The event calendar could not be read, so the session state is unknown.",
                defect=str(exc),
            )
        if active_window(windows, now_ist) is not None:
            return ReadOutcome(
                status=STATUS_OK,
                label="event-driven",
                confidence=0.8,
                rationale=(
                    "An event window is open, so this session is event-driven "
                    "and the playbook stands aside."
                ),
            )

        last_error = "every tool call failed or none was made"
        for attempt in (1, 2):
            self._tool_source.ensure_started()
            tools = {tool.name: tool for tool in self._tool_source.tools(ROLE_REGIME)}
            logger.info("regime tools resolved: %s", sorted(tools))
            try:
                return self._tool_source.submit(
                    lambda tools=tools: self._classify(request, tools),
                    timeout=self._settings.analyst_deadline_seconds,
                )
            except TimeoutError as exc:
                raise SpecialistTimeout(ROLE_REGIME, str(exc)) from exc
            except _SessionClosed as exc:
                last_error = str(exc) or "MCP session closed during the read"
                self._tool_source.mark_broken()
                logger.warning(
                    "MCP session closed during regime read (attempt %d); reconnecting",
                    attempt,
                )
                if attempt == 2:
                    break
        return ReadOutcome(
            status=STATUS_DEGRADED,
            label="unknown",
            rationale="No market read succeeded this tick, so the regime is unknown.",
            defect=last_error,
        )

    async def _classify(
        self, request: SpecialistRequest, tools: dict[str, BaseTool]
    ) -> ReadOutcome:
        ledger = EvidenceLedger()
        ledger.record_playbook("daily-weight 10m-entry prove trending 0.72")
        fetched = await self._fetch(request, tools, ledger)
        base = {
            "calls": ledger.summary(),
            "tool_calls": len(ledger.observations),
            "tool_errors": ledger.failed_calls(),
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
        if ledger.successful_calls() == 0:
            return ReadOutcome(
                status=STATUS_DEGRADED,
                label="unknown",
                rationale="No market read succeeded this tick, so the regime is unknown.",
                defect="every tool call failed or none was made",
                **base,
            )
        try:
            body = decide(self._settings, fetched["state"], _questions())
        except ModelCallFailed as exc:
            return ReadOutcome(
                status=STATUS_DEGRADED,
                label="unknown",
                rationale="Jev could not classify this tick, so the regime is unknown.",
                defect=str(exc),
                **base,
            )
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        base["model_calls"] = 1
        base["input_tokens"] = int(usage.get("input_tokens") or 0)
        base["output_tokens"] = int(usage.get("output_tokens") or 0)
        return self._finish(body.get("answers") or {}, fetched, ledger, base)

    async def _fetch(
        self,
        request: SpecialistRequest,
        tools: dict[str, BaseTool],
        ledger: EvidenceLedger,
    ) -> dict[str, Any]:
        settings = self._settings
        calls = (
            (
                "get_trend_snapshot",
                {
                    "symbol": request.index_symbol,
                    "exchange": settings.index_spot_exchange,
                    "interval": "D",
                },
            ),
            (
                "get_trend_snapshot",
                {
                    "symbol": request.index_symbol,
                    "exchange": settings.index_spot_exchange,
                    "interval": "10m",
                },
            ),
            (
                "get_quote",
                {"symbol": settings.vix_symbol, "exchange": settings.index_spot_exchange},
            ),
        )
        outputs: dict[str, str] = {}
        for name, args in calls:
            outputs[f"{name}:{args.get('interval') or args.get('symbol')}"] = await self._invoke(
                name, args, tools, ledger
            )
        daily = _trend(outputs.get("get_trend_snapshot:D", ""))
        ten = _trend(outputs.get("get_trend_snapshot:10m", ""))
        vix = _ltp(outputs.get(f"get_quote:{settings.vix_symbol}", ""))
        state = {
            "index": request.index_symbol,
            "daily_plus_di": daily[0],
            "daily_minus_di": daily[1],
            "daily_adx": daily[2],
            "ten_min_plus_di": ten[0],
            "ten_min_minus_di": ten[1],
            "ten_min_adx": ten[2],
            "india_vix": vix,
            "playbook": (
                "NIFTY 50 options-buying. Daily +DI vs -DI is the side. "
                "A daily side means trending. Strength (ADX floors) is applied "
                "by the supervisor after this vote; do not sit out on ADX here."
                + _fade_playbook(request.book)
            ),
        }
        return {"state": state, "daily": daily, "ten": ten, "vix": vix}

    async def _invoke(
        self,
        name: str,
        args: dict[str, Any],
        tools: dict[str, BaseTool],
        ledger: EvidenceLedger,
    ) -> str:
        started = time.monotonic()
        tool = tools.get(name)
        if tool is None:
            logger.warning(
                "regime tool %r missing; available=%s",
                name,
                sorted(tools),
            )
            output, ok = f"Error: {name!r} is not an available tool.", False
        else:
            try:
                output, ok = str(await tool.ainvoke(args)), True
            except Exception as exc:  # noqa: BLE001 — a bad call is data, not a crash
                if type(exc).__name__ == "ClosedResourceError":
                    raise _SessionClosed("MCP session closed during a tool call") from exc
                output, ok = f"Error calling {name}: {type(exc).__name__}: {exc}", False
            output = unwrap_tool_output(output)
            if ok and not tool_output_ok(output):
                ok = False
            if not ok:
                logger.warning(
                    "regime tool %s failed (%d chars): %s",
                    name,
                    len(output),
                    output[:160].replace("\n", " "),
                )
        if len(output) > self._settings.regime_tool_output_chars:
            output = output[: self._settings.regime_tool_output_chars] + "\n…[truncated]"
        ledger.record(
            ToolObservation(
                call_id=str(uuid.uuid4()),
                tool=name,
                args=args,
                output=output,
                ok=ok,
                latency_ms=int((time.monotonic() - started) * 1000),
                truncated=False,
            )
        )
        return output

    def _finish(
        self,
        answers: dict[str, Any],
        fetched: dict[str, Any],
        ledger: EvidenceLedger,
        base: dict[str, Any],
    ) -> ReadOutcome:
        daily, ten, vix = fetched["daily"], fetched["ten"], fetched["vix"]
        plus, minus = daily[0], daily[1]
        label_ans = answers.get("label") if isinstance(answers.get("label"), dict) else {}
        side_ans = answers.get("side") if isinstance(answers.get("side"), dict) else {}
        label = str(label_ans.get("choice") or "unknown").strip().lower()
        if label not in {"trending", "unknown"}:
            label = "unknown"
        side = str(side_ans.get("choice") or "").strip().lower()
        if plus is not None and minus is not None and plus != minus:
            label = "trending"
            side = "bullish" if plus > minus else "bearish"
        else:
            label = "unknown"
            if side not in {"bullish", "bearish"}:
                side = "bullish"
        confidence = float(label_ans.get("confidence") or 0.0)
        if label == "trending":
            confidence = 0.72 if confidence < 0.55 else min(confidence, 1.0)
        else:
            confidence = 0.4 if confidence <= 0 else min(confidence, 1.0)

        rationale, evidence = _grounded_sentence(side, daily, ten, vix, ledger=ledger)
        snapshot = dmi_snapshot(daily, ten)
        try:
            submission = RegimeSubmission(
                label=label,  # type: ignore[arg-type]
                confidence=confidence,
                rationale=rationale,
                evidence=evidence,
            )
        except ValidationError as exc:
            return ReadOutcome(
                status=STATUS_UNGROUNDED,
                label=label,
                rationale=rationale,
                defect=f"submission failed schema validation: {exc}",
                trend_snapshot=snapshot,
                **base,
            )
        defect = validate_submission(submission, ledger, self._settings.regime_rationale_max_chars)
        dumped = [item.model_dump() for item in submission.evidence]
        if defect is not None:
            return ReadOutcome(
                status=STATUS_UNGROUNDED,
                label=submission.label,
                confidence=submission.confidence,
                rationale=submission.rationale,
                evidence=dumped,
                defect=defect,
                trend_snapshot=snapshot,
                **base,
            )
        return ReadOutcome(
            status=STATUS_OK,
            label=submission.label,
            confidence=round(submission.confidence, 2),
            rationale=submission.rationale,
            evidence=dumped,
            trend_snapshot=snapshot,
            **base,
        )


def datetime_now():
    from datetime import datetime

    return datetime.now(tz=IST)


def _fade_playbook(book: dict[str, Any]) -> str:
    fade = book.get("ltp_fade") if isinstance(book, dict) else None
    if not isinstance(fade, dict) or fade.get("state") != "FROZEN":
        return ""
    tag = fade.get("tag") or "none"
    return (
        f" 09:21 LTP fade (proxy_k_plus_minus_ltp, not official Calculator Greeks): "
        f"spot={fade.get('spot')} EOS-1={fade.get('eos1')} EOR+1={fade.get('eor1')} "
        f"call_ok={fade.get('call_ok')} put_ok={fade.get('put_ok')} tag={tag}. "
        "A CE tag means buy ATM CE at EOS-1. A PE tag means buy ATM PE at EOR+1. "
        "Classify the daily side as usual; the supervisor uses the fade tag as the trigger."
    )


def _questions() -> dict[str, Any]:
    return {
        "label": {
            "type": "choice",
            "instructions": (
                "Classify the NIFTY 50 session. Choose trending when daily +DI and -DI disagree "
                "(a side exists). Choose unknown only when both daily DIs are missing or equal."
            ),
            "criteria": {
                "trending": "Daily +DI and -DI disagree, so a side exists.",
                "unknown": "Daily +DI and -DI are missing or equal.",
            },
        },
        "side": {
            "type": "choice",
            "instructions": "Daily side only. Higher +DI is bullish. Higher -DI is bearish.",
            "criteria": {
                "bullish": "Daily +DI is greater than daily -DI.",
                "bearish": "Daily -DI is greater than daily +DI.",
            },
        },
    }


def _parse_json(raw: str) -> Any:
    text = unwrap_tool_output(raw)
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except ValueError:
                return None
        return None


def _trend(raw: str) -> tuple[float | None, float | None, float | None]:
    data = _parse_json(raw)
    if not isinstance(data, dict):
        return None, None, None
    indicators = data.get("indicators") if isinstance(data.get("indicators"), dict) else data
    adx = indicators.get("adx_di") if isinstance(indicators, dict) else None
    if isinstance(adx, dict):
        return (
            _num(adx.get("+DI") or adx.get("plus_di")),
            _num(adx.get("-DI") or adx.get("minus_di")),
            _num(adx.get("ADX") or adx.get("adx")),
        )
    if not isinstance(adx, list) or len(adx) < 3:
        return None, None, None
    return _num(adx[0]), _num(adx[1]), _num(adx[2])


def _ltp(raw: str) -> float | None:
    data = _parse_json(raw)
    if not isinstance(data, dict):
        return None
    if "ltp" in data:
        return _num(data.get("ltp"))
    inner = data.get("data")
    if isinstance(inner, dict):
        return _num(inner.get("ltp"))
    return None


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: float | None) -> str | None:
    if value is None:
        return None
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text


def _grounded_sentence(
    side: str,
    daily: tuple[float | None, float | None, float | None],
    ten: tuple[float | None, float | None, float | None],
    vix: float | None,
    ledger: EvidenceLedger | None = None,
) -> tuple[str, list[EvidenceItem]]:
    plus, minus, adx = (_fmt(daily[0]), _fmt(daily[1]), _fmt(daily[2]))
    t_plus, t_minus, t_adx = (_fmt(ten[0]), _fmt(ten[1]), _fmt(ten[2]))
    vix_s = _fmt(vix)
    parts = [side]
    evidence: list[EvidenceItem] = []
    if plus and minus:
        parts.append(f"daily +DI {plus} vs -DI {minus}")
        evidence.append(EvidenceItem(tool="get_trend_snapshot", field="daily +DI", value=plus))
        evidence.append(EvidenceItem(tool="get_trend_snapshot", field="daily -DI", value=minus))
    if adx:
        parts.append(f"ADX {adx}")
        evidence.append(EvidenceItem(tool="get_trend_snapshot", field="daily ADX", value=adx))
    if t_plus and t_minus:
        parts.append(f"10m +DI {t_plus} -DI {t_minus}")
        evidence.append(EvidenceItem(tool="get_trend_snapshot", field="10m +DI", value=t_plus))
        evidence.append(EvidenceItem(tool="get_trend_snapshot", field="10m -DI", value=t_minus))
    if t_adx:
        parts.append(f"10m ADX {t_adx}")
        evidence.append(EvidenceItem(tool="get_trend_snapshot", field="10m ADX", value=t_adx))
    if vix_s:
        parts.append(f"VIX {vix_s}")
        evidence.append(EvidenceItem(tool="get_quote", field="INDIAVIX ltp", value=vix_s))
    if not evidence:
        tool = "get_trend_snapshot"
        if ledger is not None:
            tool = next((item.tool for item in ledger.observations if item.ok), "playbook")
        evidence.append(EvidenceItem(tool=tool, field="adx_di", value="missing"))
        parts = [side, "daily ADX/DMI was not present in the snapshot"]
    rationale = "; ".join(parts) + "."
    if len(rationale) < 10:
        rationale = f"{side}; daily ADX/DMI was not present in the snapshot."
    return rationale[:320], evidence
