"""Recover a fill the HTTP client gave up on. No agent, no retry of placeorder."""

from __future__ import annotations

from typing import Any


def is_place_timeout(exc: BaseException) -> bool:
    """True when placeorder failed because the client stopped waiting, not a broker NAK."""
    blob = f"{type(exc).__name__} {exc}".lower()
    return "timeout" in blob or "timed out" in blob


def long_option_lots(
    book: list[dict[str, Any]], *, index_symbol: str
) -> list[dict[str, Any]]:
    """Open long option rows for this index. Quantity <= 0 is ignored."""
    prefix = index_symbol.strip().upper()
    found: list[dict[str, Any]] = []
    for row in book:
        symbol = str(row.get("symbol") or "").strip().upper()
        try:
            quantity = float(row.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        if quantity <= 0:
            continue
        if symbol.startswith(prefix) and symbol.endswith(("CE", "PE")):
            found.append(row)
    return found


def matching_long(book: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    """The open long row for one tradable symbol, if any."""
    target = symbol.strip().upper()
    for row in book:
        if str(row.get("symbol") or "").strip().upper() != target:
            continue
        try:
            quantity = float(row.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        if quantity > 0:
            return row
    return None
