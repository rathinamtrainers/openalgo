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
