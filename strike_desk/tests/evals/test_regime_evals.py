"""LLMOps: the shipped prompt, a live model, and frozen snapshots."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from strike_desk.config import Settings
from strike_desk.mcp_toolbox import REGIME_TOOLS
from strike_desk.model_client import build_regime_model
from strike_desk.prompt_registry import PromptRegistry
from strike_desk.regime_analyst import STATUS_OK, STATUS_UNGROUNDED, RegimeAnalyst
from tests.conftest import PACKAGED_PROMPTS, CannedTool, LocalToolSource
from tests.test_regime_analyst import a_request

pytestmark = [
    pytest.mark.evals,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="live regime evals need ANTHROPIC_API_KEY",
    ),
]

CASES = json.loads((Path(__file__).parent / "regime_cases.json").read_text(encoding="utf-8"))
LABEL_AGREEMENT_FLOOR = 0.80


@pytest.fixture(scope="module")
def live_settings(tmp_path_factory):
    """The shipped configuration, with the real key and a realistic budget."""
    return Settings(
        openalgo_api_key="unused-by-the-eval-suite",
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        state_dir=tmp_path_factory.mktemp("evals"),
        prompts_dir=PACKAGED_PROMPTS,
        specialist_timeout_seconds=40.0,
        regime_deadline_margin_seconds=3.0,
    )


@pytest.fixture(scope="module")
def live_prompts(live_settings) -> PromptRegistry:
    return PromptRegistry.load(live_settings.prompts_dir)


def read_case(case, settings, prompts):
    tools = [CannedTool(name=name, output=output) for name, output in case["tools"].items()]
    analyst = RegimeAnalyst(settings, prompts, LocalToolSource(tools), build_regime_model(settings))
    return analyst.run(a_request(tick_id=f"eval:{case['id']}")).payload


@pytest.fixture(scope="module")
def outcomes(live_settings, live_prompts):
    """Run every case once, and share the answers across the assertions below."""
    return {
        case["id"]: (case, read_case(case, live_settings, live_prompts)) for case in CASES
    }


def test_label_agreement_clears_the_floor(outcomes):
    agreed = [
        case_id
        for case_id, (case, payload) in outcomes.items()
        if payload.get("label") in case["expect"]
    ]
    ratio = len(agreed) / len(outcomes)
    disagreed = {
        case_id: (payload.get("label"), case["expect"])
        for case_id, (case, payload) in outcomes.items()
        if payload.get("label") not in case["expect"]
    }
    assert ratio >= LABEL_AGREEMENT_FLOOR, f"agreement {ratio:.0%}; misses: {disagreed}"


def test_no_case_produces_an_ungrounded_submission(outcomes):
    offenders = {
        case_id: payload["defect"]
        for case_id, (_case, payload) in outcomes.items()
        if payload["status"] == STATUS_UNGROUNDED
    }
    assert not offenders, offenders


def test_every_answer_is_shaped_correctly(outcomes):
    for case_id, (_case, payload) in outcomes.items():
        assert payload["status"] in {STATUS_OK, "degraded"}, case_id
        if payload["status"] == STATUS_OK:
            assert 0.0 <= payload["confidence"] <= 1.0, case_id
            assert payload["evidence"], case_id
            assert len(payload["rationale"]) <= 320, case_id


def test_only_whitelisted_tools_are_ever_called(outcomes):
    called = {
        call["tool"] for _case, payload in outcomes.values() for call in payload["calls"]
    }
    assert called <= set(REGIME_TOOLS), called - set(REGIME_TOOLS)


def test_the_same_snapshot_yields_a_stable_label(live_settings, live_prompts):
    case = next(item for item in CASES if item["id"] == "strong-uptrend")
    labels = {read_case(case, live_settings, live_prompts).get("label") for _ in range(3)}
    assert len(labels) == 1, f"unstable labels across three replays: {labels}"


def test_a_read_stays_inside_its_cost_envelope(outcomes):
    """A prompt that starts fetching everything shows up here before it shows up on the bill."""
    for case_id, (_case, payload) in outcomes.items():
        assert payload["model_calls"] <= 4, case_id
        assert payload["input_tokens"] + payload["output_tokens"] < 60_000, case_id
