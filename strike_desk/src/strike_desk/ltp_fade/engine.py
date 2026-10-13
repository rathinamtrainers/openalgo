"""Session state machine. Paper applies decisions; it does not place orders."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .decide import evaluate_entry, evaluate_exit
from .freeze import freeze_lines
from .types import (
    FREEZE_DEADLINE,
    FREEZE_TIME,
    ChainSnapshot,
    Decision,
    FrozenLines,
    SessionState,
)

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)


def _log(state: SessionState, line: str) -> None:
    state.logs.append(line)
    logger.info(line)


def _reset(state: SessionState, day) -> None:
    state.session_date = day
    state.state = "PREOPEN"
    state.lines = None
    state.entered_today = False
    state.side = "NONE"
    state.entry_symbol = ""
    state.entry_fill = None
    state.entry_time = None
    state.lot_size = 0
    state.freeze_attempts = 0


def _format_freeze(day, now: datetime, lines: FrozenLines) -> str:
    return (
        f"freeze {day.isoformat()} {now.strftime('%H:%M')} "
        f"spot={lines.spot_at_freeze} atm={lines.atm} "
        f"R={lines.resistance} S={lines.support} "
        f"eor={lines.eor} eor1={lines.eor1} eos={lines.eos} eos1={lines.eos1} "
        f"call_ok={str(lines.call_ok).lower()} put_ok={str(lines.put_ok).lower()} "
        f"reversal={lines.reversal_mode}"
    )


def persist_freeze(path: Path, lines: FrozenLines) -> None:
    """Write the 09:21 snapshot for audit / replay."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "reversal_mode": lines.reversal_mode,
        "spot_at_freeze": lines.spot_at_freeze,
        "atm": lines.atm,
        "expiry": lines.expiry,
        "R": lines.resistance,
        "S": lines.support,
        "R_volume": lines.r_volume,
        "S_volume": lines.s_volume,
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
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class LtpFadeEngine:
    """One session. Call ``tick`` with IST ``now``, live spot, optional chain / option LTP."""

    def __init__(self, snapshot_dir: Path | None = None) -> None:
        self.state = SessionState()
        self.snapshot_dir = snapshot_dir

    def tick(
        self,
        now: datetime,
        spot: float,
        chain: ChainSnapshot | None = None,
        option_ltp: float | None = None,
    ) -> Decision:
        if now.tzinfo is None:
            return Decision(action="none", reason="clock_not_ist")
        day = now.astimezone(IST).date()
        clock = now.astimezone(IST).time()

        if self.state.session_date != day:
            _reset(self.state, day)
            _log(self.state, f"session {day.isoformat()} PREOPEN")

        if clock < FREEZE_TIME:
            if self.state.state != "PREOPEN":
                _reset(self.state, day)
            return Decision(action="none", reason="preopen")

        if self.state.state in {"PREOPEN", "FREEZING"}:
            self.state.state = "FREEZING"
            self.state.freeze_attempts += 1
            if chain is None:
                if clock >= FREEZE_DEADLINE:
                    self.state.state = "NO_TRADE"
                    _log(self.state, "freeze failed after 09:30 — NO_TRADE")
                    return Decision(action="none", reason="freeze_failed")
                return Decision(action="none", reason="freezing")
            lines = freeze_lines(chain)
            if lines is None:
                if clock >= FREEZE_DEADLINE:
                    self.state.state = "NO_TRADE"
                    _log(self.state, "freeze failed after 09:30 — NO_TRADE")
                    return Decision(action="none", reason="freeze_failed")
                return Decision(action="none", reason="freeze_unusable")
            self.state.lines = lines
            self.state.state = "FROZEN"
            _log(self.state, _format_freeze(day, now, lines))
            if self.snapshot_dir is not None:
                persist_freeze(self.snapshot_dir / f"ltp-fade-{day.isoformat()}.json", lines)

        if self.state.state == "NO_TRADE":
            return Decision(action="none", reason="no_trade")

        if self.state.state == "IN_TRADE":
            decision = evaluate_exit(now, spot, option_ltp, self.state)
            if decision.action == "exit":
                _log(self.state, decision.detail or decision.reason)
                self.state.state = "DONE"
                self.state.side = "NONE"
            return decision

        if self.state.state == "DONE":
            return Decision(action="none", reason="already_entered")

        if chain is None:
            return Decision(action="none", reason="no_chain_for_entry")
        decision = evaluate_entry(now, spot, chain, self.state)
        if decision.reason == "dual_tag":
            _log(self.state, "skip dual_tag")
        elif decision.action == "none" and decision.reason in {
            "missing_eor1",
            "atm_premium_invalid",
            "no_atm_contract",
        }:
            _log(self.state, f"skip {decision.reason}")
        elif decision.action == "enter":
            self.state.entered_today = True
            self.state.side = decision.side
            self.state.entry_symbol = decision.symbol
            self.state.entry_fill = decision.fill_premium
            self.state.entry_time = now
            self.state.lot_size = decision.lot_size
            self.state.state = "IN_TRADE"
            _log(self.state, decision.detail or decision.reason)
        return decision
