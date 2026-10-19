"""Live OFA: OpenAlgo depth -> flags -> Analyzer fill + Tradetron Hands."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import IST, Settings
from .errors import AnalyzerOff, OpenAlgoError
from .ofa import (
    OfaSnapshot,
    SessionState,
    evaluate,
    format_snapshot,
    monthly_fut_symbol,
    parse_depth,
)
from .ofa_analyzer import OfaAnalyzer
from .ofa_hands import OFA_BUY_CE, OFA_BUY_PE, OFA_EXIT, OfaHands
from .openalgo_client import OpenAlgoClient

logger = logging.getLogger(__name__)


class OfaRunner:
    """One depth read, one snapshot. Fires Analyzer + Hands only on a signal change."""

    def __init__(
        self,
        settings: Settings,
        client: OpenAlgoClient,
        *,
        hands: OfaHands | None = None,
        analyzer: OfaAnalyzer | None = None,
        expiry_date_for=None,
        is_trading_day=None,
        trading_window=None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._hands = hands
        self._analyzer = analyzer
        self._expiry_date_for = expiry_date_for
        self._is_trading_day = is_trading_day
        self._trading_window = trading_window
        self._state_path = settings.state_dir / "ofa.json"
        self._exec_path = settings.state_dir / "ofa_exec.json"
        self.last_actions: list[str] = []

    def symbol(self, now: datetime | None = None) -> tuple[str, str]:
        configured = (self._settings.ofa_symbol or "").strip()
        exchange = (self._settings.ofa_exchange or "NFO").strip().upper()
        if configured:
            return configured, exchange
        day = (now or datetime.now(tz=IST)).date()
        return monthly_fut_symbol(self._settings.index_symbol, day), exchange

    def step(self, now: datetime | None = None) -> OfaSnapshot:
        now = now or datetime.now(tz=IST)
        symbol, exchange = self.symbol(now)
        raw = self._client.depth(symbol, exchange)
        book = parse_depth(raw, symbol=symbol, exchange=exchange)
        if book.ltp <= 0:
            raise OpenAlgoError(f"OFA {symbol}: empty depth")
        state = _load_state(self._state_path)
        snap, nxt = evaluate(
            book,
            state,
            now=now,
            tick=self._settings.ofa_tick,
            wall_mult=self._settings.ofa_wall_mult,
            thin_total=self._settings.ofa_thin_total,
        )
        _save_state(self._state_path, nxt)
        self.last_actions = self._act(snap, now)
        logger.info(
            "ofa %s signal=%s of_long=%s of_short=%s of_exit=%s %s",
            symbol,
            snap.signal,
            snap.of_long,
            snap.of_short,
            snap.of_exit,
            snap.reason,
        )
        return snap

    def _act(self, snap: OfaSnapshot, now: datetime) -> list[str]:
        if self._settings.kill_switch_path.exists():
            logger.info("ofa fill skipped: kill switch")
            return []
        if not self._in_session(now):
            return []
        exec_state = _load_exec(self._exec_path)
        prev = exec_state.get("acted_signal") or "FLAT"
        if snap.signal == prev:
            return []
        expiry_day = self._expiry_day(now.date())
        actions: list[str] = []
        if prev in {"LONG", "SHORT"} and snap.signal != prev:
            self._close_analyzer(exec_state)
            if snap.signal == "FLAT":
                self._send_hands(OFA_EXIT)
                exec_state["acted_signal"] = "FLAT"
                exec_state["analyzer_symbol"] = ""
                exec_state["analyzer_qty"] = 0
                _save_exec(self._exec_path, exec_state)
                return ["exit"]
            actions.append("exit")
        if snap.signal == "LONG":
            self._open_analyzer(exec_state, "CE", expiry_day)
            self._send_hands(OFA_BUY_CE)
            exec_state["acted_signal"] = "LONG"
            actions.append("buy_ce")
        elif snap.signal == "SHORT":
            self._open_analyzer(exec_state, "PE", expiry_day)
            self._send_hands(OFA_BUY_PE)
            exec_state["acted_signal"] = "SHORT"
            actions.append("buy_pe")
        _save_exec(self._exec_path, exec_state)
        return actions

    def _in_session(self, now: datetime) -> bool:
        if self._trading_window is not None:
            window = self._trading_window(now.date())
            if window is None:
                return False
            start, end = window
            return start <= now <= end
        if self._is_trading_day is not None and not self._is_trading_day(now.date()):
            return False
        return True

    def _expiry_day(self, day: date) -> date | None:
        if self._expiry_date_for is None:
            return None
        return self._expiry_date_for(day)

    def _open_analyzer(
        self,
        exec_state: dict[str, Any],
        option_type: str,
        expiry_day: date | None,
    ) -> None:
        if self._analyzer is None:
            return
        if expiry_day is None:
            logger.info("ofa analyzer skipped: no weekly expiry")
            return
        try:
            symbol, qty = self._analyzer.resolve_atm(option_type, expiry_day)
            self._analyzer.place(symbol, "BUY", qty)
        except AnalyzerOff as exc:
            logger.info("ofa analyzer refused: %s", exc)
            return
        exec_state["analyzer_symbol"] = symbol
        exec_state["analyzer_qty"] = qty

    def _close_analyzer(self, exec_state: dict[str, Any]) -> None:
        if self._analyzer is None:
            return
        symbol = str(exec_state.get("analyzer_symbol") or "")
        qty = int(exec_state.get("analyzer_qty") or 0)
        if not symbol or qty <= 0:
            return
        try:
            self._analyzer.place(symbol, "SELL", qty)
        except AnalyzerOff as exc:
            logger.info("ofa analyzer refused: %s", exc)

    def _send_hands(self, action: str) -> None:
        if self._hands is None:
            logger.info("ofa hands skipped: not configured")
            return
        self._hands.send(action)


def _load_state(path: Path) -> SessionState:
    if not path.exists():
        return SessionState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SessionState()
    return SessionState.from_dict(raw if isinstance(raw, dict) else None)


def _save_state(path: Path, state: SessionState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict()), encoding="utf-8")


def _load_exec(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"acted_signal": "FLAT", "analyzer_symbol": "", "analyzer_qty": 0}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"acted_signal": "FLAT", "analyzer_symbol": "", "analyzer_qty": 0}
    if not isinstance(raw, dict):
        return {"acted_signal": "FLAT", "analyzer_symbol": "", "analyzer_qty": 0}
    signal = raw.get("acted_signal") or "FLAT"
    if signal not in {"LONG", "SHORT", "FLAT"}:
        signal = "FLAT"
    try:
        qty = int(raw.get("analyzer_qty") or 0)
    except (TypeError, ValueError):
        qty = 0
    return {
        "acted_signal": signal,
        "analyzer_symbol": str(raw.get("analyzer_symbol") or ""),
        "analyzer_qty": qty,
    }


def _save_exec(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def render(snap: OfaSnapshot, actions: list[str] | None = None) -> str:
    text = format_snapshot(snap)
    if actions:
        text += f"\n  fired={' '.join(actions)}"
    return text
