# Iteration 04 — Implementation Guide: the Options Strategist

> **Document:** the build instructions for UC-04. Read `01_use_case.md` first for what this
> slice is and why; this file is how it is made. `03_manual_test_cases.md` is what you check
> by hand, `04_test_automation.md` is what CI checks forever, and `05_deployment_guide.md`
> releases it to the host.
>
> **Starting point:** the iteration-03 desk. `strike_desk/src/strike_desk/` holds
> `config.py`, `errors.py`, `events.py`, `journal.py`, `mcp_toolbox.py`, `grounding.py`,
> `model_client.py`, `prompt_registry.py`, `regime_analyst.py`, `decline_taxonomy.py`,
> `decline_report.py`, `graph.py`, `runner.py`, `service.py`, `session.py`, `specialists.py`,
> `book_state.py`, `observability.py`, `openalgo_client.py`, `__main__.py` and
> `prompts/regime_analyst.md`. The journal is at `SCHEMA_VERSION = 3` and the taxonomy at
> `dt-1`.
>
> **Ending point:** the same desk with a registered `strategist` role, a `proposals` table,
> a deterministic playbook, a second tool whitelist, a taxonomy at `dt-2`, and a
> `strike-desk proposals` command. Still no order path, still no `enter`.

## 1. What you are adding, and the shape of it

Eight files are new and eight are edited. The new ones are the substance of the slice:

| File | What it is |
| --- | --- |
| `playbook.py` | Every numeric constraint a contract must satisfy, as data plus one pure `check` function. Versioned and digested. |
| `options_strategist.py` | The agent: the loop, the two submissions, the span coverage, the row builder. |
| `proposal_view.py` | One object that renders a proposal row as text or as JSON, so the two cannot disagree. |
| `prompts/options_strategist.md` | The versioned prompt artifact. |
| `tests/chain_fixtures.py` | A recorded option chain, symbol resolution and Greeks response, so the playbook and the loop are testable with no broker session. |
| `tests/test_playbook.py` | The deterministic verifier, exhaustively. |
| `tests/test_options_strategist.py` | The loop, the whitelist, the grounding, the five statuses. |
| `tests/test_tick_proposals.py` | Classification and routing through a real tick. |

The edits are small and each has one reason:

| File | Edit |
| --- | --- |
| `config.py` | The strategist's model and budgets, the playbook's constants, `directional_regimes`, and a budget invariant that the tick can actually hold two specialists. |
| `errors.py` | `PlaybookViolation`. |
| `grounding.py` | `ProposalSubmission`, `NoContractSubmission`, `validate_proposal`. |
| `mcp_toolbox.py` | A second whitelist, a union load, a per-role `tools(role)`, and a forbidden-prefix assertion. |
| `model_client.py` | `build_strategist_model` and `strategist_cost_micros`. |
| `decline_taxonomy.py` | Three codes, one category, two variants, `dt-2`. Additive only. |
| `journal.py` | The `proposals` table, `record_proposal`, `list_proposals`, `SCHEMA_VERSION = 4`. |
| `graph.py` | A `propose` node, a router, four decision-table branches, three `REASON_*` constants. |
| `specialists.py` | `ROLE_RISK`. |
| `service.py` | Register the strategist beside the analyst, on the same toolbox. |
| `__main__.py` | The `proposals` command, and the playbook artifact in `status`. |

Build them in the order of the sections below. Each one compiles and its tests pass before
the next is written, and the graph is edited last because it is the only file where a
mistake is visible as a wrong decision rather than as an import error.

**One toolbox, two whitelists.** Resist the obvious shape of giving the strategist its own
`McpToolbox`. That object owns a subprocess, an event loop and a thread; a second instance
doubles all three for a tool surface that is the same server. The toolbox loads the union of
both whitelists once and hands each role its own slice, which keeps one subprocess, one
loop, one thread, and one place where a forbidden tool name is refused.

## 2. Prerequisites and pinned versions

Nothing new is installed. The strategist uses the same `langchain-anthropic`,
`langchain-mcp-adapters` and `langgraph` the analyst already uses; only the model id
changes, and that is configuration rather than a dependency. The one edit to
`strike_desk/pyproject.toml` is the package version.

### `strike_desk/pyproject.toml`

```toml
[project]
name = "strike-desk"
version = "0.4.0"
```

Everything else in that file stays as iteration 03 left it. Run `uv sync` after the bump so
`strike_desk/uv.lock` records the new version.

The host prerequisite is not a package. This slice reads the live option chain through
OpenAlgo's MCP tools, and those call `/api/v1/optionchain`, `/api/v1/quotes` and the master
contract tables. **A broker session that cannot serve `/quotes` cannot serve a chain**, so
the strategist's whole read path is unavailable until the broker is reconnected. That is
why `03_manual_test_cases.md` is split into an off-hours block that needs no broker at all
and a market-hours block that needs a live one.

## 3. The playbook

Start here, because it is the thing the model is optimising inside and the thing that
adjudicates what it returns. A playbook is a frozen dataclass of numbers plus one pure
function. It calls no tool, holds no state, and cannot fail in a way that depends on when it
runs — which is exactly what makes it the right adjudicator for a proposal a language model
wrote.

The constants are read from settings once at construction, so the environment is still the
single place a band is changed, and `digest` hashes the resolved values so a change to
`STRIKE_DESK_PLAYBOOK_DELTA_MIN` on the host moves the artifact stamped on every subsequent
proposal row. `describe` renders the constraints as the block of text the prompt embeds:
the model is told the numbers rather than left to infer them, which is what makes a
violation a defect rather than a misunderstanding.

`check` returns a list of violation strings — empty means the proposal stands. It never
raises and never rewrites the proposal. Every check names what it wanted and what it got,
because a violation that says only "delta out of band" is useless in a journal read a month
later. The checks are ordered from cheapest and most structural to most arithmetic, so the
first violation in the list is usually the informative one.

Two details are easy to get wrong. **Theta is negative for a long option**, so the budget
check takes its magnitude; a submission that reports theta as positive is not silently
accepted, it is caught by the sign check. And **the breakeven tolerance is absolute rupees,
not a percentage**, because a rounding of `24812.35 + 141.20` should be allowed to differ by
five paise and nothing more, whatever the strike happens to be.

### `strike_desk/src/strike_desk/playbook.py`

```python
"""The playbook: every numeric constraint a contract must satisfy, and the pure
function that decides whether one does. No tool call, no state, no model."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, time

from .config import Settings
from .grounding import ProposalSubmission

PLAYBOOK_VERSION = "pb-1"

#: How close a re-derived breakeven must be to the submitted one, in rupees.
BREAKEVEN_TOLERANCE = 0.05


@dataclass(frozen=True)
class Playbook:
    """The constraint set, resolved from settings once and then immutable."""

    delta_min: float
    delta_max: float
    max_spread_pct: float
    min_open_interest: int
    iv_floor: float
    iv_ceiling: float
    max_lots: int
    min_days_to_expiry: int
    max_days_to_expiry: int
    theta_budget_rupees: float
    time_stop: str

    @classmethod
    def from_settings(cls, settings: Settings) -> Playbook:
        return cls(
            delta_min=settings.playbook_delta_min,
            delta_max=settings.playbook_delta_max,
            max_spread_pct=settings.playbook_max_spread_pct,
            min_open_interest=settings.playbook_min_open_interest,
            iv_floor=settings.playbook_iv_floor,
            iv_ceiling=settings.playbook_iv_ceiling,
            max_lots=settings.playbook_max_lots,
            min_days_to_expiry=settings.playbook_min_days_to_expiry,
            max_days_to_expiry=settings.playbook_max_days_to_expiry,
            theta_budget_rupees=settings.playbook_theta_budget_rupees,
            time_stop=settings.playbook_time_stop,
        )

    @property
    def digest(self) -> str:
        """A content digest over the resolved constraints, so a band change is attributable."""
        canonical = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]

    @property
    def artifact(self) -> str:
        return f"{PLAYBOOK_VERSION}+{self.digest}"

    @property
    def time_stop_time(self) -> time:
        return time.fromisoformat(self.time_stop)

    def describe(self) -> str:
        """The constraints as the prompt states them. The model optimises inside numbers."""
        return "\n".join(
            (
                "- Direction and type must agree: bullish buys CE, bearish buys PE.",
                f"- |delta| must be between {self.delta_min:.2f} and {self.delta_max:.2f}.",
                f"- (ask - bid) / mid must be at most {self.max_spread_pct:.2f}%.",
                f"- Open interest must be at least {self.min_open_interest:,}.",
                f"- Implied volatility must be between {self.iv_floor:.1f}% "
                f"and {self.iv_ceiling:.1f}%.",
                f"- Lots must be between 1 and {self.max_lots}; "
                f"quantity must equal lots x lot_size.",
                f"- Expiry must be between {self.min_days_to_expiry} and "
                f"{self.max_days_to_expiry} calendar days away.",
                f"- Total theta cost per day (|theta| x quantity) must be at most "
                f"Rs {self.theta_budget_rupees:,.0f}.",
                f"- The time-stop must be later than now and no later than "
                f"{self.time_stop} IST.",
                "- Breakeven must equal strike + entry_price_high for a CE, "
                "strike - entry_price_high for a PE.",
                "- entry_price_low <= ask <= entry_price_high, "
                "stop_price < entry_price_low, target_price > entry_price_high.",
            )
        )


def _spread_pct(bid: float, ask: float) -> float | None:
    """The bid-ask spread as a percentage of mid, or ``None`` when mid is unusable."""
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return (ask - bid) / mid * 100.0


def check(
    proposal: ProposalSubmission,
    playbook: Playbook,
    *,
    now_ist: datetime,
) -> list[str]:
    """Re-derive every constraint from the proposal's own numbers.

    Returns the violations by name; an empty list means the contract is allowed. This is
    the desk's arithmetic, run against what the model asserted — the model's persuasion
    has no standing here.
    """
    violations: list[str] = []
    expected_type = "CE" if proposal.direction == "bullish" else "PE"
    if proposal.option_type != expected_type:
        violations.append(
            f"direction {proposal.direction!r} requires {expected_type}, "
            f"got {proposal.option_type}"
        )

    magnitude = abs(proposal.delta)
    if not playbook.delta_min <= magnitude <= playbook.delta_max:
        violations.append(
            f"|delta| {magnitude:.3f} is outside the "
            f"{playbook.delta_min:.2f}-{playbook.delta_max:.2f} band"
        )

    if proposal.bid <= 0 or proposal.ask <= 0:
        violations.append(f"quote is unusable: bid {proposal.bid}, ask {proposal.ask}")
    elif proposal.ask < proposal.bid:
        violations.append(f"ask {proposal.ask} is below bid {proposal.bid}")
    else:
        spread = _spread_pct(proposal.bid, proposal.ask)
        if spread is None:
            violations.append("mid price is zero or negative; spread is undefined")
        elif spread > playbook.max_spread_pct:
            violations.append(
                f"spread {spread:.2f}% exceeds the {playbook.max_spread_pct:.2f}% cap"
            )

    if proposal.open_interest < playbook.min_open_interest:
        violations.append(
            f"open interest {proposal.open_interest:,} is below the "
            f"{playbook.min_open_interest:,} floor"
        )

    if not playbook.iv_floor <= proposal.implied_volatility <= playbook.iv_ceiling:
        violations.append(
            f"implied volatility {proposal.implied_volatility:.2f}% is outside the "
            f"{playbook.iv_floor:.1f}-{playbook.iv_ceiling:.1f}% band"
        )

    if not 1 <= proposal.lots <= playbook.max_lots:
        violations.append(f"lots {proposal.lots} is outside 1-{playbook.max_lots}")
    if proposal.quantity != proposal.lots * proposal.lot_size:
        violations.append(
            f"quantity {proposal.quantity} is not lots {proposal.lots} "
            f"x lot size {proposal.lot_size}"
        )

    premium = proposal.entry_price_high
    expected_breakeven = (
        proposal.strike + premium if proposal.option_type == "CE" else proposal.strike - premium
    )
    if abs(proposal.breakeven - expected_breakeven) > BREAKEVEN_TOLERANCE:
        violations.append(
            f"breakeven {proposal.breakeven:.2f} does not equal "
            f"{expected_breakeven:.2f} for a {proposal.option_type} at strike "
            f"{proposal.strike:.2f} paying {premium:.2f}"
        )

    if proposal.entry_price_low > proposal.entry_price_high:
        violations.append(
            f"entry band {proposal.entry_price_low:.2f}-{proposal.entry_price_high:.2f} "
            "is inverted"
        )
    elif not proposal.entry_price_low <= proposal.ask <= proposal.entry_price_high:
        violations.append(
            f"ask {proposal.ask:.2f} is outside the entry band "
            f"{proposal.entry_price_low:.2f}-{proposal.entry_price_high:.2f}"
        )
    if proposal.stop_price >= proposal.entry_price_low:
        violations.append(
            f"stop {proposal.stop_price:.2f} is not below the entry band low "
            f"{proposal.entry_price_low:.2f}"
        )
    if proposal.target_price <= proposal.entry_price_high:
        violations.append(
            f"target {proposal.target_price:.2f} is not above the entry band high "
            f"{proposal.entry_price_high:.2f}"
        )

    try:
        expiry = date.fromisoformat(proposal.expiry)
    except ValueError:
        violations.append(f"expiry {proposal.expiry!r} is not an ISO date")
    else:
        days = (expiry - now_ist.date()).days
        if not playbook.min_days_to_expiry <= days <= playbook.max_days_to_expiry:
            violations.append(
                f"expiry is {days} day(s) away, outside "
                f"{playbook.min_days_to_expiry}-{playbook.max_days_to_expiry}"
            )

    if proposal.theta_per_day > 0:
        violations.append(
            f"theta {proposal.theta_per_day:.2f} is positive; a long option decays"
        )
    theta_cost = abs(proposal.theta_per_day) * proposal.quantity
    if theta_cost > playbook.theta_budget_rupees:
        violations.append(
            f"theta cost Rs {theta_cost:,.0f}/day exceeds the "
            f"Rs {playbook.theta_budget_rupees:,.0f} budget"
        )

    try:
        stop_at = time.fromisoformat(proposal.time_stop_ist)
    except ValueError:
        violations.append(f"time-stop {proposal.time_stop_ist!r} is not HH:MM")
    else:
        if stop_at <= now_ist.time():
            violations.append(f"time-stop {proposal.time_stop_ist} is not in the future")
        if stop_at > playbook.time_stop_time:
            violations.append(
                f"time-stop {proposal.time_stop_ist} is later than the "
                f"{playbook.time_stop} session cutoff"
            )

    return violations


__all__ = ["BREAKEVEN_TOLERANCE", "PLAYBOOK_VERSION", "Playbook", "check"]
```

## 4. Grounding grows a submission

`grounding.py` already owns the evidence ledger, the citation rule and the analyst's
submission schema. The strategist's two submissions belong in the same file for one reason:
they are validated against the same ledger by the same rule, and splitting them would give
you two places where "may cite no number the agent did not fetch" is implemented.

`ProposalSubmission` is deliberately wide. Every field the acceptance criteria name is a
field, none is optional, and none is free text except the rationale. That is what lets
`playbook.check` be a pure function over the submission rather than a parser of prose. The
`direction ↔ option_type` agreement is asserted twice on purpose: once here as the model's
contract, so an obviously wrong submission fails schema validation before any arithmetic
runs, and once in the playbook, because a schema is a convenience and the playbook is the
authority.

`NoContractSubmission` is small and carries evidence, because a refusal is a claim about the
chain and a claim about the chain must be grounded exactly as a proposal is. "Nothing was
liquid enough" is only credible if the agent read the liquidity.

`validate_proposal` reuses the ledger's `ungrounded_numbers` over the rationale and every
evidence value, exactly as `validate_submission` does — with one addition. A proposal's
*structured numeric fields* are also claims about the chain, so the strike, the bid, the
ask, the delta, the IV and the open interest must each appear in the ledger too. That is the
check that stops a model from citing correct numbers in its sentence while quietly inventing
the contract underneath it.

### `strike_desk/src/strike_desk/grounding.py` — additions

```python
# Append to the existing module. LABELS, TRADEABLE_LABELS, EvidenceItem, RegimeSubmission,
# ToolObservation, EvidenceLedger, extract_numbers and validate_submission are unchanged.

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
            f"rationale is {len(proposal.rationale)} characters, "
            f"over the {max_rationale_chars} cap"
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
```

Two supporting edits inside the existing code. `model_validator` joins the pydantic import
at the top of the file:

```python
from pydantic import BaseModel, Field, model_validator
```

And `EvidenceLedger` gains two methods. The first exists because `validate_proposal` needs to
ask whether a *symbol* — a string, not a number — was ever returned:

```python
    def text(self) -> str:
        """Every successful tool output concatenated, for non-numeric grounding checks."""
        return "\n".join(
            observation.output for observation in self._observations if observation.ok
        )
```

The second closes a gap that only appears once the strategist starts refusing. A refusal is
most useful when it names the band it failed — "open interest away from the money is under
50,000" is worth more than "liquidity was thin". But `50,000` is a *playbook constant*: the
desk stated it in the prompt, and no tool ever returned it. Under the citation rule as
iteration 02 wrote it, naming the constraint you failed makes your explanation ungrounded,
which is exactly backwards.

So the ledger records what the desk itself supplied, the same way `record_calendar` records a
window the desk supplied rather than a tool output. The rule is unchanged in substance — an
agent may cite only what it was actually given — and the set of things it was given now
correctly includes the constraints it was asked to work inside:

```python
    def record_playbook(self, described: str) -> None:
        """The constraints the desk stated in the prompt are facts the agent was given.

        Without this, naming the band you failed — the most useful thing a refusal can do —
        would itself be an ungrounded citation.
        """
        self._tools.add("playbook")
        self._numbers |= extract_numbers(described)
```

The strategist calls it once, before the first model round, from the same string the prompt
embeds — so the numbers in the ledger and the numbers the model was shown are the same
numbers by construction:

```python
        ledger = EvidenceLedger()
        ledger.record_playbook(self._playbook.describe())
```

Note what this does **not** loosen. The chain, the Greeks and the symbol are still bound by
`GROUNDED_FIELDS` and by `ledger.text()`, and those come only from tool output. A model can
now say "the 50,000 floor bound", and still cannot say the contract had open interest it
never read.

## 5. The toolbox grows a second whitelist

`mcp_toolbox.py` currently selects exactly `REGIME_TOOLS` and hands every caller the same
list. It now loads the union of both whitelists and hands each role its own slice.

The forbidden-prefix assertion is the structural half of AC-5 and it runs at import, not at
call time. OpenAlgo's MCP server exposes `place_options_order`, `place_options_multi_order`,
`placeorder`, `modifyorder`, `cancelorder`, `close_all_positions` and more on the same
session the desk is already holding open. Nothing stops a future edit from adding one of
those to a whitelist by accident — except a check that refuses to import the module if a
whitelisted name begins with a mutating verb. A typo becomes a service that will not start,
which is the correct failure for a list that is the only thing standing between a proposal
and an order.

Note what the strategist's list does **not** contain: `get_historical_data` and
`get_volatility_snapshot`. Those are the analyst's instruments for classifying a session,
and the strategist's job is to price a contract in the session the analyst already
classified. Keeping them out is not a safety property, it is a cost and focus one — fewer
tools, fewer rounds, a smaller prompt.

### `strike_desk/src/strike_desk/mcp_toolbox.py` — edits

```python
#: Everything the Regime Analyst may reach. Unchanged from iteration 02.
REGIME_TOOLS: tuple[str, ...] = (
    "get_quote",
    "get_historical_data",
    "get_trend_snapshot",
    "get_momentum_snapshot",
    "get_volatility_snapshot",
    "get_expiry_dates",
)

#: Everything the Options Strategist may reach. It reads the chain, resolves a symbol and
#: prices the Greeks; it forms its own directional view from trend and momentum. Nothing
#: here can place, modify, cancel or square off an order, and nothing here can send a
#: message.
STRATEGIST_TOOLS: tuple[str, ...] = (
    "get_quote",
    "get_expiry_dates",
    "get_option_chain",
    "get_option_symbol",
    "get_option_greeks",
    "get_trend_snapshot",
    "get_momentum_snapshot",
)

#: The union, loaded once from one session.
REQUIRED_TOOLS: tuple[str, ...] = tuple(sorted(set(REGIME_TOOLS) | set(STRATEGIST_TOOLS)))

TOOLS_BY_ROLE: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {ROLE_REGIME: REGIME_TOOLS, ROLE_STRATEGIST: STRATEGIST_TOOLS}
)

#: A whitelisted tool name may never begin with a verb that changes state at the broker.
FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "place",
    "modify",
    "cancel",
    "close",
    "square",
    "send",
    "set",
    "toggle",
)


def _assert_read_only(names: tuple[str, ...]) -> None:
    """Import-time guardrail: a mutating tool cannot reach a whitelist by accident."""
    offenders = sorted(
        name for name in names if name.startswith(FORBIDDEN_PREFIXES)
    )
    if offenders:
        raise ValueError(
            f"whitelisted tools must be read-only; these are not: {', '.join(offenders)}"
        )


_assert_read_only(REQUIRED_TOOLS)


def select_tools(loaded: list[BaseTool]) -> list[BaseTool]:
    """Keep exactly the union whitelist, in order, and fail closed if one is absent."""
    by_name = {tool.name: tool for tool in loaded}
    missing = [name for name in REQUIRED_TOOLS if name not in by_name]
    if missing:
        raise McpUnavailable(f"MCP server is missing required tools: {', '.join(missing)}")
    return [by_name[name] for name in REQUIRED_TOOLS]


def tools_for_role(loaded: list[BaseTool], role: str) -> list[BaseTool]:
    """The slice of the loaded union that one role may reach."""
    allowed = TOOLS_BY_ROLE.get(role)
    if allowed is None:
        raise McpUnavailable(f"no tool whitelist is defined for role {role!r}")
    by_name = {tool.name: tool for tool in loaded}
    return [by_name[name] for name in allowed if name in by_name]
```

The `ToolSource` protocol and `McpToolbox.tools` both take the role now:

```python
class ToolSource(Protocol):
    """What a specialist needs from whatever holds its tools."""

    def ensure_started(self) -> None: ...

    def tools(self, role: str) -> list[BaseTool]: ...

    def submit(self, factory: Callable[[], Coroutine[Any, Any, Any]], timeout: float) -> Any: ...
```

```python
    def tools(self, role: str) -> list[BaseTool]:
        with self._lock:
            if not self._tools:
                raise McpUnavailable("no MCP tools are loaded")
            loaded = list(self._tools)
        selected = tools_for_role(loaded, role)
        if not selected:
            raise McpUnavailable(f"no tools are loaded for role {role!r}")
        return selected
```

Add the three imports the new code needs, beside the existing ones:

```python
from collections.abc import Callable, Coroutine, Mapping
from types import MappingProxyType

from .specialists import ROLE_REGIME, ROLE_STRATEGIST
```

And the one call site inside `regime_analyst.py` gains its role:

```python
        tools = self._tool_source.tools(ROLE_REGIME)
```

## 6. The taxonomy moves to dt-2, additively

Three codes, one category, two variants, and **nothing existing changes**. That constraint is
not stylistic. Amit's host journal holds rows written under `dt-1`, and iteration 03's report
classifies every row by resolving its code through today's taxonomy and counting a
disagreement as `taxonomy drift`. Change `regime-not-tradeable` from `regime`/`routine` to
anything else and every historical row carrying it becomes drift the moment the report runs.
Adding entries and adding variants cannot do that, because no existing row carries a code or
a category that did not exist when it was written.

The new category is `contract`: *the chain itself was the thing that stopped the trade*. It
is genuinely distinct from `regime` (the session was wrong), `data` (we could not read) and
`specialist` (the agent was not usable) — the session was right, the read succeeded, the
agent worked, and there was still nothing worth buying.

`no-viable-contract` is `routine`, and that is the most important disposition decision in
the slice. Most ticks that reach the strategist will end here. If it were `degraded`, the
health block would report a desk in permanent distress; if it were `defect`, `strike-desk
declines` would exit 2 every day. It is the playbook working.

Adding the `no_risk` variant and the `chain` variant moves `TAXONOMY_DIGEST`, because
`compute_digest` hashes `dict(item.templates)`. That is expected: the version moves to `dt-2`
deliberately, the golden file is regenerated deliberately, and the digest change is the
audit trail for both.

### `strike_desk/src/strike_desk/decline_taxonomy.py` — edits

```python
TAXONOMY_VERSION = "dt-2"

CATEGORY_BOOK = "book"
CATEGORY_CONTRACT = "contract"
CATEGORY_DATA = "data"
CATEGORY_REGIME = "regime"
CATEGORY_SPECIALIST = "specialist"
CATEGORY_SYSTEM = "system"
CATEGORY_UNKNOWN = "unknown"
CATEGORIES = frozenset(
    {
        CATEGORY_BOOK,
        CATEGORY_CONTRACT,
        CATEGORY_DATA,
        CATEGORY_REGIME,
        CATEGORY_SPECIALIST,
        CATEGORY_SYSTEM,
    }
)
```

Two existing entries gain a variant. Their code, category, disposition and `default`
template are untouched:

```python
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
```

Three entries are added. Put them after `regime-ungrounded` so the tuple reads in tick order:

```python
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
```

## 7. The journal grows a table

`SCHEMA_VERSION` moves to 4 and one table is added. There is no `ALTER TABLE` in this slice
and no column added to an existing table, which makes the migration the simplest kind the
journal can have: `create_schema()` calls `create_all`, SQLAlchemy issues
`CREATE TABLE IF NOT EXISTS`, and the `after_create` DDL listener attaches the same
append-only `UPDATE` and `DELETE` triggers the other three tables carry. No existing row is
read, rewritten or locked.

The rollback story follows from that. An iteration-03 binary opening a widened database
simply never selects from `proposals` and never inserts into it; every one of its own writes
is still valid. That is a strictly easier rollback than iteration 03's, and the deployment
guide says so.

`playbook_verdict` and `violations_json` are stored on the row rather than recomputed. That
is deliberate and it is the opposite of the choice iteration 03 made for `reason_category`.
A category is derivable from a code at read time, so storing it is a convenience; a playbook
verdict is derivable only from the playbook *as it was configured when the proposal was
made*, and the bands are environment variables that change between releases. Re-deriving it
later would answer a different question. The `playbook_artifact` column is what makes the
stored verdict attributable.

### `strike_desk/src/strike_desk/journal.py` — edits

```python
SCHEMA_VERSION = 4


class Proposal(Base):
    """One row per contract proposal attempt. Never updated, never deleted."""

    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    tick_id: Mapped[str] = mapped_column(String(48), index=True, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trading_day: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    index_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)  # tick | cli
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    regime_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    regime_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- the contract, null on every status except `proposed` ------------------
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    expiry: Mapped[str | None] = mapped_column(String(10), nullable=True)
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(2), nullable=True)
    lots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lot_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entry_price_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_price_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    theta_per_day: Mapped[float | None] = mapped_column(Float, nullable=True)
    implied_volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    open_interest: Mapped[int | None] = mapped_column(Integer, nullable=True)
    breakeven: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_stop_ist: Mapped[str | None] = mapped_column(String(5), nullable=True)

    # --- the reasoning, the verdict and the receipts ---------------------------
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    playbook_artifact: Mapped[str] = mapped_column(String(32), nullable=False)
    playbook_verdict: Mapped[str] = mapped_column(String(16), nullable=False)  # pass | fail | n/a
    violations_json: Mapped[str] = mapped_column(Text, nullable=False)
    defect: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_tool_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_cost_micros: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_name: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_set_version: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=SCHEMA_VERSION)
```

The append-only trigger loop takes the new table:

```python
for _table in (Decision.__table__, TraceSpan.__table__, RegimeRead.__table__, Proposal.__table__):
```

And three repository methods join the class:

```python
    def record_proposal(self, **fields: Any) -> str:
        """Append one proposal. Raises JournalWriteError so the tick fails closed."""
        try:
            with self.session_scope() as session:
                session.add(Proposal(**fields))
        except SQLAlchemyError as exc:
            raise JournalWriteError(
                f"could not append proposal: {exc.__class__.__name__}"
            ) from exc
        return str(fields["proposal_id"])

    def list_proposals(self, trading_day: str, limit: int = 200) -> Sequence[Proposal]:
        with self.session_scope() as session:
            statement = (
                select(Proposal)
                .where(Proposal.trading_day == trading_day)
                .order_by(Proposal.created_at_utc.asc())
                .limit(limit)
            )
            return list(session.execute(statement).scalars())

    def recent_proposal_days(self, days: int) -> list[str]:
        """The most recent trading days that hold at least one proposal, oldest first."""
        with self.session_scope() as session:
            statement = (
                select(Proposal.trading_day)
                .distinct()
                .order_by(Proposal.trading_day.desc())
                .limit(days)
            )
            found = [str(row) for row in session.execute(statement).scalars()]
        return sorted(found)
```

## 8. The prompt artifact

The prompt states the playbook's numbers rather than describing them, by interpolating
`playbook.describe()` at request-build time. That is what makes a playbook violation a
defect: the model was told the band, in the units the band is measured in, in the same
message it was asked to work in.

Note the instruction about the two submissions. The single most common failure mode in a
loop like this is a model that keeps fetching in the hope of finding something acceptable
and then runs out of rounds, producing a `degraded` row that means nothing. Telling it
explicitly that refusing is a first-class answer, and that a refusal is not a failed task,
is what turns that into an honest `no-viable-contract`.

### `strike_desk/src/strike_desk/prompts/options_strategist.md`

```markdown
---
name: options_strategist
version: v1
model_tier: sonnet
---
You are the Options Strategist of a disciplined intraday index options-buying desk. The
Regime Analyst has already classified the session; your one job is to turn that regime into
exactly one concrete long CE or PE proposal, grounded entirely in the chain and Greeks you
read in this call — or to say plainly that no contract qualifies.

You propose. You never place. You have no tool that can place, modify, cancel or square off
an order, and you must not describe an order as placed, pending or filled.

## The two answers

Finish by calling exactly one of these, exactly once:

- `submit_proposal` — you found one contract that meets every constraint below.
- `submit_no_viable_contract` — you read the chain and nothing met them.

**A refusal is a correct answer, not a failed task.** Most sessions do not offer a contract
worth buying: spreads are wide, open interest is thin, or premium is priced for a move that
has already happened. Say so, name the constraint that bound, and cite the readings that
show it. Do not stretch a constraint to produce a proposal, and do not spend rounds hunting
for one when the chain has already told you the answer.

## The constraints

These are the playbook. They are checked again by deterministic code after you submit, from
the numbers you cite, so a proposal that misses one is rejected as a defect rather than
re-priced.

{constraints}

## How to work

1. Resolve the expiry with `get_expiry_dates` before you quote or price anything. Never
   assume a date.
2. Form your own directional view from `get_trend_snapshot` and `get_momentum_snapshot`.
   The regime you were given says what kind of session it is, not which way it is going.
   If the two disagree with each other, refuse rather than pick a side.
3. Read the chain around the money with `get_option_chain`, using `strike_count` to keep it
   small. The chain carries bid, ask, open interest and lot size.
4. Narrow to one strike, then call `get_option_symbol` for the exact tradable symbol and its
   lot size, and `get_option_greeks` for that contract's delta, theta and implied volatility.
5. Ground every claim. You may cite a number only if a tool returned it in this call. Do not
   recall values from memory, do not interpolate between strikes, and do not compute a
   premium you did not read. Derived arithmetic — breakeven, quantity, theta cost — is
   expected and is recomputed from your cited inputs.
6. Never answer in prose.

## Tools

- `get_expiry_dates` — the available option or futures expiries. Call this first.
- `get_option_chain` — CE/PE per strike with LTP, bid, ask, OHLC, volume, open interest,
  lot size and moneyness. Use `strike_count` to limit to N strikes around ATM.
- `get_option_symbol` — resolve an ATM/ITM/OTM offset to the exact symbol plus lot size,
  tick size and underlying LTP.
- `get_option_greeks` — delta, gamma, theta, vega and implied volatility for one symbol.
- `get_quote` — spot for the index or a single contract.
- `get_trend_snapshot` — SMA/EMA stack, Supertrend, ADX/DMI, Ichimoku in one call.
- `get_momentum_snapshot` — RSI, MACD, Stochastic, CCI, Williams %R in one call.

## The submission

- Every numeric field must be a value a tool returned, or arithmetic over such values.
- `entry_price_low` and `entry_price_high` must bracket the current ask; the high is the
  premium the breakeven is derived from.
- `stop_price` is below the entry band, `target_price` above it, both in premium terms.
- `time_stop_ist` is HH:MM, later than now and no later than the session cutoff.
- `rationale` — one paragraph, at most the configured cap. Name the direction and why, the
  strike and why that strike, the liquidity that makes it tradable, and what would make you
  wrong. Cite only numbers you fetched.
- `evidence` — every data point the proposal rests on, each with the tool that produced it,
  the field name, and the value exactly as reported.
```

## 9. The strategist

The agent is `regime_analyst.py`'s shape with three differences, and holding it to that
shape is deliberate: two agents that loop differently are two loops to debug.

**Two submission tools instead of one.** Both are bound on every round and both are
intercepted rather than executed. The final round binds only the two submissions with
`tool_choice: any`, so a model that has spent its rounds is forced to answer one way or the
other rather than fetching into the void.

**A verification pass after grounding.** The analyst validates citation and stops. The
strategist validates citation and then runs `playbook.check`, and the two failures are
different statuses with different reason codes and different dispositions —
`ungrounded` means it cited what it did not read, `invalid` means it read correctly and
proposed something the desk will not buy.

**A rejected-tool counter.** The analyst records a whitelist rejection on the span. The
strategist also counts them onto the row, because "the model tried to reach a tool it does
not have" is the single most important thing to be able to grep for in an agent that sits
this close to an order path.

### `strike_desk/src/strike_desk/options_strategist.py`

```python
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

VERDICT_PASS = "pass"
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
```

The model tier is two small functions in `model_client.py`. The strategist gets its own
prices because Sonnet is not Haiku and a shared price would make the day's cost meaningless:

```python
def build_strategist_model(settings: Settings) -> ChatAnthropic:
    """Claude Sonnet 5 — the deliberation tier. Temperature 0: a chooser, not a writer."""
    if settings.anthropic_api_key is None:
        raise ModelCallFailed("STRIKE_DESK_ANTHROPIC_API_KEY is not configured")
    return ChatAnthropic(
        model=settings.strategist_model,
        temperature=settings.strategist_temperature,
        max_tokens=settings.strategist_max_output_tokens,
        timeout=settings.strategist_deadline_seconds,
        max_retries=1,
        stop=None,
        api_key=settings.anthropic_api_key,
    )


def strategist_cost_micros(settings: Settings, input_tokens: int, output_tokens: int) -> int:
    """Cost in USD micro-dollars at the strategist's own tier prices."""
    return round(
        input_tokens * settings.strategist_price_in_per_mtok
        + output_tokens * settings.strategist_price_out_per_mtok
    )
```

## 10. Wiring it into the tick

`specialists.py` gains one line, because the decision table needs a name for the role it is
about to say is missing:

```python
ROLE_REGIME = "regime"
ROLE_STRATEGIST = "strategist"
ROLE_RISK = "risk"
```

The graph gains a `propose` node, one router, four decision-table branches and three
constants. The router is where AC-6 is enforced, and it is worth reading closely: the
strategist is reached only when there is no error, no budget problem, a label that is
tradeable, a confidence at or above the floor, and a label in the directional set. Anything
else falls through to `decide` without spending a token.

### `strike_desk/src/strike_desk/graph.py` — edits

New constants beside the existing `REASON_*` block:

```python
REASON_NO_VIABLE_CONTRACT = "no-viable-contract"
REASON_PROPOSAL_UNGROUNDED = "proposal-ungrounded"
REASON_PROPOSAL_INVALID = "proposal-invalid"
```

New `TickState` keys:

```python
    proposal: dict[str, Any] | None
    proposal_status: str | None
    proposal_detail: str | None
    proposal_violations: list[str] | None
```

The router and the node. `_wants_a_contract` is module-level and takes its settings as an
argument, so it is testable without building a graph; `route_after_consult` and `propose` are
**nested inside `build_tick_graph`** beside `plan`, `consult`, `decide` and `persist`, because
they close over `deps` and `tracer` exactly as those do:

```python
# module level, beside _decide_outcome
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


# nested inside build_tick_graph, beside plan / consult / decide / persist
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
            "strategy.symbol", str((result.payload.get("proposal") or {}).get("symbol") or "none")
        )
        span.set_attribute("strategy.playbook_verdict", str(result.payload.get("playbook_verdict")))
        span.set_attribute("strategy.token_cost_micros", int(result.token_cost_micros))
        return update
```

`_record_proposal` sits beside `_record_read` and has the same job — append the row, then
translate the status into tick state:

```python
def _record_proposal(deps: TickDeps, state: TickState, result: SpecialistResult) -> dict[str, Any]:
    """Append the proposal, then translate its status into tick state."""
    payload = dict(result.payload)
    deps.journal.record_proposal(
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
    }
```

The decision table gains its four branches, placed after the low-confidence check and
replacing the old unconditional `no_strategist` return:

```python
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
```

Finally the builder wires the node in. `consult` no longer edges straight to `decide`:

```python
    builder.add_node("propose", propose)
    builder.add_conditional_edges(
        "consult", route_after_consult, {"propose": "propose", "decide": "decide"}
    )
    builder.add_edge("propose", "decide")
```

Import the four status constants at the top of `graph.py` under names that do not collide
with the analyst's:

```python
from .options_strategist import (
    STATUS_DEGRADED as STRATEGY_DEGRADED,
    STATUS_INVALID as STRATEGY_INVALID,
    STATUS_NO_CONTRACT as STRATEGY_NO_CONTRACT,
    STATUS_UNGROUNDED as STRATEGY_UNGROUNDED,
    build_proposal_row,
)
from .specialists import ROLE_RISK
```

`service.py` registers the strategist on the same toolbox. Note that the toolbox is built
once and both specialists share it — this is the FD-hygiene point from §1, and it is also
why `_register_specialists` builds the analyst first and returns early on a missing key:

```python
    def _register_specialists(self) -> None:
        """Stand up the reasoning plane, or run without it and decline every tick."""
        if self._settings.anthropic_api_key is None:
            logger.warning(
                "no Anthropic API key configured — the desk will tick and decline with "
                "specialist-unavailable until one is set"
            )
            return
        toolbox = McpToolbox(self._settings)
        specialists = []
        try:
            specialists.append(build_regime_analyst(self._settings, self._prompts, toolbox))
        except (ModelCallFailed, PromptNotFound):
            logger.exception("could not build the regime analyst — running without one")
        try:
            specialists.append(build_options_strategist(self._settings, self._prompts, toolbox))
        except (ModelCallFailed, PromptNotFound):
            logger.exception("could not build the options strategist — running without one")
        if not specialists:
            toolbox.close()
            return
        try:
            toolbox.start()
        except McpUnavailable:
            logger.exception("MCP session unavailable at startup — it will retry on each read")
        self._toolbox = toolbox
        for specialist in specialists:
            self.registry.register(specialist)
```

One more line in `service.py`: the specialist executor is sized for the specialists that can
run at once. They run sequentially inside a tick, so `max_workers=2` in `specialists.py`
still holds and needs no change — but the log line at startup should say which roles are
live, which it already does via `self.registry.registered_roles()`.

## 11. Configuration and the command

Three groups of settings arrive, and one existing default changes.

**The budget arithmetic is the change that matters.** Iteration 03 shipped
`tick_budget_seconds = 40` with one specialist that could take 22 seconds. Two specialists
can now want 22 + 31 = 53 seconds inside that budget, and because every node calls
`_over_budget` first, what you would actually see in production is a good regime read
followed by a `tick-timeout` decline — a `system`/`degraded` row that looks like an
infrastructure problem and is really an arithmetic one. So `tick_budget_seconds` moves to 90
and a model validator refuses a configuration where the two specialist timeouts do not fit
inside it. The invariant is stated once, in code, at startup:

```
specialist_timeout_seconds + strategist_timeout_seconds < tick_budget_seconds
                     25    +            35             <         90          ✓
analyst deadline   = 25 - 3 = 22      strategist deadline = 35 - 4 = 31
```

### `strike_desk/src/strike_desk/config.py` — edits

```python
from pydantic import Field, SecretStr, field_validator, model_validator
```

```python
    # --- Tick cadence and budgets ------------------------------------------
    tick_interval_seconds: int = Field(default=900, ge=30, le=3600)
    tick_budget_seconds: float = Field(default=90.0, gt=0, le=300)
    specialist_timeout_seconds: float = Field(default=25.0, gt=0, le=60)
    strategist_timeout_seconds: float = Field(default=35.0, gt=0, le=120)

    # --- Supervisor policy --------------------------------------------------
    min_regime_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    directional_regimes: str = "trending"

    # --- Options Strategist (deliberation plane) ----------------------------
    strategist_model: str = "claude-sonnet-5"
    strategist_temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    strategist_max_output_tokens: int = Field(default=2400, ge=512, le=16384)
    strategist_max_rounds: int = Field(default=6, ge=2, le=12)
    strategist_deadline_margin_seconds: float = Field(default=4.0, ge=0.5, le=20.0)
    strategist_tool_output_chars: int = Field(default=16000, ge=2000, le=80000)
    proposal_rationale_max_chars: int = Field(default=700, ge=200, le=2000)
    strategist_price_in_per_mtok: float = Field(default=3.0, ge=0.0, le=1000.0)
    strategist_price_out_per_mtok: float = Field(default=15.0, ge=0.0, le=1000.0)

    # --- Playbook -----------------------------------------------------------
    playbook_delta_min: float = Field(default=0.35, gt=0.0, lt=1.0)
    playbook_delta_max: float = Field(default=0.60, gt=0.0, le=1.0)
    playbook_max_spread_pct: float = Field(default=1.5, gt=0.0, le=25.0)
    playbook_min_open_interest: int = Field(default=50_000, ge=0)
    playbook_iv_floor: float = Field(default=8.0, ge=0.0, le=200.0)
    playbook_iv_ceiling: float = Field(default=35.0, ge=0.0, le=500.0)
    playbook_max_lots: int = Field(default=2, ge=1, le=20)
    playbook_min_days_to_expiry: int = Field(default=1, ge=0, le=60)
    playbook_max_days_to_expiry: int = Field(default=10, ge=1, le=120)
    playbook_theta_budget_rupees: float = Field(default=1500.0, gt=0.0, le=1_000_000.0)
    playbook_time_stop: str = "15:00"
```

The validators and the two derived properties:

```python
    @field_validator("directional_regimes")
    @classmethod
    def _validate_directional(cls, value: str) -> str:
        from .grounding import TRADEABLE_LABELS

        labels = {piece.strip() for piece in value.split(",") if piece.strip()}
        if not labels:
            raise ValueError("directional_regimes must name at least one regime")
        unknown = sorted(labels - set(TRADEABLE_LABELS))
        if unknown:
            raise ValueError(f"not tradeable regimes: {', '.join(unknown)}")
        return value

    @field_validator("playbook_time_stop")
    @classmethod
    def _validate_time_stop(cls, value: str) -> str:
        time.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def _budgets_fit(self) -> Settings:
        """A tick must be able to hold both specialists, or every good read ends in a
        tick-timeout that looks like an infrastructure problem and is not."""
        if self.playbook_delta_min >= self.playbook_delta_max:
            raise ValueError("playbook_delta_min must be below playbook_delta_max")
        if self.playbook_iv_floor >= self.playbook_iv_ceiling:
            raise ValueError("playbook_iv_floor must be below playbook_iv_ceiling")
        if self.playbook_min_days_to_expiry > self.playbook_max_days_to_expiry:
            raise ValueError("playbook_min_days_to_expiry must not exceed the maximum")
        needed = self.specialist_timeout_seconds + self.strategist_timeout_seconds
        if needed >= self.tick_budget_seconds:
            raise ValueError(
                f"specialist timeouts total {needed:.0f}s, which does not fit inside the "
                f"{self.tick_budget_seconds:.0f}s tick budget"
            )
        return self

    @property
    def directional_regime_set(self) -> frozenset[str]:
        return frozenset(
            piece.strip() for piece in self.directional_regimes.split(",") if piece.strip()
        )

    @property
    def strategist_deadline_seconds(self) -> float:
        """The strategist's own budget, always under the registry's timeout."""
        return max(
            1.0, self.strategist_timeout_seconds - self.strategist_deadline_margin_seconds
        )
```

### `strike_desk/src/strike_desk/proposal_view.py`

One object, two renderings. The text and the JSON cannot disagree because the JSON *is* the
object and the text is a formatting of it.

```python
"""One proposal row, rendered as text or as JSON. Two views, one object."""

from __future__ import annotations

import json
from typing import Any

from .decline_taxonomy import TAXONOMY_ARTIFACT
from .journal import Proposal

STATUS_MARK = {
    "proposed": "PROPOSED",
    "no-contract": "no contract",
    "ungrounded": "UNGROUNDED",
    "invalid": "INVALID",
    "degraded": "degraded",
}


def as_dict(row: Proposal) -> dict[str, Any]:
    """Everything one proposal row holds, in the shape --json prints."""
    return {
        "proposal_id": row.proposal_id,
        "tick_id": row.tick_id,
        "trace_id": row.trace_id,
        "trading_day": row.trading_day,
        "created_at_utc": row.created_at_utc.isoformat(),
        "status": row.status,
        "regime": {"label": row.regime_label, "confidence": row.regime_confidence},
        "contract": {
            "direction": row.direction,
            "symbol": row.symbol,
            "expiry": row.expiry,
            "strike": row.strike,
            "option_type": row.option_type,
            "lots": row.lots,
            "lot_size": row.lot_size,
            "quantity": row.quantity,
            "entry_band": [row.entry_price_low, row.entry_price_high],
            "delta": row.delta,
            "theta_per_day": row.theta_per_day,
            "implied_volatility": row.implied_volatility,
            "open_interest": row.open_interest,
            "breakeven": row.breakeven,
            "stop_price": row.stop_price,
            "target_price": row.target_price,
            "time_stop_ist": row.time_stop_ist,
        },
        "playbook": {
            "artifact": row.playbook_artifact,
            "verdict": row.playbook_verdict,
            "violations": json.loads(row.violations_json or "[]"),
        },
        "rationale": row.rationale,
        "defect": row.defect,
        "cost": {
            "model": row.model_version,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "token_cost_micros": row.token_cost_micros,
        },
        "tools": {
            "calls": row.tool_call_count,
            "errors": row.tool_error_count,
            "rejected": row.rejected_tool_count,
        },
        "prompt": {
            "name": row.prompt_name,
            "version": row.prompt_version,
            "digest": row.prompt_digest,
            "set": row.prompt_set_version,
        },
        "latency_ms": row.latency_ms,
    }


def render(rows: list[Proposal], *, days: list[str]) -> str:
    """The terminal rendering of the same rows --json prints."""
    lines = [
        f"proposals        : {len(rows)} over {len(days)} day(s)",
        f"taxonomy         : {TAXONOMY_ARTIFACT}",
        "",
    ]
    if not rows:
        lines.append("(no proposal attempts in this window)")
        return "\n".join(lines)

    for row in rows:
        view = as_dict(row)
        contract = view["contract"]
        mark = STATUS_MARK.get(row.status, row.status)
        header = f"{row.created_at_utc.strftime('%H:%M:%S')}  {mark}"
        if contract["symbol"]:
            header += (
                f"  {contract['symbol']}  x{contract['lots']} lot(s)"
                f"  entry {contract['entry_band'][0]}-{contract['entry_band'][1]}"
            )
        lines.append(header)
        if contract["symbol"]:
            lines.append(
                f"    delta {contract['delta']}  iv {contract['implied_volatility']}%"
                f"  oi {contract['open_interest']}  theta {contract['theta_per_day']}/day"
            )
            lines.append(
                f"    breakeven {contract['breakeven']}  stop {contract['stop_price']}"
                f"  target {contract['target_price']}  time-stop {contract['time_stop_ist']} IST"
            )
        lines.append(f"    playbook  {row.playbook_verdict}  ({row.playbook_artifact})")
        for violation in view["playbook"]["violations"]:
            lines.append(f"      - {violation}")
        if row.defect:
            lines.append(f"    defect    {row.defect}")
        if row.rationale:
            lines.append(f"    {row.rationale}")
        lines.append(
            f"    cost ${row.token_cost_micros / 1_000_000:.4f}"
            f"  tools {row.tool_call_count}/{row.tool_error_count} err"
            f"/{row.rejected_tool_count} rejected  {row.latency_ms}ms"
        )
        lines.append("")
    return "\n".join(lines).rstrip()
```

### `strike_desk/src/strike_desk/__main__.py` — edits

The `proposals` command mirrors `declines` exactly, including the argument discipline: bad
arguments exit 2 *before* the journal is opened, because a report that has already touched
the database has already had a chance to be wrong about it.

`_resolve_days` is the day/window resolution `declines` already performs. Iteration 03
inlined it in `cmd_declines`; lift it to a module-level helper that raises `ValueError` with
the message the user sees, and have both commands call it. Two commands that resolve a date
range differently is a bug waiting for a Monday.

```python
def _add_proposals_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("proposals", help="list what the strategist proposed")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--day", help="one IST trading day, YYYY-MM-DD")
    group.add_argument(
        "--since", nargs="?", type=int, const=-1, help="the most recent N journalled days"
    )
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")


def cmd_proposals(args: Any) -> int:
    settings = get_settings()
    try:
        days = _resolve_days(args, settings)  # shared with `declines`; raises ValueError
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    journal = Journal(settings.db_path)
    try:
        journal.create_schema()
        if days is None:
            days = journal.recent_proposal_days(settings.report_default_days)
        rows = [row for day in days for row in journal.list_proposals(day)]
        if args.json:
            print(
                json.dumps(
                    {
                        "taxonomy": TAXONOMY_ARTIFACT,
                        "playbook": Playbook.from_settings(settings).artifact,
                        "days": days,
                        "proposals": [as_dict(row) for row in rows],
                    },
                    indent=2,
                    default=str,
                )
            )
        else:
            print(render(rows, days=days))
    finally:
        journal.close()
    return 0
```

And `status` prints the playbook beside the taxonomy, so one command shows every artifact
the desk is running:

```python
    print(f"taxonomy         : {TAXONOMY_ARTIFACT}")
    print(f"playbook         : {Playbook.from_settings(settings).artifact}")
    print(f"strategist model : {settings.strategist_model}")
    print(f"directional      : {settings.directional_regimes}")
```

### `strike_desk/.env.example` — the new block

```bash
# --- Cadence and budgets ---
# The tick must hold both specialists: 25 + 35 < 90. The service refuses to start otherwise.
STRIKE_DESK_TICK_INTERVAL_SECONDS=900
STRIKE_DESK_TICK_BUDGET_SECONDS=90
STRIKE_DESK_SPECIALIST_TIMEOUT_SECONDS=25
STRIKE_DESK_STRATEGIST_TIMEOUT_SECONDS=35

# --- Supervisor policy ---
STRIKE_DESK_MIN_REGIME_CONFIDENCE=0.55
# A directional long is proposed only in a directional regime. `range-bound` is tradeable
# but has no naked-long entry in this playbook — that arrives with the spreads of UC-19.
STRIKE_DESK_DIRECTIONAL_REGIMES=trending

# --- Options Strategist ---
STRIKE_DESK_STRATEGIST_MODEL=claude-sonnet-5
STRIKE_DESK_STRATEGIST_MAX_ROUNDS=6
STRIKE_DESK_STRATEGIST_DEADLINE_MARGIN_SECONDS=4
STRIKE_DESK_STRATEGIST_TOOL_OUTPUT_CHARS=16000
STRIKE_DESK_PROPOSAL_RATIONALE_MAX_CHARS=700
STRIKE_DESK_STRATEGIST_PRICE_IN_PER_MTOK=3.0
STRIKE_DESK_STRATEGIST_PRICE_OUT_PER_MTOK=15.0

# --- Playbook ---
STRIKE_DESK_PLAYBOOK_DELTA_MIN=0.35
STRIKE_DESK_PLAYBOOK_DELTA_MAX=0.60
STRIKE_DESK_PLAYBOOK_MAX_SPREAD_PCT=1.5
STRIKE_DESK_PLAYBOOK_MIN_OPEN_INTEREST=50000
STRIKE_DESK_PLAYBOOK_IV_FLOOR=8.0
STRIKE_DESK_PLAYBOOK_IV_CEILING=35.0
STRIKE_DESK_PLAYBOOK_MAX_LOTS=2
STRIKE_DESK_PLAYBOOK_MIN_DAYS_TO_EXPIRY=1
STRIKE_DESK_PLAYBOOK_MAX_DAYS_TO_EXPIRY=10
STRIKE_DESK_PLAYBOOK_THETA_BUDGET_RUPEES=1500
STRIKE_DESK_PLAYBOOK_TIME_STOP=15:00
```

## 12. First working result

With the broker connected and the market open:

```console
$ uv run strike-desk status
index            : NIFTY
taxonomy         : dt-2+<digest>
playbook         : pb-1+<digest>
strategist model : claude-sonnet-5
directional      : trending
roles            : regime, strategist

$ uv run strike-desk tick-now
$ uv run strike-desk proposals
proposals        : 1 over 1 day(s)
taxonomy         : dt-2+<digest>

11:47:12  PROPOSED  NIFTY02SEP2624800CE  x1 lot(s)  entry 138.5-142.0
    delta 0.472  iv 12.8%  oi 2841250  theta -18.4/day
    breakeven 24942.0  stop 112.0  target 196.0  time-stop 14:45 IST
    playbook  pass  (pb-1+<digest>)
    Trend and momentum both confirm the upside: ADX 27.4 with +DI above -DI, RSI 61.2 and
    price above the 20-EMA. The 24800 CE is the nearest strike inside the delta band at
    0.472, with 28.4 lakh open interest and a 0.7% spread, so it can be exited. IV at
    12.8% is mid-band, so the premium is not paying for a move already made. I am wrong
    if ADX rolls over below 20 or price loses the 20-EMA on a 15m close.
    cost $0.0412  tools 5/0 err/0 rejected  9820ms

$ uv run strike-desk declines
...
specialist-unavailable   1   specialist/degraded   a specialist the tick needed was not usable
    Declined: NIFTY02SEP2624800CE at 142.00 was proposed and passed the playbook, but no
    'risk' specialist is registered to adjudicate it. A proposal alone is never an entry.
```

That last block is the slice working exactly as designed. A contract was found, verified and
journalled — and the desk still did not trade, because UC-05 does not exist yet.

## 13. Reference

| Symbol | Where | What it is |
| --- | --- | --- |
| `Playbook` | `playbook.py` | The frozen constraint set, versioned `pb-1` plus a digest of its values. |
| `check` | `playbook.py` | Pure re-derivation of every constraint; returns violations by name. |
| `ProposalSubmission` | `grounding.py` | The wide, fully-typed contract the model must fill. |
| `NoContractSubmission` | `grounding.py` | The grounded refusal — a first-class answer. |
| `validate_proposal` | `grounding.py` | Citation grounding, extended to the structured numeric fields and the symbol. |
| `STRATEGIST_TOOLS` | `mcp_toolbox.py` | The seven read-only tools the strategist may reach. |
| `FORBIDDEN_PREFIXES` | `mcp_toolbox.py` | The import-time assertion that no whitelist holds a mutating verb. |
| `OptionsStrategist` | `options_strategist.py` | The agent: loop, two submissions, grounding, playbook, spans. |
| `Proposal` | `journal.py` | The append-only row. `SCHEMA_VERSION = 4`. |
| `CATEGORY_CONTRACT` | `decline_taxonomy.py` | The new category. `TAXONOMY_VERSION = "dt-2"`. |
| `ROLE_RISK` | `specialists.py` | The role the tick now names as missing. |
| `strategist_deadline_seconds` | `config.py` | Strategist timeout minus its margin: 35 − 4 = 31. |
| `directional_regime_set` | `config.py` | The regimes a directional long is allowed in. |

## 14. Limitations

Stated plainly, so none of them is discovered later as a surprise.

1. **Nothing enforces the time-stop, the stop or the target.** They are written into the
   proposal because AC-1 requires them and because UC-07 will need them. No code watches
   them, because there is no position to watch.
2. **The playbook is a first cut.** The bands are defensible starting values, not tuned
   ones. Tuning needs the replay eval harness of UC-17 and the baseline comparison of UC-11;
   until then, changing a band is a judgement call that the digest makes attributable.
3. **One contract, one leg, one index.** No spreads (UC-19), no second index (UC-16).
4. **The strategist re-reads trend and momentum the analyst already read.** That is a
   deliberate duplication — the analyst classifies the session, the strategist forms its own
   directional view — and it costs two extra tool calls per proposal. If the cost matters
   later, passing the analyst's evidence through the request is the obvious optimisation,
   and it would need care not to let a stale reading become a grounded citation.
5. **`get_option_greeks` computes rather than fetches.** OpenAlgo derives Greeks from a
   model with an interest-rate assumption; the delta and theta in a proposal are that
   model's, not the exchange's. The evidence ledger records what the tool returned, so the
   proposal is grounded in what the desk was told, which is the most that can be claimed.
6. **`record_playbook` widens the grounded set by exactly the playbook's own numbers, and
   that has one narrow cost.** Seeding `describe()` puts `0.35`, `0.60`, `1.50`, `8.0`,
   `35.0`, `50,000`, `1,500`, `1`, `2`, `10` and `15:00` into the ledger. So a proposal
   claiming a delta of exactly `0.60` — a band edge — now passes the `GROUNDED_FIELDS` check
   without any tool having returned it. The playbook still accepts that delta (it is inside
   the band), so such a proposal would stand. This is a real hole and it is accepted
   deliberately: closing it would mean grounding the structured fields against a
   tool-output-only ledger while grounding the sentence against the wider one, which is two
   rules where the slice's whole discipline is that there is one. The exposure is bounded to
   a handful of specific literals at the constraint boundaries, and any proposal built on one
   of them still has to survive every other check against numbers that *are* tool-sourced —
   the strike, the bid, the ask, the IV, the open interest and the symbol. If it ever
   matters, the fix is a separate `ledger.stated_numbers` set consulted only by the
   sentence validators, and it should arrive with a test that fails without it.
7. **No proposal has ever been acted on.** Every number in this slice is unfalsified by a
   fill. Nothing here should be read as evidence that the playbook picks good contracts —
   only that it picks contracts that meet its own stated rules.
