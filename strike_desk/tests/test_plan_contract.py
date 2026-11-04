"""Plan-node contract: live_symbols arity, broker_token_error, getattr fallbacks."""

from __future__ import annotations

from importlib.machinery import SourceFileLoader
from pathlib import Path

import httpx
import pytest

from strike_desk.openalgo_client import OpenAlgoClient
from strike_desk.plan_contract import (
    call_broker_token_error,
    call_live_symbols,
    call_open_approvals,
    call_session_stopped,
)

_GUARD = SourceFileLoader(
    "check_analyzer_plan_contract",
    str(Path(__file__).resolve().parents[1] / "scripts" / "check-analyzer-plan-contract.py"),
).load_module()


class Bare:
    pass


class OneArgJournal:
    def live_symbols(self):
        return {"NIFTYLIVECE"}


@pytest.mark.parametrize(
    "index_symbol",
    ["NIFTY", "BANKNIFTY", "SENSEX", "CRUDEOIL", "BTC", "ETH"],
)
def test_call_live_symbols_all_boards(journal, index_symbol):
    day = "2026-11-04"
    assert call_live_symbols(journal) == frozenset()
    assert call_live_symbols(journal, day) == frozenset()
    assert call_live_symbols(Bare()) == frozenset()
    assert index_symbol
    assert call_live_symbols(OneArgJournal(), day) == frozenset({"NIFTYLIVECE"})


def test_call_broker_token_error_getattr():
    assert call_broker_token_error(Bare()) is None
    assert hasattr(OpenAlgoClient, "broker_token_error")


def test_call_broker_token_error_ok(client: OpenAlgoClient, openalgo):
    openalgo.post("/api/v1/quotes").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": {"ltp": 1}})
    )
    openalgo.post("/api/v1/history").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": []})
    )
    assert call_broker_token_error(client) is None


def test_session_stopped_and_approvals_getattr(journal):
    assert call_session_stopped(journal, "2026-11-04") is False
    assert call_session_stopped(Bare(), "2026-11-04") is False
    assert call_open_approvals(journal) == []
    assert call_open_approvals(Bare()) == []


def test_deploy_guard_rejects_slim_journal():
    errors = _GUARD.check_journal(
        "class Journal:\n    def live_symbols(self):\n        return []\n"
    )
    assert any("PositionRow" in item for item in errors)


def test_deploy_guard_accepts_host_shaped_journal():
    source = """
class PositionRow:
    pass
class Journal:
    def live_symbols(self, trading_day=None):
        return frozenset()
    def session_stopped(self, trading_day):
        return False
    def open_approvals(self):
        return []
"""
    assert _GUARD.check_journal(source) == []


def test_deploy_guard_requires_getattr_on_graph():
    bare = (
        "live = journal.live_symbols(trading_day)\n"
        "token_dead = deps.client.broker_token_error()\n"
    )
    assert _GUARD.check_graph(bare)
    safe = """
probe = getattr(journal, "live_symbols", None)
live = probe(trading_day) if callable(probe) else frozenset()
probe = getattr(deps.client, "broker_token_error", None)
token_dead = probe() if callable(probe) else None
stop = getattr(deps.journal, "session_stopped", None)
appr = getattr(deps.journal, "open_approvals", None)
"""
    assert _GUARD.check_graph(safe) == []
    assert (
        _GUARD.check_client(
            "class C:\n    def broker_token_error(self):\n        return None\n"
        )
        == []
    )
