"""The toolbox at its seams: selection, connection, and lifecycle guards."""

from __future__ import annotations

import pytest

from strike_desk.errors import McpUnavailable
from strike_desk.mcp_toolbox import REGIME_TOOLS, REQUIRED_TOOLS, McpToolbox, select_tools
from strike_desk.specialists import ROLE_REGIME
from tests.conftest import API_KEY, CannedTool


def tools_named(*names: str) -> list[CannedTool]:
    return [CannedTool(name=name) for name in names]


def test_whitelist_is_exactly_six_read_only_market_tools():
    assert REGIME_TOOLS == (
        "get_quote",
        "get_historical_data",
        "get_trend_snapshot",
        "get_momentum_snapshot",
        "get_volatility_snapshot",
        "get_expiry_dates",
    )


def test_selection_keeps_the_whitelist_and_drops_everything_else():
    loaded = tools_named(
        *REQUIRED_TOOLS, "place_order", "close_all_positions", "send_telegram_alert"
    )
    selected = select_tools(loaded)
    assert [tool.name for tool in selected] == list(REQUIRED_TOOLS)


@pytest.mark.parametrize("missing", REQUIRED_TOOLS)
def test_a_missing_tool_fails_the_session_closed(missing):
    loaded = tools_named(*[name for name in REQUIRED_TOOLS if name != missing])
    with pytest.raises(McpUnavailable, match=missing):
        select_tools(loaded)


def test_connection_names_the_interpreter_the_script_and_the_key(settings, tmp_path):
    python = tmp_path / "python"
    script = tmp_path / "mcpserver.py"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    script.write_text("print('hi')\n", encoding="utf-8")
    box = McpToolbox(
        settings.model_copy(update={"mcp_python": python, "mcp_server_script": script})
    )

    connection = box._connection()  # noqa: SLF001
    assert connection["transport"] == "stdio"
    assert connection["command"] == str(python)
    assert connection["args"] == [str(script), API_KEY, settings.openalgo_base_url]
    assert connection["cwd"] == str(script.parent)  # keeps the installed mcp package resolvable
    assert "TZ" in connection["env"]


@pytest.mark.parametrize("field", ["mcp_python", "mcp_server_script"])
def test_missing_binaries_raise_before_anything_is_spawned(settings, tmp_path, field):
    present = tmp_path / "present"
    present.write_text("x", encoding="utf-8")
    update = {"mcp_python": present, "mcp_server_script": present, field: tmp_path / "absent"}
    with pytest.raises(McpUnavailable):
        McpToolbox(settings.model_copy(update=update))._connection()  # noqa: SLF001


def test_using_a_stopped_toolbox_raises_rather_than_hanging(settings):
    box = McpToolbox(settings)
    with pytest.raises(McpUnavailable):
        box.tools(ROLE_REGIME)
    with pytest.raises(McpUnavailable):
        box.submit(lambda: None, timeout=1.0)
    box.close()  # idempotent, and safe on a toolbox that never started
