"""Map an OpenAlgo / Strike Desk option-chain payload onto fade fields."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .types import ChainSnapshot, StrikeRow


def _side(row: dict[str, Any], name: str) -> dict[str, Any] | None:
    raw = row.get(name) or row.get(name.upper()) or row.get(name.lower())
    return raw if isinstance(raw, dict) else None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _volume(value: Any) -> float | None:
    parsed = _float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _lot_size(ce: dict[str, Any] | None, pe: dict[str, Any] | None) -> int:
    for side in (ce, pe):
        if not side:
            continue
        raw = side.get("lotsize") if side.get("lotsize") is not None else side.get("lot_size")
        parsed = _float(raw)
        if parsed is not None and parsed > 0:
            return int(parsed)
    return 0


def snapshot_from_chain(payload: dict[str, Any], *, as_of: datetime) -> ChainSnapshot | None:
    """Accept the OpenAlgo ``get_option_chain`` body (or its ``data`` wrapper)."""
    body = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(body, dict):
        return None
    spot = _float(body.get("underlying_ltp") or body.get("spot") or body.get("ltp"))
    rows_raw = body.get("chain") or body.get("strikes") or []
    if spot is None or not isinstance(rows_raw, list):
        return None

    rows: list[StrikeRow] = []
    for item in rows_raw:
        if not isinstance(item, dict):
            continue
        strike = _float(item.get("strike"))
        if strike is None:
            continue
        ce = _side(item, "ce")
        pe = _side(item, "pe")
        rows.append(
            StrikeRow(
                strike=int(strike),
                ce_ltp=_float((ce or {}).get("ltp")),
                pe_ltp=_float((pe or {}).get("ltp")),
                ce_volume=_volume((ce or {}).get("volume")),
                pe_volume=_volume((pe or {}).get("volume")),
                ce_symbol=str((ce or {}).get("symbol") or ""),
                pe_symbol=str((pe or {}).get("symbol") or ""),
                lot_size=_lot_size(ce, pe),
                ce_oi=_float((ce or {}).get("oi")),
                pe_oi=_float((pe or {}).get("oi")),
            )
        )
    if not rows:
        return None
    expiry = str(body.get("expiry_date") or body.get("expiry") or "")
    return ChainSnapshot(spot=spot, expiry=expiry, rows=tuple(rows), as_of=as_of)
