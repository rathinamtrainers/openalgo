"""The monitor path holds no reasoning plane, and no entry outlives a gated exit path."""

from __future__ import annotations

import importlib
import sys

import pytest

REASONING_MODULES = (
    "strike_desk.regime_analyst",
    "strike_desk.options_strategist",
    "strike_desk.model_client",
    "strike_desk.mcp_toolbox",
    "strike_desk.prompt_registry",
    "anthropic",
)

MONITOR_PATH = (
    "strike_desk.levels",
    "strike_desk.price_feed",
    "strike_desk.exit_executor",
    "strike_desk.position_monitor",
)


def _saved_modules(*names: str) -> dict[str, object]:
    return {name: sys.modules.get(name) for name in names}


def _restore_modules(saved: dict[str, object]) -> None:
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def assert_no_reasoning_imports(module_name: str) -> None:
    saved = _saved_modules(*REASONING_MODULES, module_name)
    try:
        for name in (*REASONING_MODULES, module_name):
            sys.modules.pop(name, None)
        importlib.import_module(module_name)
        leaked = [name for name in REASONING_MODULES if name in sys.modules]
        assert leaked == [], f"{module_name} pulled in the reasoning plane: {leaked}"
    finally:
        _restore_modules(saved)


def test_no_reasoning_module_is_imported_by_the_monitor_path():
    saved = _saved_modules(*REASONING_MODULES, *MONITOR_PATH)
    try:
        for name in (*REASONING_MODULES, *MONITOR_PATH):
            sys.modules.pop(name, None)
        for name in MONITOR_PATH:
            importlib.import_module(name)
        leaked = [name for name in REASONING_MODULES if name in sys.modules]
        assert leaked == [], f"the monitor path pulled in the reasoning plane: {leaked}"
    finally:
        _restore_modules(saved)


def test_autonomy_imports_no_reasoning_module():
    """The guards that replace the human must be as unreachable from an agent as the exits are."""
    assert_no_reasoning_imports("strike_desk.autonomy")


def test_exit_paths_are_whitelisted():
    from strike_desk.execution_client import EXECUTION_PATHS
    from strike_desk.openalgo_client import READ_ONLY_PATHS

    assert "/api/v1/closeposition" in EXECUTION_PATHS
    assert "/api/v1/quotes" in READ_ONLY_PATHS
    assert "/api/v1/placeorder" not in READ_ONLY_PATHS


def test_the_tick_declines_when_the_exit_path_is_gated(monitor_settings, journal):
    from strike_desk.decline_taxonomy import TAXONOMY_VERSION, entry_for
    from strike_desk.exit_executor import exit_path_status

    class Gated:
        def analyze_mode(self) -> bool:
            return False

        def order_mode(self) -> str:
            return "semi_auto"

    status = exit_path_status(Gated())
    assert not status.ok
    entry = entry_for("exit-path-gated")
    assert TAXONOMY_VERSION == "dt-5"
    assert entry.outcome == "decline" and entry.disposition == "defect"


@pytest.mark.parametrize("quantity", [0, -1, -75])
def test_a_non_positive_quantity_never_reaches_the_wire(monitor_settings, quantity):
    from strike_desk.execution_client import ExecutionClient
    from strike_desk.exit_executor import ExitExecutor
    from strike_desk.openalgo_client import OpenAlgoClient

    executor = ExitExecutor(
        monitor_settings,
        ExecutionClient(monitor_settings),
        OpenAlgoClient(monitor_settings),
        "strike-desk-NIFTY",
    )
    assert executor.fire("X", "NFO", quantity).status == "failed"


def test_unattended_mode_cannot_be_enabled_without_a_mirror(monitor_settings, tmp_path):
    """A desk that cannot read OpenAlgo's order mode may not claim to be unattended."""
    from strike_desk.errors import MirrorUnavailable
    from strike_desk.openalgo_mirror import OpenAlgoMirror

    settings = monitor_settings.model_copy(
        update={"autonomy": "unattended", "openalgo_db_path": tmp_path / "missing.db"}
    )
    mirror = OpenAlgoMirror(settings)
    try:
        with pytest.raises(MirrorUnavailable):
            mirror.order_mode()
    finally:
        mirror.close()
