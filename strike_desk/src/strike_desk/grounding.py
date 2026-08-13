"""Grounding: what the agent read, and the refusal to let it cite anything else."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

LABELS: tuple[str, ...] = (
    "trending",
    "range-bound",
    "high-volatility",
    "event-driven",
    "unknown",
)

TRADEABLE_LABELS = frozenset({"trending", "range-bound"})

_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


class EvidenceItem(BaseModel):
    """One data point the agent claims to have read, and where it came from."""

    tool: str = Field(description="The tool call that produced this value.")
    field: str = Field(description="The field or indicator name, e.g. 'rsi_14'.")
    value: str = Field(description="The value exactly as the tool reported it.")


class RegimeSubmission(BaseModel):
    """The one structured answer a regime read is allowed to end with."""

    label: Literal["trending", "range-bound", "high-volatility", "event-driven", "unknown"] = Field(
        description="The regime this index is in right now."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="0-1 confidence in the label.")
    rationale: str = Field(
        min_length=10,
        description="One sentence a trader can read back in a month. No confidence value in it.",
    )
    evidence: list[EvidenceItem] = Field(
        min_length=1, description="Every data point the rationale rests on."
    )


@dataclass(frozen=True)
class ToolObservation:
    """One executed tool call and what came back from it."""

    call_id: str
    tool: str
    args: dict[str, Any]
    output: str
    ok: bool
    latency_ms: int
    truncated: bool


def extract_numbers(text: str) -> set[float]:
    """Every number appearing anywhere in a blob of text."""
    found: set[float] = set()
    for raw in _NUMBER.findall(text):
        try:
            found.add(float(raw.replace(",", "")))
        except ValueError:  # pragma: no cover - the regex cannot produce this
            continue
    return found


def _decimals(raw: str) -> int:
    _, _, fraction = raw.partition(".")
    return len(fraction)


def _matches(raw: str, observed: set[float]) -> bool:
    """True when a cited literal is a rounding of something actually observed."""
    try:
        cited = float(raw.replace(",", ""))
    except ValueError:  # pragma: no cover - the regex cannot produce this
        return False
    tolerance = 0.5 * (10 ** -_decimals(raw)) + 1e-9
    return any(abs(cited - value) <= tolerance for value in observed)


class EvidenceLedger:
    """Everything this read actually observed, and the arbiter of what it may cite."""

    def __init__(self) -> None:
        self._observations: list[ToolObservation] = []
        self._numbers: set[float] = set()
        self._tools: set[str] = set()

    def record(self, observation: ToolObservation) -> None:
        self._observations.append(observation)
        self._numbers |= extract_numbers(str(observation.args))
        if observation.ok:
            self._tools.add(observation.tool)
            self._numbers |= extract_numbers(observation.output)

    def record_calendar(self, name: str, detail: str) -> None:
        """The event-calendar path observes a window rather than a tool output."""
        self._tools.add("event-calendar")
        self._numbers |= extract_numbers(f"{name} {detail}")

    @property
    def observations(self) -> tuple[ToolObservation, ...]:
        return tuple(self._observations)

    @property
    def numbers(self) -> set[float]:
        return set(self._numbers)

    def successful_calls(self) -> int:
        return sum(1 for observation in self._observations if observation.ok)

    def failed_calls(self) -> int:
        return sum(1 for observation in self._observations if not observation.ok)

    def summary(self) -> list[dict[str, Any]]:
        """A compact record of the calls, for the journal row."""
        return [
            {
                "tool": observation.tool,
                "args": observation.args,
                "ok": observation.ok,
                "chars": len(observation.output),
                "truncated": observation.truncated,
                "latency_ms": observation.latency_ms,
            }
            for observation in self._observations
        ]

    def ungrounded_numbers(self, text: str) -> list[str]:
        """Every numeric literal in ``text`` that this ledger never saw."""
        return [raw for raw in _NUMBER.findall(text) if not _matches(raw, self._numbers)]


def validate_submission(
    submission: RegimeSubmission, ledger: EvidenceLedger, max_rationale_chars: int
) -> str | None:
    """Return a defect description, or ``None`` when the submission is grounded."""
    if len(submission.rationale) > max_rationale_chars:
        return (
            f"rationale is {len(submission.rationale)} characters, "
            f"over the {max_rationale_chars} cap"
        )

    unknown_tools = sorted(
        {item.tool for item in submission.evidence} - set(ledger._tools)  # noqa: SLF001
    )
    if unknown_tools:
        return f"evidence cites tools that returned nothing this read: {', '.join(unknown_tools)}"

    floating = ledger.ungrounded_numbers(submission.rationale)
    if floating:
        return f"rationale cites numbers absent from the evidence: {', '.join(floating)}"

    for item in submission.evidence:
        floating = ledger.ungrounded_numbers(item.value)
        if floating:
            return (
                f"evidence item {item.tool}.{item.field} cites "
                f"numbers absent from the tool output: {', '.join(floating)}"
            )
    return None
