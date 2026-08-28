"""The deterministic verifier: every constraint, in both directions."""

from __future__ import annotations

import pytest

from strike_desk.grounding import ProposalSubmission
from strike_desk.playbook import PLAYBOOK_VERSION, Playbook, check
from tests.chain_fixtures import FIXED_NOW, proposal_args, valid_proposal


@pytest.fixture
def playbook(settings) -> Playbook:
    return Playbook.from_settings(settings)


def test_valid_proposal_passes(playbook: Playbook) -> None:
    assert check(valid_proposal(), playbook, now_ist=FIXED_NOW) == []


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"delta": 0.90}, "outside the 0.35-0.60 band"),
        ({"delta": 0.10}, "outside the 0.35-0.60 band"),
        ({"bid": 150.0, "ask": 230.0}, "exceeds the 1.50% cap"),
        ({"bid": 0.0, "ask": 0.0}, "quote is unusable"),
        ({"bid": 200.0, "ask": 180.0}, "is below bid"),
        ({"open_interest": 100}, "below the 50,000 floor"),
        ({"implied_volatility": 41.7}, "outside the 8.0-35.0% band"),
        ({"implied_volatility": 2.0}, "outside the 8.0-35.0% band"),
        ({"lots": 9}, "outside 1-2"),
        ({"quantity": 74}, "is not lots 1 x lot size 75"),
        ({"breakeven": 24997.0}, "does not equal 24992.00"),
        ({"entry_price_low": 300.0}, "is inverted"),
        ({"entry_price_low": 100.0, "entry_price_high": 120.0}, "outside the entry band"),
        ({"stop_price": 189.0}, "is not below the entry band low"),
        ({"target_price": 190.0}, "is not above the entry band high"),
        ({"expiry": "2026-12-31"}, "outside 1-10"),
        ({"expiry": "2026-08-25"}, "outside 1-10"),
        ({"expiry": "02-09-2026"}, "is not an ISO date"),
        ({"theta_per_day": 18.4}, "is positive; a long option decays"),
        ({"theta_per_day": -40.0}, "exceeds the Rs 1,500 budget"),
        ({"time_stop_ist": "09:00"}, "is not in the future"),
        ({"time_stop_ist": "16:30"}, "later than the 15:00 session cutoff"),
        ({"time_stop_ist": "not-a-time"}, "is not HH:MM"),
    ],
)
def test_each_violation_is_named(playbook: Playbook, overrides: dict, fragment: str) -> None:
    violations = check(valid_proposal(**overrides), playbook, now_ist=FIXED_NOW)
    assert violations, f"expected a violation for {overrides}"
    assert any(fragment in violation for violation in violations), violations


def test_direction_and_type_must_agree(playbook: Playbook) -> None:
    """The schema refuses the mismatch, and the playbook refuses it again."""
    with pytest.raises(ValueError, match="requires PE"):
        valid_proposal(direction="bearish")

    # Bypass the schema the way a hand-built row could, and confirm the playbook still
    # binds. `model_construct` skips validation by design; do not reach for
    # `object.__setattr__` here — it would keep "working" if the model were ever made
    # frozen, silently hiding the change to the schema guarantee.
    proposal = ProposalSubmission.model_construct(**proposal_args(direction="bearish"))
    violations = check(proposal, playbook, now_ist=FIXED_NOW)
    assert any("requires PE" in violation for violation in violations)


def test_violations_are_independent(playbook: Playbook) -> None:
    """check reports every failure, not only the first — a journal needs all of them."""
    violations = check(
        valid_proposal(delta=0.95, open_interest=10, lots=9),
        playbook,
        now_ist=FIXED_NOW,
    )
    assert len(violations) >= 3
    assert any("delta" in v for v in violations)
    assert any("open interest" in v for v in violations)
    assert any("lots" in v for v in violations)


def test_the_playbook_is_a_versioned_artifact(playbook: Playbook, settings) -> None:
    assert playbook.artifact.startswith(f"{PLAYBOOK_VERSION}+")
    assert len(playbook.digest) == 12

    tightened = Playbook.from_settings(settings.model_copy(update={"playbook_delta_max": 0.55}))
    assert tightened.digest != playbook.digest
    assert tightened.artifact.startswith(f"{PLAYBOOK_VERSION}+")


def test_describe_states_every_number_the_prompt_needs(playbook: Playbook) -> None:
    described = playbook.describe()
    for number in ("0.35", "0.60", "1.50%", "50,000", "8.0", "35.0", "1,500", "15:00"):
        assert number in described
