"""Safe plan-node helpers. Missing methods must not AttributeError the tick."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def call_live_symbols(journal: Any, trading_day: str | None = None) -> frozenset[str]:
    """graph.py plan: ``journal.live_symbols(trading_day)`` with optional-arg fallback."""
    probe = getattr(journal, "live_symbols", None)
    if not callable(probe):
        return frozenset()
    if trading_day is None:
        result = probe()
    else:
        try:
            result = probe(trading_day)
        except TypeError:
            result = probe()
    return frozenset(str(item).strip().upper() for item in (result or ()) if item)


def call_broker_token_error(client: Any) -> str | None:
    """graph.py ``_consult_ready``: missing method is not a dead token."""
    probe = getattr(client, "broker_token_error", None)
    if not callable(probe):
        return None
    return probe()


def call_session_stopped(journal: Any, trading_day: str) -> bool:
    probe = getattr(journal, "session_stopped", None)
    if not callable(probe):
        return False
    return bool(probe(trading_day))


def call_open_approvals(journal: Any) -> Sequence[Any]:
    probe = getattr(journal, "open_approvals", None)
    if not callable(probe):
        return []
    return list(probe() or [])
