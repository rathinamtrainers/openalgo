"""Live CoA mind: 5-minute chain -> exits -> CoA Hands webhook. Not QuadShield."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from .config import IST, Settings
from .ltp_coa import evaluate
from .ltp_coa_brief import build_brief, tape_to_dict, update_tape, write_public_brief
from .ltp_coa_exits import (
    OpenTrade,
    can_reenter,
    decide_exit,
    in_entry_window,
)
from .ltp_coa_replay import expiry_token
from .openalgo_client import OpenAlgoClient
from .tradetron_hands import COA_BUY_CE, COA_BUY_PE, COA_EXIT, CoaHands

logger = logging.getLogger(__name__)


class CoaLiveRunner:
    def __init__(
        self,
        settings: Settings,
        client: OpenAlgoClient,
        hands: CoaHands,
        *,
        is_trading_day,
        expiry_date_for,
        trading_window=None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._hands = hands
        self._is_trading_day = is_trading_day
        self._expiry_date_for = expiry_date_for
        self._trading_window = trading_window
        self._state_path = settings.state_dir / "coa_live.json"

    def step(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(tz=IST)
        sent: list[str] = []
        if self._settings.kill_switch_path.exists():
            logger.info("coa live skipped: kill switch")
            return sent
        window = self._trading_window(now.date()) if self._trading_window is not None else None
        if window is None and not self._is_trading_day(now.date()):
            logger.info("coa live skipped: market closed")
            return sent
        if window is not None:
            start, end = window
            if not (start <= now <= end):
                logger.info("coa live skipped: outside session")
                return sent

        expiry_day = self._expiry_date_for(now.date())
        if expiry_day is None:
            logger.info("coa live skipped: no expiry")
            return sent
        token = expiry_token(expiry_day)
        payload = self._client.optionchain(
            self._settings.index_symbol,
            self._settings.index_spot_exchange,
            token,
            strike_count=10,
        )
        chain = payload.get("chain") or []
        spot = float(payload.get("underlying_ltp") or 0)
        atm = float(payload.get("atm_strike") or 0)
        if spot <= 0 or atm <= 0 or not chain:
            logger.info("coa live skipped: empty chain")
            return sent
        snap = evaluate(chain, spot, atm=atm)
        state = _load_state(self._state_path)
        clock = now.astimezone(IST).time()
        open_trade = state.get("open")

        if open_trade is not None:
            trade = _trade_from_dict(open_trade)
            ltp = _atm_ltp(chain, trade.strike, trade.side)
            trade.note_ltp(ltp)
            reason = decide_exit(clock, spot, ltp, trade, snap)
            if reason and ltp > 0:
                self._hands.send(COA_EXIT)
                sent.append(COA_EXIT)
                logger.info("coa live exit %s ltp=%.2f", reason, ltp)
                state["open"] = None
                state["last_exit"] = None if reason == "15:15 flatten" else now.isoformat()
                self._publish_brief(now, snap, state)
                return sent
            state["open"] = _trade_to_dict(trade)
            self._publish_brief(now, snap, state)
            return sent

        if not in_entry_window(clock):
            self._publish_brief(now, snap, state)
            return sent
        last_exit = _parse_ts(state.get("last_exit"))
        if not can_reenter(now, last_exit):
            self._publish_brief(now, snap, state)
            return sent
        if snap.signal == "BUY_CE":
            ltp = _atm_ltp(chain, atm, "CE")
            if ltp > 0:
                self._hands.send(COA_BUY_CE)
                sent.append(COA_BUY_CE)
                state["open"] = _trade_to_dict(_new_trade("CE", ltp, now, atm, snap))
                logger.info("coa live BUY_CE ltp=%.2f CoA %s", ltp, snap.scenario)
        elif snap.signal == "BUY_PE":
            ltp = _atm_ltp(chain, atm, "PE")
            if ltp > 0:
                self._hands.send(COA_BUY_PE)
                sent.append(COA_BUY_PE)
                state["open"] = _trade_to_dict(_new_trade("PE", ltp, now, atm, snap))
                logger.info("coa live BUY_PE ltp=%.2f CoA %s", ltp, snap.scenario)
        self._publish_brief(now, snap, state)
        return sent

    def _publish_brief(self, now: datetime, snap, state: dict) -> None:
        tape = update_tape(state.get("tape"), snap, now)
        state["tape"] = tape_to_dict(tape)
        payload = build_brief(snap, now, tape)
        write_public_brief(self._settings.public_brief_path, payload)
        _save_state(self._state_path, state)


def _new_trade(side: str, ltp: float, now: datetime, atm: float, snap) -> OpenTrade:
    return OpenTrade(
        side=side,
        entry=ltp,
        entry_ts=now,
        peak=ltp,
        eos=snap.eos,
        eor=snap.eor,
        support=None if snap.support is None else snap.support.strike,
        resistance=None if snap.resistance is None else snap.resistance.strike,
        scenario=snap.scenario or "?",
        strike=atm,
    )


def _atm_ltp(chain: list, strike: float, side: str) -> float:
    key = "ce" if side == "CE" else "pe"
    for row in chain:
        if abs(float(row.get("strike") or 0) - float(strike)) < 0.01:
            data = row.get(key) if isinstance(row.get(key), dict) else {}
            return float(data.get("ltp") or 0)
    return 0.0


def _trade_to_dict(trade: OpenTrade) -> dict:
    return {
        "side": trade.side,
        "entry": trade.entry,
        "entry_ts": trade.entry_ts.isoformat(),
        "peak": trade.peak,
        "eos": trade.eos,
        "eor": trade.eor,
        "support": trade.support,
        "resistance": trade.resistance,
        "scenario": trade.scenario,
        "reclaimed": trade.reclaimed,
        "strike": trade.strike,
    }


def _trade_from_dict(raw: dict) -> OpenTrade:
    ts = _parse_ts(raw.get("entry_ts")) or datetime.now(tz=IST)
    trade = OpenTrade(
        side=str(raw.get("side") or "CE"),
        entry=float(raw.get("entry") or 0),
        entry_ts=ts,
        peak=float(raw.get("peak") or 0),
        eos=raw.get("eos"),
        eor=raw.get("eor"),
        support=raw.get("support"),
        resistance=raw.get("resistance"),
        scenario=raw.get("scenario"),
        reclaimed=bool(raw.get("reclaimed")),
        strike=float(raw.get("strike") or 0),
    )
    return trade


def _load_state(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return {"open": None, "last_exit": None}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    parsed = datetime.fromisoformat(str(raw))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)
