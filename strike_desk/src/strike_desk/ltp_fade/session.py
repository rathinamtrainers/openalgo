"""Session-level 09:21 freeze shared by Jev, the strategist, and the watch."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from strike_desk.errors import OpenAlgoError
from strike_desk.openalgo_client import OpenAlgoClient

from .adapter import snapshot_from_chain
from .decide import evaluate_entry
from .engine import persist_freeze
from .exits import format_trailer
from .freeze import freeze_lines
from .reversal import REVERSAL_MODE
from .types import (
    FREEZE_DEADLINE,
    FREEZE_TIME,
    ChainSnapshot,
    FrozenLines,
    SessionState,
)

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)

_MONTHS = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)


def _to_ddmmmyy(raw: str) -> str | None:
    text = (raw or "").strip().upper().replace(" ", "")
    for fmt, size in (("%d-%b-%Y", 11), ("%d-%b-%y", 9), ("%d%b%y", 7), ("%Y-%m-%d", 10)):
        try:
            from datetime import datetime as dt

            parsed = dt.strptime(text[:size] if fmt.startswith("%d-%b") else text[:10], fmt)
            return f"{parsed.day:02d}{_MONTHS[parsed.month - 1]}{parsed.strftime('%y')}"
        except ValueError:
            continue
    compact = text.replace("-", "")
    if len(compact) >= 7 and compact[:2].isdigit():
        return compact[:7]
    return None


def current_week_expiry(dates: list[str], today) -> str | None:
    from datetime import datetime as dt

    parsed: list[tuple[Any, str]] = []
    for raw in dates:
        token = _to_ddmmmyy(str(raw))
        if not token:
            continue
        try:
            day = dt.strptime(token, "%d%b%y")
        except ValueError:
            continue
        if day.date() >= today:
            parsed.append((day.date(), token))
    parsed.sort()
    return parsed[0][1] if parsed else None


class FadeSession:
    """Freeze once, expose lines to Jev, detect first-touch tags, never place."""

    def __init__(self, snapshot_dir: Path | None = None) -> None:
        self.snapshot_dir = snapshot_dir
        self.lines: FrozenLines | None = None
        self.state_name = "PREOPEN"
        self.session_date = None
        self.entered_today = False
        self._chain: ChainSnapshot | None = None
        self._expiry: str | None = None
        self._armed_tag: str | None = None
        self._last_book: dict[str, Any] = {"state": "PREOPEN", "tag": None}

    def mark_entered(self) -> None:
        self.entered_today = True

    def trailer_for(self, side: str) -> str:
        lines = self.lines
        if lines is None:
            return format_trailer(side, None, None)
        if side == "CE":
            return format_trailer(side, lines.tgt_ce, lines.sl_ce_spot)
        return format_trailer(side, lines.tgt_pe, lines.sl_pe_spot)

    def as_book(self) -> dict[str, Any]:
        return dict(self._last_book)

    def consume_watch_trigger(self) -> bool:
        """True once per newly printed tag while flat."""
        tag = self._last_book.get("tag")
        if tag in {"CE", "PE"} and tag != self._armed_tag and not self.entered_today:
            self._armed_tag = str(tag)
            return True
        if tag not in {"CE", "PE"}:
            self._armed_tag = None
        return False

    def sync(self, client: OpenAlgoClient, now: datetime, settings: Any) -> dict[str, Any]:
        if now.tzinfo is None:
            self._last_book = {"state": "NO_TRADE", "tag": None, "reason": "clock_not_ist"}
            return self.as_book()
        ist = now.astimezone(IST)
        day = ist.date()
        clock = ist.time()
        if self.session_date != day or clock < FREEZE_TIME:
            self.lines = None
            self.state_name = "PREOPEN"
            self.session_date = day
            self.entered_today = False
            self._chain = None
            self._armed_tag = None
            if clock < FREEZE_TIME:
                self._last_book = {"state": "PREOPEN", "tag": None, "session_date": day.isoformat()}
                return self.as_book()

        if self.lines is None:
            self.state_name = "FREEZING"
            try:
                self._freeze(client, ist, settings)
            except OpenAlgoError as exc:
                logger.warning("ltp fade freeze fetch failed: %s", exc)
                if clock >= FREEZE_DEADLINE:
                    self.state_name = "NO_TRADE"
                    self._last_book = {"state": "NO_TRADE", "tag": None, "reason": "freeze_failed"}
                    return self.as_book()
                self._last_book = {"state": "FREEZING", "tag": None, "reason": str(exc)[:160]}
                return self.as_book()

        spot = self._spot(client, settings, fallback=self.lines.spot_at_freeze if self.lines else 0)
        tag = self._tag(ist, spot)
        self._last_book = self._pack(spot, tag)
        return self.as_book()

    def _freeze(self, client: OpenAlgoClient, now: datetime, settings: Any) -> None:
        if self._expiry is None:
            raw = client.expiry_dates(
                settings.index_symbol, settings.option_exchange, "options"
            )
            dates = raw.get("data") if isinstance(raw.get("data"), list) else []
            if isinstance(raw.get("data"), dict):
                dates = raw["data"].get("expirydates") or raw["data"].get("expiry_dates") or []
            self._expiry = current_week_expiry([str(item) for item in dates], now.date())
        if not self._expiry:
            raise OpenAlgoError("no live weekly expiry for the fade freeze")
        payload = client.optionchain(
            settings.index_symbol, settings.index_spot_exchange, self._expiry
        )
        snapshot = snapshot_from_chain(payload, as_of=now)
        if snapshot is None:
            raise OpenAlgoError("option chain could not be mapped for the fade freeze")
        lines = freeze_lines(snapshot)
        if lines is None:
            if now.time() >= FREEZE_DEADLINE:
                self.state_name = "NO_TRADE"
            raise OpenAlgoError("fade freeze unusable (no ATM)")
        self.lines = lines
        self._chain = snapshot
        self.state_name = "FROZEN"
        logger.info(
            "ltp fade freeze %s spot=%s atm=%s R=%s S=%s call_ok=%s put_ok=%s %s",
            now.date().isoformat(),
            lines.spot_at_freeze,
            lines.atm,
            lines.resistance,
            lines.support,
            lines.call_ok,
            lines.put_ok,
            REVERSAL_MODE,
        )
        if self.snapshot_dir is not None:
            persist_freeze(self.snapshot_dir / f"ltp-fade-{now.date().isoformat()}.json", lines)

    def _spot(self, client: OpenAlgoClient, settings: Any, fallback: float) -> float:
        try:
            quote = client.quotes(settings.index_symbol, settings.index_spot_exchange)
            ltp = quote.get("ltp")
            return float(ltp) if ltp is not None else fallback
        except (OpenAlgoError, TypeError, ValueError):
            return fallback

    def _tag(self, now: datetime, spot: float) -> str | None:
        if self.lines is None or self._chain is None or self.entered_today:
            return None
        probe = SessionState()
        probe.state = "FROZEN"
        probe.lines = self.lines
        probe.entered_today = self.entered_today
        decision = evaluate_entry(now, spot, self._chain, probe)
        if decision.action == "enter":
            return decision.side
        if decision.reason == "dual_tag":
            return "DUAL"
        return None

    def _pack(self, spot: float, tag: str | None) -> dict[str, Any]:
        lines = self.lines
        payload: dict[str, Any] = {
            "state": self.state_name,
            "tag": tag if tag in {"CE", "PE"} else None,
            "dual_tag": tag == "DUAL",
            "entered_today": self.entered_today,
            "spot": spot,
            "reversal_mode": REVERSAL_MODE,
            "hysteresis": "first_touch",
        }
        if lines is None:
            return payload
        payload.update(
            {
                "atm": lines.atm,
                "expiry": lines.expiry,
                "R": lines.resistance,
                "S": lines.support,
                "eor": lines.eor,
                "eor1": lines.eor1,
                "eor2": lines.eor2,
                "eos": lines.eos,
                "eos1": lines.eos1,
                "eos2": lines.eos2,
                "tgt_ce": lines.tgt_ce,
                "tgt_pe": lines.tgt_pe,
                "sl_ce_spot": lines.sl_ce_spot,
                "sl_pe_spot": lines.sl_pe_spot,
                "call_ok": lines.call_ok,
                "put_ok": lines.put_ok,
            }
        )
        return payload
