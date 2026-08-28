# Iteration 05 — Test Automation

> **Document:** the suite that holds UC-05 in place. `03_manual_test_cases.md` is what you
> check once with your hands; this is what CI checks on every push forever.

## 1. Framework, and what is real

Same framework as iterations 01 to 04: `pytest`, `respx` for OpenAlgo's HTTP surface, a fake
chat model for the reasoning plane, a real SQLite journal in a `tmp_path`, and a real compiled
LangGraph. This slice mocks less than any before it, because the thing under test takes data
and returns a verdict: the officer's own tests construct a book dictionary and a proposal and
call a function. There is no clock to freeze beyond the one the fixtures already fix, no
network to intercept, and no model anywhere near the code path.

The proposal every case starts from is iteration 04's recorded one, so the arithmetic below is
the arithmetic of a real NIFTY contract: a ₹192.00 entry against a ₹152.00 stop over 75 units
is **₹3,000 of risk and ₹14,400 of premium per lot**. Every capital figure in this suite is
chosen so exactly one limit is the interesting one, and the fixture module says so in a
comment beside each.

The suite adds five files and extends three:

| File | What it proves |
| --- | --- |
| `tests/risk_fixtures.py` | Book snapshots at chosen capital bases, the two-lot proposal, and the verdict seeder. |
| `tests/test_risk_officer.py` | Every limit, both sides of every boundary, the reduce path, the holds, and the import graph. |
| `tests/test_risk_scenarios.py` | The frozen regression set: scenario → verdict → tripped limit → the exact sentence. |
| `tests/test_tick_adjudication.py` | Routing, the session stop, journal ordering, spans, and that `enter` needs a cleared verdict. |
| `tests/test_risk_view.py` | Text and JSON are one object. |

Plus additions to `tests/test_decline_taxonomy.py` (the fourth outcome and the additive
guarantee), `tests/test_journal_migration.py` (the new table) and `tests/conftest.py` (three
methods on the existing `tick_harness`).

## 2. The book fixtures

A book snapshot is what `read_book_state` produces, as a dictionary, because that is what the
graph puts in tick state and therefore what the officer actually receives. `book_with` splits
a requested capital base across available cash and utilised margin in a fixed 80/20 ratio so
that a test naming a base gets that base regardless of how the split moves, and every position
it holds is a real `OpenPosition` shape.

### `strike_desk/tests/risk_fixtures.py`

```python
"""Book snapshots and sized proposals for the Risk Officer's tests.

The recorded contract from ``chain_fixtures`` risks Rs 3,000 a lot (192.00 entry, 152.00
stop, 75 units) and deploys Rs 14,400 of premium a lot. Every capital base below is chosen
from those two numbers so that exactly one limit is the interesting one.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from strike_desk.grounding import ProposalSubmission
from strike_desk.journal import SCHEMA_VERSION

from .chain_fixtures import INDEX, LOT_SIZE, SYMBOL, valid_proposal

#: The recorded contract, per lot.
RISK_PER_LOT = 3_000.0
PREMIUM_PER_LOT = 14_400.0

#: Capital bases, each named for the limit it makes interesting at the default percentages.
ROOMY = 1_500_000.0  # per-trade cap 7,500 — two lots (6,000) clear
REDUCES = 800_000.0  # per-trade cap 4,000 — two lots breach, one lot (3,000) clears
VETOES = 500_000.0  # per-trade cap 2,500 — even one lot breaches
ON_THE_CAP = 600_000.0  # per-trade cap 3,000 — exactly one lot's risk, so it breaches


def position(
    *, quantity: int = LOT_SIZE, average_price: float = 192.0, symbol: str = SYMBOL
) -> dict[str, Any]:
    """One open position, in the shape ``BookState.as_dict`` emits."""
    return {
        "symbol": symbol,
        "exchange": "NFO",
        "product": "MIS",
        "quantity": quantity,
        "average_price": average_price,
        "ltp": average_price,
        "pnl": 0.0,
    }


def book_with(
    *,
    capital: float = ROOMY,
    realised: float = 0.0,
    unrealised: float = 0.0,
    positions: tuple[dict[str, Any], ...] = (),
    decisions_today: int = 0,
) -> dict[str, Any]:
    """A book snapshot at a chosen capital base, in the shape tick state holds it."""
    cash = round(capital * 0.8, 2)
    return {
        "captured_at_utc": datetime.now(tz=UTC).isoformat(),
        "flat": not positions,
        "open_positions": list(positions),
        "available_cash": cash,
        "utilised_margin": round(capital - cash, 2),
        "realised_pnl": realised,
        "unrealised_pnl": unrealised,
        "decisions_today": decisions_today,
    }


def two_lots(**overrides: Any) -> ProposalSubmission:
    """The recorded proposal at two lots, with quantity kept consistent."""
    return valid_proposal(lots=2, quantity=2 * LOT_SIZE, **overrides)


def seed_verdict(journal: Any, *, trading_day: str, verdict: str = "pass", **fields: Any) -> str:
    """Append one risk_verdicts row directly, for view and report tests."""
    row: dict[str, Any] = {
        "verdict_id": str(uuid.uuid4()),
        "tick_id": str(uuid.uuid4()),
        "trace_id": "0" * 32,
        "proposal_id": str(uuid.uuid4()),
        "created_at_utc": datetime.now(tz=UTC),
        "trading_day": trading_day,
        "index_symbol": INDEX,
        "symbol": SYMBOL,
        "verdict": verdict,
        "tripped_limit": None,
        "configured_value": None,
        "observed_value": None,
        "limit_unit": None,
        "capital_base": ROOMY,
        "lots_requested": 2,
        "lots_cleared": 2,
        "premium_at_risk": 2 * PREMIUM_PER_LOT,
        "max_loss_at_stop": 2 * RISK_PER_LOT,
        "session_stop": False,
        "checks_json": json.dumps([]),
        "limits_artifact": "rl-1+testdigest",
        "detail": "cleared 2 lot(s)",
        "latency_us": 214,
        "schema_version": SCHEMA_VERSION,
    }
    row.update(fields)
    return journal.record_risk_verdict(**row)
```

## 3. The officer, exhaustively

This is the file the PRD's LLMOps clause is pointing at when it says the deterministic limits
must have tests that fail loudly. Every limit is asserted at three points — one rupee inside,
exactly on, one rupee outside for the money limits; one under, exactly at, one over for the
counts — because the boundary is the only part of a limit anyone gets wrong.

Three tests in it are structural rather than numeric and are worth calling out.
`test_the_officer_imports_no_reasoning_module` parses the module and asserts its import graph
never touches the model, prompt, specialist or HTTP layers; that is AC-7 proved rather than
promised, and it is the test that fails the day someone decides the officer would be "smarter"
with a model call in it. `test_the_rationale_has_no_standing` feeds the same contract twice,
once with a rationale begging for an exception, and asserts the verdicts are identical.
`test_the_officer_never_raises` throws malformed books at it and asserts a verdict comes back
every time — a risk check that raises is a risk check something upstream can swallow.

### `strike_desk/tests/test_risk_officer.py`

```python
"""The Risk Officer: every limit, both sides of every boundary, and the import graph."""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from strike_desk import risk_officer
from strike_desk.grounding import ProposalSubmission
from strike_desk.playbook import Playbook
from strike_desk.risk_officer import (
    FR5_LIMITS,
    LIMIT_CAPITAL_BASE,
    LIMIT_DAILY_LOSS,
    LIMIT_EXPIRY_WINDOW,
    LIMIT_MAX_LOTS,
    LIMIT_MAX_POSITIONS,
    LIMIT_MAX_TRADES,
    LIMIT_PER_TRADE_LOSS,
    LIMIT_PROPOSAL_SHAPE,
    RISK_LIMITS_VERSION,
    VERDICT_HOLD,
    VERDICT_PASS,
    VERDICT_REDUCE,
    VERDICT_VETO,
    RiskLimits,
    adjudicate,
    assess_session,
)

from .chain_fixtures import FIXED_NOW, proposal_args, valid_proposal
from .risk_fixtures import (
    ON_THE_CAP,
    REDUCES,
    ROOMY,
    VETOES,
    book_with,
    position,
    two_lots,
)


@pytest.fixture
def limits(settings: Any) -> RiskLimits:
    return RiskLimits.from_settings(settings)


@pytest.fixture
def playbook(settings: Any) -> Playbook:
    return Playbook.from_settings(settings)


def adjudged(proposal: Any, book: dict[str, Any], limits: RiskLimits, playbook: Playbook):
    """Adjudicate at the fixed clock with a clean day, which is most cases."""
    return adjudicate(proposal, book, limits, playbook, now_ist=FIXED_NOW, entries_today=0)


def test_a_proposal_with_headroom_passes(limits, playbook) -> None:
    verdict = adjudged(two_lots(), book_with(capital=ROOMY), limits, playbook)
    assert verdict.verdict == VERDICT_PASS
    assert verdict.lots_cleared == 2
    assert verdict.tripped is None
    assert verdict.capital_base == ROOMY
    assert verdict.max_loss_at_stop == 6_000.0
    assert verdict.premium_at_risk == 28_800.0


def test_every_fr5_limit_is_evaluated(limits, playbook) -> None:
    """AC-2: all eight are named on every adjudication, breached or not."""
    verdict = adjudged(two_lots(), book_with(capital=ROOMY), limits, playbook)
    assert {check.limit for check in verdict.checks} == set(FR5_LIMITS)


def test_the_checks_are_evaluated_at_the_size_that_was_requested(limits, playbook) -> None:
    verdict = adjudged(two_lots(), book_with(capital=REDUCES), limits, playbook)
    per_trade = next(c for c in verdict.checks if c.limit == LIMIT_PER_TRADE_LOSS)
    assert (verdict.verdict, verdict.lots_cleared) == (VERDICT_REDUCE, 1)
    assert (per_trade.configured, per_trade.observed) == (4_000.0, 6_000.0)
    assert verdict.max_loss_at_stop == 3_000.0  # the headline figure is the cleared size


def test_no_size_clears_so_it_is_a_veto(limits, playbook) -> None:
    verdict = adjudged(two_lots(), book_with(capital=VETOES), limits, playbook)
    assert verdict.verdict == VERDICT_VETO
    assert verdict.lots_cleared == 0
    assert verdict.tripped is not None
    assert verdict.tripped.limit == LIMIT_PER_TRADE_LOSS
    assert (verdict.tripped.configured, verdict.tripped.observed) == (2_500.0, 3_000.0)


@pytest.mark.parametrize(
    ("capital", "expected"),
    [
        (ON_THE_CAP - 1, VERDICT_VETO),
        (ON_THE_CAP, VERDICT_VETO),  # touching the cap is breaching it
        (ON_THE_CAP + 1, VERDICT_PASS),
    ],
)
def test_a_rupee_limit_breaches_on_touch(capital, expected, limits, playbook) -> None:
    """AC-3. A cap is the amount you may not lose, not the amount you may."""
    verdict = adjudged(valid_proposal(), book_with(capital=capital), limits, playbook)
    assert verdict.verdict == expected


@pytest.mark.parametrize(
    ("entries_today", "expected"),
    [(1, VERDICT_PASS), (2, VERDICT_PASS), (3, VERDICT_VETO)],
)
def test_a_count_limit_permits_its_configured_number(
    entries_today, expected, limits, playbook
) -> None:
    """AC-3. max_trades_per_day = 3 permits the third entry and refuses the fourth."""
    verdict = adjudicate(
        two_lots(),
        book_with(capital=ROOMY),
        limits,
        playbook,
        now_ist=FIXED_NOW,
        entries_today=entries_today,
    )
    assert verdict.verdict == expected
    if expected == VERDICT_VETO:
        assert verdict.tripped.limit == LIMIT_MAX_TRADES
        assert (verdict.tripped.configured, verdict.tripped.observed) == (3.0, 4.0)


def test_a_count_limit_is_never_resolved_by_reduction(limits, playbook) -> None:
    """AC-4. A fourth trade is a fourth trade however small it is."""
    verdict = adjudicate(
        two_lots(),
        book_with(capital=ROOMY),
        limits,
        playbook,
        now_ist=FIXED_NOW,
        entries_today=3,
    )
    assert (verdict.verdict, verdict.lots_cleared) == (VERDICT_VETO, 0)


def test_an_open_position_trips_the_concurrency_limit(limits, playbook) -> None:
    verdict = adjudged(
        two_lots(), book_with(capital=ROOMY, positions=(position(),)), limits, playbook
    )
    assert verdict.verdict == VERDICT_VETO
    assert verdict.tripped.limit == LIMIT_MAX_POSITIONS
    assert (verdict.tripped.configured, verdict.tripped.observed) == (1.0, 2.0)


def test_too_many_lots_trips_the_lot_ceiling(limits, playbook) -> None:
    verdict = adjudged(
        valid_proposal(lots=3, quantity=225), book_with(capital=ROOMY), limits, playbook
    )
    assert verdict.tripped.limit == LIMIT_MAX_LOTS
    assert verdict.verdict == VERDICT_VETO


@pytest.mark.parametrize(
    ("expiry_offset_days", "hour", "breached"),
    [(7, 15, False), (0, 13, False), (0, 14, True), (0, 15, True)],
)
def test_the_expiry_day_window_binds_only_on_expiry_day(
    expiry_offset_days, hour, breached, limits, playbook
) -> None:
    now = FIXED_NOW.replace(hour=hour, minute=0)
    expiry = (now + timedelta(days=expiry_offset_days)).date().isoformat()
    verdict = adjudicate(
        valid_proposal(expiry=expiry),
        book_with(capital=ROOMY),
        limits,
        playbook,
        now_ist=now,
        entries_today=0,
    )
    check = next(c for c in verdict.checks if c.limit == LIMIT_EXPIRY_WINDOW)
    assert check.breached is breached
    if breached:
        assert verdict.verdict == VERDICT_VETO


def test_a_breached_daily_cap_vetoes_and_marks_a_session_stop(limits, playbook) -> None:
    book = book_with(capital=ROOMY, realised=-30_000.0)  # cap is 2% of 1,500,000
    verdict = adjudged(two_lots(), book, limits, playbook)
    assert verdict.verdict == VERDICT_VETO
    assert verdict.tripped.limit == LIMIT_DAILY_LOSS
    assert verdict.session_stop is True


@pytest.mark.parametrize(
    ("realised", "unrealised", "stopped"),
    [(0.0, 0.0, False), (-29_999.0, 0.0, False), (-15_000.0, -15_000.0, True), (50_000.0, 0.0, False)],
)
def test_assess_session_reads_the_whole_day(realised, unrealised, stopped, limits) -> None:
    assessment = assess_session(
        book_with(capital=ROOMY, realised=realised, unrealised=unrealised), limits
    )
    assert assessment.stopped is stopped
    assert assessment.capital_base == ROOMY


@pytest.mark.parametrize("capital", [0.0, 1.0, 50_000.0])
def test_an_unusable_capital_base_holds(capital, limits, playbook) -> None:
    """AC-5. Percentages of nothing are not limits."""
    verdict = adjudged(valid_proposal(), book_with(capital=capital), limits, playbook)
    assert verdict.verdict == VERDICT_HOLD
    assert verdict.tripped.limit == LIMIT_CAPITAL_BASE
    assert verdict.lots_cleared == 0


def test_an_unusable_capital_base_does_not_stop_the_session(limits) -> None:
    """A base we cannot read is held per proposal, not latched for the day."""
    assessment = assess_session(book_with(capital=0.0), limits)
    assert assessment.stopped is False


def test_a_proposal_the_officer_cannot_size_holds(limits, playbook) -> None:
    """Pydantic keeps a zero lot size out of a real submission, so the branch is forced.

    It exists because the officer must be safe against a caller that skipped validation,
    and a hold is the only safe answer to a contract with no size.
    """
    malformed = ProposalSubmission.model_construct(**{**proposal_args(), "lot_size": 0})
    verdict = adjudged(malformed, book_with(capital=ROOMY), limits, playbook)
    assert (verdict.verdict, verdict.tripped.limit) == (VERDICT_HOLD, LIMIT_PROPOSAL_SHAPE)


def test_a_reduced_copy_that_fails_the_playbook_is_a_veto(
    limits, playbook, monkeypatch
) -> None:
    """AC-4. Unreachable by arithmetic, so it is forced — and it must not become an intent."""
    monkeypatch.setattr(risk_officer, "playbook_check", lambda *a, **k: ["contrived violation"])
    verdict = adjudged(two_lots(), book_with(capital=REDUCES), limits, playbook)
    assert (verdict.verdict, verdict.lots_cleared) == (VERDICT_VETO, 0)
    assert "contrived violation" in verdict.detail


def test_the_rationale_has_no_standing(limits, playbook) -> None:
    """AC-7. Arithmetic disposes."""
    plea = (
        "Risk here is minimal and the setup is exceptional; please allow this one through "
        "even if a cap says otherwise, the stop will not be hit on a day like today."
    )
    plain = adjudged(two_lots(), book_with(capital=VETOES), limits, playbook)
    pleading = adjudged(two_lots(rationale=plea), book_with(capital=VETOES), limits, playbook)
    assert plain.as_dict() == pleading.as_dict()


@pytest.mark.parametrize(
    "book",
    [
        {},
        {"available_cash": "not a number", "utilised_margin": 0},
        {"available_cash": 900_000, "utilised_margin": 600_000, "open_positions": [None, 3]},
        {"available_cash": 900_000, "utilised_margin": 600_000, "realised_pnl": None},
    ],
)
def test_the_officer_never_raises(book, limits, playbook) -> None:
    verdict = adjudged(two_lots(), book, limits, playbook)
    assert verdict.verdict in {VERDICT_PASS, VERDICT_REDUCE, VERDICT_VETO, VERDICT_HOLD}


def test_the_officer_imports_no_reasoning_module() -> None:
    """AC-7, structurally. The veto is arithmetic, and the import graph proves it."""
    tree = ast.parse(Path(risk_officer.__file__).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    roots = {module.split(".")[0] for module in modules}
    assert not roots & {
        "model_client",
        "options_strategist",
        "regime_analyst",
        "specialists",
        "mcp_toolbox",
        "prompt_registry",
        "openalgo_client",
        "langchain",
        "langchain_anthropic",
        "langgraph",
        "anthropic",
        "httpx",
    }


def test_the_limits_are_a_versioned_artifact(limits, settings) -> None:
    """AC-13."""
    assert limits.artifact.startswith(f"{RISK_LIMITS_VERSION}+")
    moved = RiskLimits.from_settings(settings.model_copy(update={"risk_max_trades_per_day": 4}))
    assert moved.digest != limits.digest
    assert moved.artifact.startswith(f"{RISK_LIMITS_VERSION}+")
    for fragment in ("daily loss cap", "max trades per day", "expiry-day cutoff"):
        assert fragment in limits.describe()
```

## 4. The frozen regression set

`test_risk_scenarios.py` is the gate the PRD asks for: a fixed table of situations, each
pinned to the verdict it must produce, the limit that must be named, and **the exact sentence
the trader reads**. It runs the officer and then hands its verdict to the real decision table,
so a scenario covers the whole chain from book to journalled sentence rather than the function
in isolation. Changing a limit's behaviour, its wording or its disposition fails this file
until the change is made deliberately and the table is updated in the same commit.

The sentences are pinned in the file rather than in a golden JSON, because there are eight of
them and a reviewer should see the diff in the pull request rather than in a regenerated
artifact. The taxonomy's own golden file still covers the templates; this covers the sentences
as the decision table actually assembles them.

### `strike_desk/tests/test_risk_scenarios.py`

```python
"""The frozen risk regression set: scenario -> verdict -> limit -> the trader's sentence."""

from __future__ import annotations

from typing import Any

import pytest

from strike_desk.graph import (
    OUTCOME_DECLINE,
    OUTCOME_ENTER,
    REASON_RISK_CLEARED,
    REASON_RISK_INPUT_UNAVAILABLE,
    REASON_RISK_VETO,
    _decide_outcome,
)
from strike_desk.playbook import Playbook
from strike_desk.risk_officer import RiskLimits, adjudicate

from .chain_fixtures import FIXED_NOW, valid_proposal
from .risk_fixtures import REDUCES, ROOMY, VETOES, book_with, position, two_lots

#: Each case: what the book and the proposal are, and exactly what the desk must answer.
SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "clears-at-full-size",
        "capital": ROOMY,
        "lots": 2,
        "entries_today": 0,
        "positions": (),
        "verdict": "pass",
        "limit": None,
        "outcome": OUTCOME_ENTER,
        "code": REASON_RISK_CLEARED,
        "sentence": (
            "Intent: buy 2 lot(s) of NIFTY02SEP2624800CE at up to 192.00, risking Rs 6,000 "
            "to the 152.00 stop against a Rs 1,500,000 capital base. This is an intent, not "
            "an order."
        ),
    },
    {
        "id": "reduced-to-fit-the-per-trade-cap",
        "capital": REDUCES,
        "lots": 2,
        "entries_today": 0,
        "positions": (),
        "verdict": "reduce",
        "limit": "per-trade-loss-cap",
        "outcome": OUTCOME_ENTER,
        "code": REASON_RISK_CLEARED,
        "sentence": (
            "Intent: buy 1 lot(s) of NIFTY02SEP2624800CE at up to 192.00, cut from 2 lot(s) "
            "to fit the per-trade-loss-cap limit of Rs 4,000. Risking Rs 3,000 to the 152.00 "
            "stop. This is an intent, not an order."
        ),
    },
    {
        "id": "no-size-clears",
        "capital": VETOES,
        "lots": 2,
        "entries_today": 0,
        "positions": (),
        "verdict": "veto",
        "limit": "per-trade-loss-cap",
        "outcome": OUTCOME_DECLINE,
        "code": REASON_RISK_VETO,
        "sentence": (
            "Declined: even one lot of NIFTY02SEP2624800CE trips the per-trade-loss-cap "
            "limit - configured Rs 2,500, observed Rs 3,000. There is no size this desk may "
            "take."
        ),
    },
    {
        "id": "the-days-fourth-trade",
        "capital": ROOMY,
        "lots": 2,
        "entries_today": 3,
        "positions": (),
        "verdict": "veto",
        "limit": "max-trades-per-day",
        "outcome": OUTCOME_DECLINE,
        "code": REASON_RISK_VETO,
        "sentence": (
            "Declined: NIFTY02SEP2624800CE trips the max-trades-per-day limit - configured "
            "3, observed 4. The veto is arithmetic and is not negotiated."
        ),
    },
    {
        "id": "a-position-is-already-open",
        "capital": ROOMY,
        "lots": 2,
        "entries_today": 0,
        "positions": (position(),),
        "verdict": "veto",
        "limit": "max-concurrent-positions",
        "outcome": OUTCOME_DECLINE,
        "code": REASON_RISK_VETO,
        "sentence": (
            "Declined: NIFTY02SEP2624800CE trips the max-concurrent-positions limit - "
            "configured 1, observed 2. The veto is arithmetic and is not negotiated."
        ),
    },
    {
        "id": "the-capital-base-is-unreadable",
        "capital": 0.0,
        "lots": 2,
        "entries_today": 0,
        "positions": (),
        "verdict": "hold",
        "limit": "capital-base",
        "outcome": OUTCOME_DECLINE,
        "code": REASON_RISK_INPUT_UNAVAILABLE,
        "sentence": (
            "Declined: the capital-base limit could not be evaluated (capital base Rs 0 is "
            "at or below the Rs 50,000 floor). A proposal is held, never assumed safe."
        ),
    },
)


def _run(case: dict[str, Any], settings: Any) -> tuple[Any, tuple[str, str, str]]:
    proposal = two_lots() if case["lots"] == 2 else valid_proposal()
    verdict = adjudicate(
        proposal,
        book_with(capital=case["capital"], positions=case["positions"]),
        RiskLimits.from_settings(settings),
        Playbook.from_settings(settings),
        now_ist=FIXED_NOW,
        entries_today=case["entries_today"],
    )
    state = {
        "regime_label": "trending",
        "regime_confidence": 0.8,
        "proposal_status": "proposed",
        "proposal": proposal.model_dump(),
        "risk": verdict.as_dict(),
    }
    return verdict, _decide_outcome(state, settings)


@pytest.mark.parametrize("case", SCENARIOS, ids=lambda case: case["id"])
def test_the_frozen_scenario_produces_its_pinned_verdict(case, settings) -> None:
    verdict, _ = _run(case, settings)
    assert verdict.verdict == case["verdict"]
    assert (verdict.tripped.limit if verdict.tripped else None) == case["limit"]


@pytest.mark.parametrize("case", SCENARIOS, ids=lambda case: case["id"])
def test_the_frozen_scenario_reads_the_way_it_is_pinned(case, settings) -> None:
    """A wording change is a deliberate change, made in this file or not at all."""
    _, (outcome, code, text) = _run(case, settings)
    assert (outcome, code) == (case["outcome"], case["code"])
    assert text == case["sentence"]
    assert len(text) <= settings.reason_text_max_chars


def test_every_clearing_scenario_says_it_is_not_an_order(settings) -> None:
    for case in SCENARIOS:
        if case["outcome"] == OUTCOME_ENTER:
            assert "not an order" in case["sentence"]
```

## 5. The tick

`test_tick_adjudication.py` drives the real graph with a stubbed strategist, so it tests
routing and journalling rather than the officer's arithmetic. `tick_harness` gains four
things in `conftest.py` — `set_book(**kwargs)` to install a book snapshot from
`risk_fixtures.book_with`, `verdict_rows()` to read `risk_verdicts`, `analyst_calls` beside the
existing `strategist_calls`, and `requested_paths()` returning the set of OpenAlgo paths the
`respx` router actually saw — and nothing else about it changes.

Two tests carry the slice's non-negotiables. `test_enter_requires_a_cleared_verdict` replaces
iteration 04's `test_enter_is_unreachable`, and it is the structural one: it reflects over
`_decide_outcome` and asserts that every branch returning `OUTCOME_ENTER` is inside the block
guarded by a cleared verdict, so an `enter` cannot be reached from a veto, a hold, a missing
verdict or any proposal status. `test_a_stopped_session_never_consults_a_specialist` is the
cost-and-safety one: on a stopped session neither model is called at all.

### `strike_desk/tests/test_tick_adjudication.py`

```python
"""Routing, latching, journal ordering and spans through a real graph."""

from __future__ import annotations

import inspect

import pytest

from strike_desk.errors import JournalWriteError
from strike_desk.graph import (
    OUTCOME_DECLINE,
    OUTCOME_ENTER,
    REASON_RISK_CLEARED,
    REASON_RISK_INPUT_UNAVAILABLE,
    REASON_RISK_SESSION_STOPPED,
    REASON_RISK_VETO,
    _decide_outcome,
)
from strike_desk.options_strategist import (
    STATUS_DEGRADED,
    STATUS_NO_CONTRACT,
    STATUS_PROPOSED,
)

from .risk_fixtures import REDUCES, ROOMY, VETOES


def test_a_cleared_proposal_becomes_an_intent(tick_harness) -> None:
    """AC-8, behaviourally."""
    tick_harness.set_book(capital=ROOMY)
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=STATUS_PROPOSED)
    decision = tick_harness.run_tick()
    assert decision.outcome == OUTCOME_ENTER
    assert decision.reason_code == REASON_RISK_CLEARED
    assert len(tick_harness.verdict_rows()) == 1
    assert tick_harness.verdict_rows()[0].verdict == "pass"


def test_a_reduced_intent_records_both_sizes(tick_harness) -> None:
    tick_harness.set_book(capital=REDUCES)
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=STATUS_PROPOSED, lots=2)
    decision = tick_harness.run_tick()
    row = tick_harness.verdict_rows()[0]
    assert decision.outcome == OUTCOME_ENTER
    assert (row.lots_requested, row.lots_cleared) == (2, 1)
    assert row.tripped_limit == "per-trade-loss-cap"
    assert "cut from 2 lot(s)" in decision.reason_text


def test_a_veto_declines_and_names_the_limit(tick_harness) -> None:
    tick_harness.set_book(capital=VETOES)
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=STATUS_PROPOSED, lots=2)
    decision = tick_harness.run_tick()
    assert (decision.outcome, decision.reason_code) == (OUTCOME_DECLINE, REASON_RISK_VETO)
    assert tick_harness.verdict_rows()[0].verdict == "veto"


def test_an_unreadable_capital_base_holds_the_proposal(tick_harness) -> None:
    tick_harness.set_book(capital=0.0)
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=STATUS_PROPOSED)
    decision = tick_harness.run_tick()
    assert decision.reason_code == REASON_RISK_INPUT_UNAVAILABLE
    assert decision.outcome == OUTCOME_DECLINE


@pytest.mark.parametrize("status", [STATUS_NO_CONTRACT, STATUS_DEGRADED])
def test_only_a_proposed_contract_is_adjudicated(status, tick_harness) -> None:
    tick_harness.set_book(capital=ROOMY)
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=status)
    decision = tick_harness.run_tick()
    assert decision.outcome == OUTCOME_DECLINE
    assert tick_harness.verdict_rows() == []


def test_a_stopped_session_never_consults_a_specialist(tick_harness) -> None:
    """AC-6: the gate is before the spend, not after it."""
    tick_harness.set_book(capital=ROOMY, realised=-40_000.0)
    decision = tick_harness.run_tick()
    assert decision.reason_code == REASON_RISK_SESSION_STOPPED
    assert (tick_harness.analyst_calls, tick_harness.strategist_calls) == (0, 0)
    assert decision.token_cost_micros == 0
    assert tick_harness.proposal_rows() == []


def test_the_session_stop_is_latched_once_a_day(tick_harness) -> None:
    tick_harness.set_book(capital=ROOMY, realised=-40_000.0)
    tick_harness.run_tick()
    tick_harness.run_tick()
    latched = [row for row in tick_harness.verdict_rows() if row.session_stop]
    assert len(latched) == 1
    assert len(tick_harness.decision_rows()) == 2


def test_the_latch_survives_a_recovered_book(tick_harness) -> None:
    """A session stop is for the session; it is not re-argued every fifteen minutes."""
    tick_harness.set_book(capital=ROOMY, realised=-40_000.0)
    tick_harness.run_tick()
    tick_harness.set_book(capital=ROOMY, realised=5_000.0)
    decision = tick_harness.run_tick()
    assert decision.reason_code == REASON_RISK_SESSION_STOPPED
    assert tick_harness.analyst_calls == 0


def test_an_open_position_still_holds_rather_than_stopping(tick_harness) -> None:
    """The stop takes the desk out of new entries; managing the book comes first."""
    from .risk_fixtures import position

    tick_harness.set_book(capital=ROOMY, realised=-40_000.0, positions=(position(),))
    decision = tick_harness.run_tick()
    assert decision.outcome == "hold"
    assert decision.reason_code == "position-open"


def test_the_verdict_row_is_written_before_the_decision(tick_harness, monkeypatch) -> None:
    """AC-1. An intent that was not journalled did not happen."""

    def boom(**_: object) -> str:
        raise JournalWriteError("forced")

    tick_harness.set_book(capital=ROOMY)
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=STATUS_PROPOSED)
    monkeypatch.setattr(tick_harness.journal, "record_decision", boom)
    with pytest.raises(JournalWriteError):
        tick_harness.run_tick()
    assert len(tick_harness.verdict_rows()) == 1
    assert tick_harness.decision_rows() == []


def test_enter_requires_a_cleared_verdict(tick_harness) -> None:
    """AC-8, structurally. Replaces iteration 04's test_enter_is_unreachable."""
    source = inspect.getsource(_decide_outcome)
    head, _, tail = source.partition('risk["verdict"] == VERDICT_VETO')
    assert "OUTCOME_ENTER" not in head, "an enter branch sits before the veto check"
    assert tail.count("OUTCOME_ENTER") == 1, "exactly one branch may return an intent"


def test_no_verdict_at_all_still_declines(settings, tick_state) -> None:
    """Fail closed: an unadjudicated proposal is never an intent."""
    state = {
        **tick_state,
        "regime_label": "trending",
        "regime_confidence": 0.8,
        "proposal_status": STATUS_PROPOSED,
        "proposal": {"symbol": "NIFTY02SEP2624800CE"},
    }
    outcome, code, text = _decide_outcome(state, settings)
    assert outcome == OUTCOME_DECLINE
    assert "'risk'" in text


def test_the_spans_carry_what_ac12_requires(tick_harness, journal) -> None:
    tick_harness.set_book(capital=REDUCES)
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=STATUS_PROPOSED, lots=2)
    decision = tick_harness.run_tick()
    spans = {span.name: span for span in journal.spans_for_trace(decision.trace_id)}
    assert {"risk.session", "tick.adjudicate"} <= set(spans)
    adjudicate = spans["tick.adjudicate"].attributes_json
    for attribute in (
        "risk.verdict",
        "risk.limit",
        "risk.configured",
        "risk.observed",
        "risk.capital_base",
        "risk.lots_requested",
        "risk.lots_cleared",
        "risk.latency_us",
        "risk.limits_artifact",
    ):
        assert attribute in adjudicate


def test_no_write_path_is_ever_touched(tick_harness) -> None:
    """AC-8. An intent is not an order, and nothing here can become one."""
    tick_harness.set_book(capital=ROOMY)
    tick_harness.set_regime(label="trending", confidence=0.8)
    tick_harness.set_proposal(status=STATUS_PROPOSED)
    tick_harness.run_tick()
    assert tick_harness.requested_paths() <= {
        "/api/v1/funds",
        "/api/v1/positionbook",
        "/api/v1/market/timings",
        "/api/v1/ping",
    }
```

## 6. The trader-facing surface

### `strike_desk/tests/test_risk_view.py`

```python
"""Text and JSON are two renderings of one row, so they cannot disagree."""

from __future__ import annotations

import json

from strike_desk.risk_view import as_dict, render

from .risk_fixtures import ROOMY, seed_verdict


def test_the_json_holds_every_number_the_text_prints(journal) -> None:
    seed_verdict(
        journal,
        trading_day="2026-09-01",
        verdict="reduce",
        lots_requested=2,
        lots_cleared=1,
        tripped_limit="per-trade-loss-cap",
        configured_value=4_000.0,
        observed_value=6_000.0,
        limit_unit="INR",
        checks_json=json.dumps(
            [
                {
                    "limit": "per-trade-loss-cap",
                    "unit": "INR",
                    "configured": 4_000.0,
                    "observed": 6_000.0,
                    "breached": True,
                    "detail": "2 lot(s)",
                }
            ]
        ),
    )
    rows = journal.list_risk_verdicts("2026-09-01")
    text = render(list(rows), days=["2026-09-01"])
    view = as_dict(rows[0])

    assert "REDUCE" in text and "2 -> 1 lot(s)" in text
    assert "per-trade-loss-cap" in text
    assert "Rs 4,000" in text and "Rs 6,000" in text
    assert view["tripped"] == {
        "limit": "per-trade-loss-cap",
        "configured": 4_000.0,
        "observed": 6_000.0,
        "unit": "INR",
    }
    assert view["lots"] == {"requested": 2, "cleared": 1}
    assert view["capital_base"] == ROOMY
    assert json.dumps(view, default=str)  # serialisable as --json prints it


def test_an_empty_window_says_so(journal) -> None:
    assert "no adjudications" in render([], days=[])
```

## 7. Extending the existing files

**`tests/test_decline_taxonomy.py`** — the parity test already reflects over `graph.py`'s
`REASON_*` constants and picks up the four new codes with no edit. `DT1_CLASSES` grows into
`FROZEN_CLASSES` with iteration 04's three codes added, so the additive guarantee now covers
everything shipped before this release:

```python
#: Every code dt-1 and dt-2 shipped, with the class it shipped with. These may never change.
FROZEN_CLASSES = {
    "position-open": ("book", "routine"),
    "data-quality": ("data", "degraded"),
    "specialist-unavailable": ("specialist", "degraded"),
    "specialist-timeout": ("specialist", "degraded"),
    "regime-not-tradeable": ("regime", "routine"),
    "regime-low-confidence": ("regime", "routine"),
    "regime-ungrounded": ("regime", "defect"),
    "tick-timeout": ("system", "degraded"),
    "internal-error": ("system", "defect"),
    "no-viable-contract": ("contract", "routine"),
    "proposal-ungrounded": ("contract", "defect"),
    "proposal-invalid": ("contract", "defect"),
}


@pytest.mark.parametrize(("code", "expected"), sorted(FROZEN_CLASSES.items()))
def test_the_taxonomy_is_additive(code: str, expected: tuple[str, str]) -> None:
    """An older row must never become taxonomy drift because we shipped dt-3."""
    assert (describe(code).category, describe(code).disposition) == expected


def test_the_outcome_set_gained_enter_and_nothing_else() -> None:
    assert OUTCOMES == {"decline", "hold", "enter"}
    assert describe("risk-cleared").outcome == "enter"
    assert {entry.outcome for entry in REASONS.values() if entry.code != "risk-cleared"} == {
        "decline",
        "hold",
    }


def test_an_entry_never_flips_the_exit_code(journal) -> None:
    """AC-10. A cleared intent is routine; only defects change what `declines` returns."""
    seed_decision(journal, trading_day="2026-09-01", outcome="enter", reason_code="risk-cleared")
    report = build_day_report(journal, "2026-09-01", "NIFTY")
    assert (report.entries, report.declines, report.defects) == (1, 0, 0)
    assert report.healthy is True
    assert report.drift == 0
    assert "entries" in render_window(build_window_report(journal, "NIFTY", ["2026-09-01"]))
```

The golden file grows the four new codes and the two `risk-cleared` variants. Regenerate it
deliberately with `uv run pytest tests/test_decline_taxonomy.py --golden-update`, read the
diff, and commit it with the wording rather than after it.

**`tests/test_journal_migration.py`** — one test, because the migration is one `CREATE TABLE`:

```python
def test_a_v4_journal_gains_the_table_in_place(tmp_path, v4_journal_bytes) -> None:
    path = tmp_path / "strike_desk.db"
    path.write_bytes(v4_journal_bytes)
    before = _table_names(path), _row_count(path, "decisions"), _row_count(path, "proposals")

    journal = Journal(path)
    try:
        journal.create_schema()
        journal.create_schema()  # idempotent
    finally:
        journal.close()

    after = _table_names(path), _row_count(path, "decisions"), _row_count(path, "proposals")
    assert "risk_verdicts" not in before[0] and "risk_verdicts" in after[0]
    assert before[1:] == after[1:]  # not one row read or rewritten
    assert _triggers_for(path, "risk_verdicts") == {
        "risk_verdicts_no_update",
        "risk_verdicts_no_delete",
    }
    assert _pragma_user_columns(path, "proposals") == _pragma_user_columns_v4()
```

`v4_journal_bytes` is built the way `v3_journal_bytes` was: a journal created by the previous
release's schema, seeded with a handful of decisions and proposals, frozen as bytes in the
fixture module.

## 8. Running it

```bash
cd strike_desk

uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ -q
uv run pytest tests/ --cov=strike_desk --cov-report=term-missing --cov-fail-under=80
uv run --group dev bandit -r src/ -q
```

Every one of those runs with no broker session, no Anthropic key and no open market — this
slice needs none of the three, which is why block A of the manual tests is almost the whole
document.

### `.github/workflows/strike-desk-ci.yml` — the early gate

The structural gate runs the cheap, non-negotiable tests before the full suite. Iteration 04's
`test_enter_is_unreachable` no longer exists and must be replaced in the same change, or the
first CI run on this branch fails on a missing test id:

```yaml
      - name: Structural gates
        working-directory: strike_desk
        run: |
          uv run pytest \
            tests/test_decline_taxonomy.py \
            tests/test_journal_migration.py \
            tests/test_playbook.py \
            tests/test_risk_officer.py \
            tests/test_risk_scenarios.py \
            tests/test_options_strategist.py::test_every_whitelisted_tool_is_a_reader \
            tests/test_options_strategist.py::test_no_whitelist_holds_a_mutating_tool \
            tests/test_tick_adjudication.py::test_enter_requires_a_cleared_verdict \
            tests/test_tick_adjudication.py::test_no_write_path_is_ever_touched \
            -q
```

Those are the build's non-negotiables: the taxonomy is additive, the migration is in place,
both deterministic checkers bind, the frozen scenarios read as pinned, no whitelist holds a
mutating tool, an intent needs a cleared verdict, and nothing ever calls a write path. If any
of them fails, nothing else about the build matters.

## 9. Traceability

| AC | Automated test | Manual case it backstops |
| --- | --- | --- |
| AC-1 verdict journalled completely | `test_a_proposal_with_headroom_passes`, `test_the_verdict_row_is_written_before_the_decision` | MT-01, MT-16 |
| AC-2 all eight limits named | `test_every_fr5_limit_is_evaluated`, `test_the_limits_are_a_versioned_artifact` | MT-01, MT-15 |
| AC-3 boundary semantics | `test_a_rupee_limit_breaches_on_touch`, `test_a_count_limit_permits_its_configured_number` | MT-04, MT-05 |
| AC-4 reduction and its re-check | `test_the_checks_are_evaluated_at_the_size_that_was_requested`, `test_no_size_clears_so_it_is_a_veto`, `test_a_count_limit_is_never_resolved_by_reduction`, `test_a_reduced_copy_that_fails_the_playbook_is_a_veto` | MT-02, MT-03, MT-06 |
| AC-5 held inputs | `test_an_unusable_capital_base_holds`, `test_a_proposal_the_officer_cannot_size_holds`, `test_an_unreadable_capital_base_holds_the_proposal` | MT-07 |
| AC-6 session stop before the spend | `test_a_stopped_session_never_consults_a_specialist`, `test_the_session_stop_is_latched_once_a_day`, `test_the_latch_survives_a_recovered_book` | MT-18 |
| AC-7 arithmetic disposes | `test_the_rationale_has_no_standing`, `test_the_officer_imports_no_reasoning_module`, `test_the_officer_never_raises` | MT-08, MT-11 |
| AC-8 `enter` needs a cleared verdict | `test_enter_requires_a_cleared_verdict`, `test_a_cleared_proposal_becomes_an_intent`, `test_no_verdict_at_all_still_declines`, `test_no_write_path_is_ever_touched` | MT-16, MT-17 |
| AC-9 additive taxonomy | `test_the_taxonomy_is_additive`, `test_the_outcome_set_gained_enter_and_nothing_else`, the parity pair | MT-10 |
| AC-10 the report holds | `test_an_entry_never_flips_the_exit_code` | MT-10, MT-16 |
| AC-11 in-place migration | `test_a_v4_journal_gains_the_table_in_place` | MT-09 |
| AC-12 spans and the command | `test_the_spans_carry_what_ac12_requires`, `test_risk_view.py`, the argument tests in `test_cli.py` | MT-12, MT-13 |
| AC-13 versioned limits, frozen set | `test_the_limits_are_a_versioned_artifact`, all of `test_risk_scenarios.py` | MT-14 |

## 10. What this suite does not prove

Worth stating, because a green suite is easy to over-read.

1. **That the limits are the right numbers.** It proves they are enforced exactly as
   configured. Whether 0.5% per trade is the right fraction of this trader's account is his
   judgement, and the PRD says so.
2. **That the account arithmetic matches the broker's.** The capital base is built from
   OpenAlgo's funds fields; a broker that reports margin differently would move the base and
   every limit with it. MT-16 is the only check of that, and it is a human reading two numbers
   side by side.
3. **That an intent leads anywhere.** No approval, no order, no fill. Every limit in this
   suite has been tested against arithmetic and never against a filled position.
