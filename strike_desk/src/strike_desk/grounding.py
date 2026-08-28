"""Grounding: what the agent read, and the refusal to let it cite anything else."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

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

    def record_playbook(self, described: str) -> None:
        """The constraints the desk stated in the prompt are facts the agent was given.

        Without this, naming the band you failed — the most useful thing a refusal can do —
        would itself be an ungrounded citation.
        """
        self._tools.add("playbook")
        self._numbers |= extract_numbers(described)

    def text(self) -> str:
        """Every successful tool output concatenated, for non-numeric grounding checks."""
        return "\n".join(observation.output for observation in self._observations if observation.ok)

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


DIRECTIONS: tuple[str, ...] = ("bullish", "bearish")
OPTION_TYPES: tuple[str, ...] = ("CE", "PE")

#: The submitted fields that are themselves claims about the chain and must be grounded.
GROUNDED_FIELDS: tuple[str, ...] = (
    "strike",
    "bid",
    "ask",
    "delta",
    "implied_volatility",
    "open_interest",
    "lot_size",
)


class ProposalSubmission(BaseModel):
    """The one structured answer a proposal is allowed to end with."""

    direction: Literal["bullish", "bearish"] = Field(
        description="The directional view this contract expresses."
    )
    index_symbol: str = Field(min_length=1, description="The underlying index, e.g. 'NIFTY'.")
    expiry: str = Field(description="Contract expiry as an ISO date, YYYY-MM-DD.")
    strike: float = Field(gt=0, description="The strike price.")
    option_type: Literal["CE", "PE"] = Field(description="CE for a call, PE for a put.")
    symbol: str = Field(
        min_length=1, description="The exact tradable OpenAlgo symbol from get_option_symbol."
    )
    lot_size: int = Field(gt=0, description="Contract lot size as the broker reports it.")
    lots: int = Field(gt=0, description="Number of lots to buy.")
    quantity: int = Field(gt=0, description="lots x lot_size.")
    bid: float = Field(ge=0, description="Best bid as the chain reported it.")
    ask: float = Field(ge=0, description="Best ask as the chain reported it.")
    entry_price_low: float = Field(gt=0, description="Low of the acceptable entry band.")
    entry_price_high: float = Field(gt=0, description="High of the acceptable entry band.")
    delta: float = Field(description="Contract delta from get_option_greeks.")
    theta_per_day: float = Field(description="Theta per day per unit, negative for a long.")
    implied_volatility: float = Field(ge=0, description="Implied volatility in percent.")
    open_interest: int = Field(ge=0, description="Open interest as the chain reported it.")
    breakeven: float = Field(gt=0, description="Underlying level at which this trade breaks even.")
    stop_price: float = Field(gt=0, description="Premium at which the trade is abandoned.")
    target_price: float = Field(gt=0, description="Premium at which the trade is taken off.")
    time_stop_ist: str = Field(description="HH:MM IST after which the trade is abandoned.")
    rationale: str = Field(
        min_length=40,
        description="One paragraph a trader can read back in a month. Cite only fetched numbers.",
    )
    evidence: list[EvidenceItem] = Field(
        min_length=1, description="Every data point the proposal rests on."
    )

    @model_validator(mode="after")
    def _direction_matches_type(self) -> ProposalSubmission:
        expected = "CE" if self.direction == "bullish" else "PE"
        if self.option_type != expected:
            raise ValueError(f"direction {self.direction!r} requires {expected}")
        return self


class NoContractSubmission(BaseModel):
    """The other answer: the chain was read and held nothing worth buying."""

    reason: str = Field(
        min_length=20,
        description="Why no contract qualified, naming the constraint that bound.",
    )
    evidence: list[EvidenceItem] = Field(
        min_length=1, description="The readings that support the refusal."
    )


def validate_proposal(
    proposal: ProposalSubmission, ledger: EvidenceLedger, max_rationale_chars: int
) -> str | None:
    """Return a defect description, or ``None`` when the proposal is grounded."""
    if len(proposal.rationale) > max_rationale_chars:
        return (
            f"rationale is {len(proposal.rationale)} characters, over the {max_rationale_chars} cap"
        )

    unknown_tools = sorted(
        {item.tool for item in proposal.evidence} - set(ledger._tools)  # noqa: SLF001
    )
    if unknown_tools:
        return f"evidence cites tools that returned nothing this read: {', '.join(unknown_tools)}"

    floating = ledger.ungrounded_numbers(proposal.rationale)
    if floating:
        return f"rationale cites numbers absent from the evidence: {', '.join(floating)}"

    for item in proposal.evidence:
        floating = ledger.ungrounded_numbers(item.value)
        if floating:
            return (
                f"evidence item {item.tool}.{item.field} cites "
                f"numbers absent from the tool output: {', '.join(floating)}"
            )

    # The contract itself is a claim about the chain, not only the sentence describing it.
    for name in GROUNDED_FIELDS:
        floating = ledger.ungrounded_numbers(str(getattr(proposal, name)))
        if floating:
            return f"proposed {name} {getattr(proposal, name)} was never observed in a tool output"

    if proposal.symbol not in ledger.text():
        return f"proposed symbol {proposal.symbol!r} was never returned by a tool"
    return None


def validate_no_contract(
    submission: NoContractSubmission, ledger: EvidenceLedger, max_reason_chars: int
) -> str | None:
    """A refusal is a claim about the chain, and is grounded by the same rule."""
    if len(submission.reason) > max_reason_chars:
        return f"reason is {len(submission.reason)} characters, over the {max_reason_chars} cap"
    floating = ledger.ungrounded_numbers(submission.reason)
    if floating:
        return f"reason cites numbers absent from the evidence: {', '.join(floating)}"
    return None
