"""The decline taxonomy: every reason code the desk can record, with the category it
belongs to, the disposition it implies, and the sentence the trader reads."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .errors import StrikeDeskError

logger = logging.getLogger(__name__)

TAXONOMY_VERSION = "dt-5"

CATEGORY_BOOK = "book"
CATEGORY_CONTRACT = "contract"
CATEGORY_DATA = "data"
CATEGORY_REGIME = "regime"
CATEGORY_RISK = "risk"
CATEGORY_SPECIALIST = "specialist"
CATEGORY_SYSTEM = "system"
CATEGORY_UNKNOWN = "unknown"
CATEGORIES = frozenset(
    {
        CATEGORY_BOOK,
        CATEGORY_CONTRACT,
        CATEGORY_DATA,
        CATEGORY_REGIME,
        CATEGORY_RISK,
        CATEGORY_SPECIALIST,
        CATEGORY_SYSTEM,
    }
)

DISPOSITION_ROUTINE = "routine"
DISPOSITION_DEGRADED = "degraded"
DISPOSITION_DEFECT = "defect"
DISPOSITIONS = frozenset({DISPOSITION_ROUTINE, DISPOSITION_DEGRADED, DISPOSITION_DEFECT})

OUTCOMES = frozenset({"decline", "hold", "enter"})

RATIONALE_PREFIX = " Analyst: "
TRUNCATION_MARK = "..."
MIN_RATIONALE_ROOM = 24


class ReasonCodeUnknown(StrikeDeskError):
    """A reason code was rendered that this build's taxonomy does not define."""


@dataclass(frozen=True)
class ReasonEntry:
    """One reason code: what it means, how it is counted, how it reads."""

    code: str
    outcome: str
    category: str
    disposition: str
    summary: str
    templates: Mapping[str, str]

    def template(self, variant: str) -> str:
        """The sentence for a variant, falling back to the entry's default."""
        template = self.templates.get(variant)
        if template is None:
            logger.warning("reason %r has no %r variant; using its default", self.code, variant)
            return self.templates["default"]
        return template


def _entry(
    code: str,
    *,
    outcome: str,
    category: str,
    disposition: str,
    summary: str,
    **templates: str,
) -> ReasonEntry:
    return ReasonEntry(
        code=code,
        outcome=outcome,
        category=category,
        disposition=disposition,
        summary=summary,
        templates=MappingProxyType(dict(templates)),
    )


_ENTRIES: tuple[ReasonEntry, ...] = (
    _entry(
        "position-open",
        outcome="hold",
        category=CATEGORY_BOOK,
        disposition=DISPOSITION_ROUTINE,
        summary="a position is already open",
        default=(
            "Held: {count} open {index} position(s) ({symbols}). "
            "This tick manages the book; it does not add to it."
        ),
    ),
    _entry(
        "data-quality",
        outcome="decline",
        category=CATEGORY_DATA,
        disposition=DISPOSITION_DEGRADED,
        summary="the desk could not read what it needed",
        default=(
            "Declined: live data could not be read ({detail}). "
            "The desk does not act on a market it could not see."
        ),
        book=(
            "Declined: the book could not be read from OpenAlgo ({detail}). "
            "An unreadable book is never assumed flat."
        ),
        regime=(
            "Declined: the regime could not be read from live data ({detail}). "
            "The desk does not classify a market it could not see."
        ),
        chain=(
            "Declined: the option chain could not be read ({detail}). "
            "The desk does not price a contract it could not see."
        ),
    ),
    _entry(
        "specialist-unavailable",
        outcome="decline",
        category=CATEGORY_SPECIALIST,
        disposition=DISPOSITION_DEGRADED,
        summary="a specialist the tick needed was not usable",
        default="Declined: no usable '{role}' specialist ({detail}).",
        no_strategist=(
            "Declined: regime '{label}' is tradeable at {confidence} confidence, but no "
            "'{role}' specialist is registered to propose a contract. "
            "A regime read alone is never an entry."
        ),
        no_risk=(
            "Declined: {symbol} at {entry} was proposed and passed the playbook, but no "
            "'{role}' specialist is registered to adjudicate it. "
            "A proposal alone is never an entry."
        ),
    ),
    _entry(
        "specialist-timeout",
        outcome="decline",
        category=CATEGORY_SPECIALIST,
        disposition=DISPOSITION_DEGRADED,
        summary="a specialist did not answer in time",
        default="Declined: the '{role}' specialist did not answer within its timeout ({detail}).",
    ),
    _entry(
        "regime-not-tradeable",
        outcome="decline",
        category=CATEGORY_REGIME,
        disposition=DISPOSITION_ROUTINE,
        summary="the regime is not one this playbook trades",
        default="Declined: regime read as '{label}', which this playbook does not trade.",
    ),
    _entry(
        "regime-low-confidence",
        outcome="decline",
        category=CATEGORY_REGIME,
        disposition=DISPOSITION_ROUTINE,
        summary="the regime was tradeable but not confident enough",
        default=(
            "Declined: regime '{label}' is tradeable but confidence {confidence} "
            "is below the {floor} floor."
        ),
    ),
    _entry(
        "regime-ungrounded",
        outcome="decline",
        category=CATEGORY_REGIME,
        disposition=DISPOSITION_DEFECT,
        summary="the regime read cited data it never fetched",
        default=(
            "Declined: the regime read cited data it did not fetch ({detail}). "
            "An ungrounded read is a defect, not an opinion."
        ),
    ),
    _entry(
        "no-viable-contract",
        outcome="decline",
        category=CATEGORY_CONTRACT,
        disposition=DISPOSITION_ROUTINE,
        summary="the chain held nothing the playbook would buy",
        default=(
            "Declined: no contract in the {index} {expiry} chain met the playbook "
            "({detail}). A tradeable session is not a tradeable contract."
        ),
        non_directional=(
            "Declined: regime '{label}' is tradeable but not directional, and this "
            "playbook has no non-directional entry. "
            "A long that pays theta to wait is the trade this desk exists to refuse."
        ),
    ),
    _entry(
        "proposal-ungrounded",
        outcome="decline",
        category=CATEGORY_CONTRACT,
        disposition=DISPOSITION_DEFECT,
        summary="the proposal cited data it never fetched",
        default=(
            "Declined: the proposal cited data it did not fetch ({detail}). "
            "An ungrounded proposal is a defect, not a trade idea."
        ),
    ),
    _entry(
        "proposal-invalid",
        outcome="decline",
        category=CATEGORY_CONTRACT,
        disposition=DISPOSITION_DEFECT,
        summary="the proposal failed the playbook's own arithmetic",
        default=(
            "Declined: {symbol} failed {count} playbook check(s) ({detail}). "
            "A contract the desk cannot verify is never bought."
        ),
    ),
    _entry(
        "tick-timeout",
        outcome="decline",
        category=CATEGORY_SYSTEM,
        disposition=DISPOSITION_DEGRADED,
        summary="the tick ran out of its budget",
        default=(
            "Declined: the tick exceeded its {budget}s budget before a decision could be assembled."
        ),
    ),
    _entry(
        "internal-error",
        outcome="decline",
        category=CATEGORY_SYSTEM,
        disposition=DISPOSITION_DEFECT,
        summary="the tick raised before it could decide",
        default=(
            "Declined: the tick raised {error} before assembling a decision. "
            "The desk stays out when it cannot reason."
        ),
    ),
    _entry(
        "risk-session-stopped",
        outcome="decline",
        category=CATEGORY_RISK,
        disposition=DISPOSITION_ROUTINE,
        summary="the day's loss cap has stopped the session",
        default=(
            "Declined: the day is down {observed} against a {configured} daily loss cap. "
            "The desk stops proposing entries for the rest of the session."
        ),
    ),
    _entry(
        "risk-input-unavailable",
        outcome="decline",
        category=CATEGORY_RISK,
        disposition=DISPOSITION_DEGRADED,
        summary="a hard limit could not be evaluated",
        default=(
            "Declined: the {limit} limit could not be evaluated ({detail}). "
            "A proposal is held, never assumed safe."
        ),
    ),
    _entry(
        "risk-veto",
        outcome="decline",
        category=CATEGORY_RISK,
        disposition=DISPOSITION_ROUTINE,
        summary="a hard limit vetoed the proposal",
        default=(
            "Declined: {symbol} trips the {limit} limit - configured {configured}, "
            "observed {observed}. The veto is arithmetic and is not negotiated."
        ),
        sized_out=(
            "Declined: even one lot of {symbol} trips the {limit} limit - configured "
            "{configured}, observed {observed}. There is no size this desk may take."
        ),
        playbook_disagreed=(
            "Declined: {symbol} reduced to fit {limit} no longer passes the playbook "
            "({detail}). A contract two checks disagree about is never bought."
        ),
    ),
    _entry(
        "risk-cleared",
        outcome="enter",
        category=CATEGORY_RISK,
        disposition=DISPOSITION_ROUTINE,
        summary="the contract cleared every hard limit",
        default=(
            "Intent: buy {lots} lot(s) of {symbol} at up to {entry}, risking {risk} to the "
            "{stop} stop against a {base} capital base. This is an intent, not an order."
        ),
        reduced=(
            "Intent: buy {lots} lot(s) of {symbol} at up to {entry}, cut from {requested} "
            "lot(s) to fit the {limit} limit of {configured}. Risking {risk} to the {stop} "
            "stop. This is an intent, not an order."
        ),
    ),
    _entry(
        "approval-pending",
        outcome="hold",
        category=CATEGORY_BOOK,
        disposition=DISPOSITION_ROUTINE,
        summary="an intent is already waiting for the trader",
        default=(
            "Held: {quantity} x {symbol} is queued as pending order {pending_order_id} and is "
            "waiting for your approval. The desk proposes nothing while an intent is open."
        ),
    ),
    _entry(
        "approval-gate-unavailable",
        outcome="decline",
        category=CATEGORY_SYSTEM,
        disposition=DISPOSITION_DEFECT,
        summary="the human approval gate could not be verified",
        default=(
            "Declined: the approval gate could not be verified ({detail}). The desk does not "
            "propose a trade it has no safe way to place."
        ),
    ),
    _entry(
        "approval-queue-stale",
        outcome="hold",
        category=CATEGORY_SYSTEM,
        disposition=DISPOSITION_DEFECT,
        summary="an intent is past its deadline and has not been settled",
        default=(
            "Held: pending order {pending_order_id} passed its approval deadline at "
            "{deadline} and has still not been settled. The desk is holding on an intent "
            "nothing is resolving — read the log and clear the queue."
        ),
    ),
    _entry(
        "exit-path-gated",
        outcome="decline",
        category=CATEGORY_SYSTEM,
        disposition=DISPOSITION_DEFECT,
        summary="an exit would need a human, so no position may be opened",
        default=(
            "Declined: the exit path is gated ({detail}). The desk does not open a position "
            "it cannot close without someone clicking Approve."
        ),
    ),
    _entry(
        "autonomy-mode-mismatch",
        outcome="decline",
        category=CATEGORY_SYSTEM,
        disposition=DISPOSITION_DEFECT,
        summary="OpenAlgo's order mode contradicts the configured autonomy",
        default=(
            "Declined: {detail}. The desk will not submit until its autonomy and OpenAlgo's "
            "order mode say the same thing."
        ),
    ),
    _entry(
        "monitor-unavailable",
        outcome="decline",
        category=CATEGORY_SYSTEM,
        disposition=DISPOSITION_DEFECT,
        summary="unattended entry refused because nothing is watching",
        default=(
            "Declined: {detail}. Unattended, the desk opens nothing that the position "
            "monitor is not alive to close."
        ),
    ),
    _entry(
        "daily-loss-cap",
        outcome="decline",
        category=CATEGORY_RISK,
        disposition=DISPOSITION_ROUTINE,
        summary="the day's realised loss reached the unattended cap",
        default="Declined: {detail}. The desk is done for the day.",
    ),
    _entry(
        "daily-trade-cap",
        outcome="decline",
        category=CATEGORY_RISK,
        disposition=DISPOSITION_ROUTINE,
        summary="the day's unattended trade count reached its cap",
        default="Declined: {detail}. The desk is done for the day.",
    ),
)


def _validate(entries: tuple[ReasonEntry, ...]) -> Mapping[str, ReasonEntry]:
    """A malformed taxonomy is an import-time failure, not a runtime surprise."""
    registry: dict[str, ReasonEntry] = {}
    for item in entries:
        if item.code in registry:
            raise ValueError(f"duplicate reason code {item.code!r}")
        if item.outcome not in OUTCOMES:
            raise ValueError(f"{item.code!r}: outcome {item.outcome!r} is not a tick outcome")
        if item.category not in CATEGORIES:
            raise ValueError(f"{item.code!r}: category {item.category!r} is not a known category")
        if item.disposition not in DISPOSITIONS:
            raise ValueError(f"{item.code!r}: disposition {item.disposition!r} is not known")
        if not item.summary.strip():
            raise ValueError(f"{item.code!r}: needs a summary for the report")
        if "default" not in item.templates:
            raise ValueError(f"{item.code!r}: needs a 'default' sentence template")
        for name, template in item.templates.items():
            if not template.strip():
                raise ValueError(f"{item.code!r}: template {name!r} is empty")
        registry[item.code] = item
    return MappingProxyType(registry)


REASONS: Mapping[str, ReasonEntry] = _validate(_ENTRIES)


def compute_digest(entries: tuple[ReasonEntry, ...]) -> str:
    """A content digest over the taxonomy, so a wording change is attributable."""
    canonical = json.dumps(
        [
            {
                "code": item.code,
                "outcome": item.outcome,
                "category": item.category,
                "disposition": item.disposition,
                "summary": item.summary,
                "templates": dict(item.templates),
            }
            for item in entries
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


TAXONOMY_DIGEST = compute_digest(_ENTRIES)
TAXONOMY_ARTIFACT = f"{TAXONOMY_VERSION}+{TAXONOMY_DIGEST}"

_UNKNOWN_TEMPLATES: Mapping[str, str] = MappingProxyType({"default": "Declined: {detail}."})


def known_codes() -> frozenset[str]:
    """Every reason code this build can classify."""
    return frozenset(REASONS)


def entry(code: str) -> ReasonEntry:
    """The entry for a code. Raises when the code is not in the taxonomy."""
    try:
        return REASONS[code]
    except KeyError as exc:
        raise ReasonCodeUnknown(f"reason code {code!r} is not in the taxonomy") from exc


def entry_for(code: str) -> ReasonEntry:
    """Alias used by the position-monitor guardrail suite."""
    return entry(code)


def describe(code: str) -> ReasonEntry:
    """The entry for a code, or an ``unknown``/``defect`` placeholder for a foreign one.

    Reports read journals written by other releases, so classification at read time must
    never raise: an unrecognised code is surfaced as a defect rather than dropped.
    """
    found = REASONS.get(code)
    if found is not None:
        return found
    return ReasonEntry(
        code=code,
        outcome="decline",
        category=CATEGORY_UNKNOWN,
        disposition=DISPOSITION_DEFECT,
        summary="a reason code this build does not define",
        templates=_UNKNOWN_TEMPLATES,
    )


class _Defaulting(dict):
    """Renders a missing template field visibly rather than raising inside a tick."""

    def __missing__(self, key: str) -> str:
        logger.warning("reason template field %r was not supplied", key)
        return "unspecified"


def _fit(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    logger.warning("reason sentence exceeded %d characters and was truncated", max_chars)
    return text[: max_chars - len(TRUNCATION_MARK)].rstrip() + TRUNCATION_MARK


def render(
    code: str,
    *,
    max_chars: int,
    variant: str = "default",
    rationale: str | None = None,
    **fields: object,
) -> str:
    """The trader-facing sentence for one verdict: deterministic, one line, capped."""
    text = " ".join(entry(code).template(variant).format_map(_Defaulting(fields)).split())
    text = _fit(text, max_chars)
    clean = " ".join((rationale or "").split())
    if not clean:
        return text
    room = max_chars - len(text) - len(RATIONALE_PREFIX)
    if room < MIN_RATIONALE_ROOM:
        return text
    if len(clean) > room:
        clean = clean[: room - len(TRUNCATION_MARK)].rstrip() + TRUNCATION_MARK
    return f"{text}{RATIONALE_PREFIX}{clean}"
