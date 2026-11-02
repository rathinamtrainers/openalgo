"""Live BTC M1 Sniper. Same Hands token as CoA; own flags and state file."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .config import IST, Settings
from .errors import OpenAlgoError
from .ltp_m1_sniper import (
    M1Bar,
    decide_m1_exit,
    evaluate_m1,
    halt_until,
)
from .tradetron_hands import M1_BUY_CE, M1_BUY_PE, M1_EXIT, CoaHands, NoopCoaHands

logger = logging.getLogger(__name__)

_M1_SYMBOL = "BTCUSD"
_M1_LOOKBACK = 3 * 3600
_M5_LOOKBACK = 8 * 3600
_STATE_FILE = "coa_live_m1_btc.json"


class M1SniperRunner:
    """Closed-bar M1 entries. Never writes coa_live_btc.json or ltp_* flags."""

    def __init__(self, settings: Settings, client: Any, hands: CoaHands | NoopCoaHands) -> None:
        self._settings = settings
        self._client = client
        self._hands = hands
        self.board = SimpleNamespace(slug="btc-m1")
        self._state_path = settings.state_dir / _STATE_FILE

    def touch_heartbeat(self, now: datetime | None = None) -> None:
        del now

    def step(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(tz=IST)
        sent: list[str] = []
        if self._settings.kill_switch_path.exists():
            logger.info("m1 sniper skipped: kill switch")
            return sent
        fetch = getattr(self._client, "candles", None)
        if not callable(fetch):
            logger.info("m1 sniper skipped: no candles")
            return sent
        end = int(now.timestamp())
        try:
            m1_raw = fetch(_M1_SYMBOL, "1m", end - _M1_LOOKBACK, end)
            m5_raw = fetch(_M1_SYMBOL, "5m", end - _M5_LOOKBACK, end)
        except OpenAlgoError as exc:
            logger.warning("m1 sniper candles failed: %s", exc)
            raise
        m1 = _closed_bars(m1_raw, now, timedelta(minutes=1))
        m5 = _closed_bars(m5_raw, now, timedelta(minutes=5))
        state = _load_state(self._state_path)
        consecutive = int(state.get("consecutive_stops") or 0)
        halted = str(state.get("halted_until") or "")
        today = now.astimezone(IST).date().isoformat()
        if halted and today >= halted:
            consecutive = 0
            halted = ""
            state["consecutive_stops"] = 0
            state["halted_until"] = ""
        snap = evaluate_m1(
            m1,
            m5,
            now,
            consecutive_stops=consecutive,
            halted_until=halted,
        )
        spot = m1[-1].close if m1 else 0.0
        open_trade = state.get("open")
        if open_trade is not None:
            sl = float(open_trade.get("sl") or 0)
            tp = float(open_trade.get("tp") or 0)
            side = str(open_trade.get("side") or "")
            reason = decide_m1_exit(spot, side, sl, tp)
            if reason:
                if self._send(M1_EXIT):
                    sent.append(M1_EXIT)
                    state["open"] = None
                    state["last_exit"] = now.isoformat()
                    if "SL" in reason:
                        consecutive += 1
                        state["consecutive_stops"] = consecutive
                        if consecutive >= 2:
                            state["halted_until"] = halt_until(now)
                            logger.info("m1 sniper two-strike halt until %s", state["halted_until"])
                    else:
                        state["consecutive_stops"] = 0
                    logger.info("m1 sniper exit %s spot=%.2f", reason, spot)
                _save_state(self._state_path, state)
                return sent
            _save_state(self._state_path, state)
            return sent

        last_bar = str(state.get("last_bar") or "")
        bar_key = m1[-1].ts.isoformat() if m1 else ""
        if bar_key and bar_key == last_bar:
            return sent
        state["last_bar"] = bar_key
        if snap.signal == "BUY_CE":
            if self._send(M1_BUY_CE):
                sent.append(M1_BUY_CE)
                state["open"] = _open_dict("CE", snap, now)
                logger.info("m1 sniper BUY_CE spot=%.2f", snap.entry or 0)
        elif snap.signal == "BUY_PE":
            if self._send(M1_BUY_PE):
                sent.append(M1_BUY_PE)
                state["open"] = _open_dict("PE", snap, now)
                logger.info("m1 sniper BUY_PE spot=%.2f", snap.entry or 0)
        _save_state(self._state_path, state)
        return sent

    def _send(self, action: str) -> bool:
        try:
            result = self._hands.send(action, board="btc-options")
        except Exception:
            logger.exception("m1 hands %s failed", action)
            return False
        if result is None:
            return True
        try:
            status = int(getattr(result, "http_status", 200))
        except (TypeError, ValueError):
            return False
        if status == 0:
            return False
        return 200 <= status < 300


def make_m1_runner(settings: Settings, client: Any, hands: CoaHands | NoopCoaHands) -> M1SniperRunner:
    return M1SniperRunner(settings, client, hands)


def _open_dict(side: str, snap, now: datetime) -> dict[str, Any]:
    return {
        "side": side,
        "entry": snap.entry,
        "entry_ts": now.isoformat(),
        "sl": snap.sl,
        "tp": snap.tp,
    }


def _closed_bars(rows: list[dict[str, Any]], now: datetime, width: timedelta) -> list[M1Bar]:
    out: list[M1Bar] = []
    now_ts = now.timestamp()
    width_s = width.total_seconds()
    for raw in rows:
        try:
            ts = int(raw.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if ts <= 0 or now_ts - ts < width_s:
            continue
        close = float(raw.get("close") or 0)
        if close <= 0:
            continue
        stamp = datetime.fromtimestamp(ts, tz=timezone.utc)
        out.append(
            M1Bar(
                ts=stamp,
                open=float(raw.get("open") or close),
                high=float(raw.get("high") or close),
                low=float(raw.get("low") or close),
                close=close,
            )
        )
    out.sort(key=lambda bar: bar.ts)
    return out


def _load_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return {"open": None, "consecutive_stops": 0, "halted_until": ""}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
