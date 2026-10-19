"""Aggregate liveness of the trading stack for the dashboard.

OpenAlgo, the broker session, the WebSocket feed, Strike Desk (the mind),
and StrikeShield (Hands supervisor) must be readable in one glance. The desk
watchdog writes a world-readable snapshot so this process never needs to
enter /var/lib/strike-desk (0750, strikedesk-only).
"""

from __future__ import annotations

import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)

STACK_STATUS_PATH = Path(os.getenv("STRIKE_DESK_STACK_STATUS", "/var/tmp/strike-desk-stack.json"))
SHIELD_STALE_SECONDS = 90
OFA_STALE_SECONDS = 90


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _age_seconds(updated_at: str | None) -> float | None:
    if not updated_at:
        return None
    try:
        stamp = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return max(0.0, (_now() - stamp.astimezone(UTC)).total_seconds())
    except ValueError:
        return None


def _read_desk_snapshot() -> dict[str, Any]:
    try:
        raw = json.loads(STACK_STATUS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        logger.exception("stack status snapshot unreadable")
        return {}
    return raw if isinstance(raw, dict) else {}


def _socket_up(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _component(component_id: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"id": component_id, "label": label, "status": status, "detail": detail}


def build_stack_status(*, analyze_mode: bool, broker: str | None, broker_connected: bool) -> dict[str, Any]:
    """Return the dashboard payload. Never raises."""
    snapshot = _read_desk_snapshot()
    ws_port = int(os.getenv("WEBSOCKET_PORT", "8765"))
    ws_up = _socket_up("127.0.0.1", ws_port)

    desk_active = str(snapshot.get("strike_desk") or "") == "active"
    shield_enabled = bool(snapshot.get("strikeshield_enabled"))
    heartbeat_age = snapshot.get("strikeshield_heartbeat_age_s")
    try:
        heartbeat_age_s = float(heartbeat_age) if heartbeat_age is not None else None
    except (TypeError, ValueError):
        heartbeat_age_s = None
    ofa_enabled = bool(snapshot.get("ofa_enabled"))
    ofa_heartbeat_age = snapshot.get("ofa_heartbeat_age_s")
    try:
        ofa_heartbeat_age_s = float(ofa_heartbeat_age) if ofa_heartbeat_age is not None else None
    except (TypeError, ValueError):
        ofa_heartbeat_age_s = None
    ofa_unit_active = str(snapshot.get("ofa_active") or "") == "active"
    snap_age = _age_seconds(snapshot.get("ts"))
    if snap_age is not None and snap_age > 180:
        desk_active = False

    set1 = bool(snapshot.get("set1"))
    set2 = bool(snapshot.get("set2open"))
    if set1 and set2:
        book = "wings + shorts"
    elif set1:
        book = "wings open"
    elif set2:
        book = "shorts open"
    else:
        book = "flat"

    ofa_fresh = (
        ofa_enabled
        and ofa_unit_active
        and ofa_heartbeat_age_s is not None
        and ofa_heartbeat_age_s <= OFA_STALE_SECONDS
    )

    components = [
        _component(
            "openalgo",
            "ZeroTouchAlgo",
            "pass",
            "this instance is answering",
        ),
        _component(
            "analyze",
            "Analyze",
            "pass" if analyze_mode else "warn",
            "on (sandbox fills)" if analyze_mode else "off — live orders",
        ),
        _component(
            "broker",
            "Broker",
            "pass" if broker_connected else "fail",
            (broker or "session") if broker_connected else "not connected",
        ),
        _component(
            "websocket",
            "Market data",
            "pass" if ws_up else "fail",
            f"127.0.0.1:{ws_port}" if ws_up else f"port {ws_port} down",
        ),
        _component(
            "mind",
            "Strike Desk",
            "pass" if desk_active else "fail",
            "24x7 mind up" if desk_active else "mind down — positional books are unprotected",
        ),
        _component(
            "strikeshield",
            "StrikeShield",
            (
                "pass"
                if shield_enabled and heartbeat_age_s is not None and heartbeat_age_s <= SHIELD_STALE_SECONDS
                else ("warn" if not shield_enabled else "fail")
            ),
            (
                f"step {int(heartbeat_age_s)}s ago"
                if heartbeat_age_s is not None
                else ("off" if not shield_enabled else "no heartbeat")
            ),
        ),
        _component(
            "ofa",
            "OFA",
            "pass" if ofa_fresh else ("warn" if not ofa_enabled else "fail"),
            (
                f"step {int(ofa_heartbeat_age_s)}s ago"
                if ofa_heartbeat_age_s is not None
                else ("off" if not ofa_enabled else "no heartbeat")
            ),
        ),
        _component(
            "hands",
            "Hands book",
            "pass" if desk_active else "fail",
            book,
        ),
    ]

    ranks = {"fail": 2, "warn": 1, "pass": 0}
    overall = "pass"
    for row in components:
        if ranks[row["status"]] > ranks[overall]:
            overall = row["status"]

    return {
        "overall": overall,
        "updated_at": _now().isoformat(),
        "components": components,
    }


def write_desk_snapshot(
    path: Path,
    *,
    openalgo: str,
    strike_desk: str,
    strikeshield_enabled: bool,
    strikeshield_heartbeat_age_s: float | None,
    set1: bool = False,
    set2open: bool = False,
    ofa_enabled: bool = False,
    ofa_active: str = "inactive",
    ofa_heartbeat_age_s: float | None = None,
) -> None:
    """Called by the host watchdog (root) so OpenAlgo can read the mind."""
    payload = {
        "ts": _now().isoformat(),
        "openalgo": openalgo,
        "strike_desk": strike_desk,
        "strikeshield_enabled": strikeshield_enabled,
        "strikeshield_heartbeat_age_s": strikeshield_heartbeat_age_s,
        "ofa_enabled": ofa_enabled,
        "ofa_active": ofa_active,
        "ofa_heartbeat_age_s": ofa_heartbeat_age_s,
        "set1": set1,
        "set2open": set2open,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass
