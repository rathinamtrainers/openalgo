"""Live CoA mind: 5-minute chain -> Analyze fill + CoA Hands. Not QuadShield."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .coa_analyzer import CoaAnalyzer
from .coa_boards import (
    BTC,
    CASH_BOARDS,
    MCX_BOARDS,
    NIFTY,
    CoaBoard,
    expiry_date_for_board,
    pick_listed_expiry,
)
from .config import IST, Settings
from .errors import AnalyzerOff, OpenAlgoError
from .ltp_coa import evaluate
from .ltp_coa_brief import build_brief, publish_public_brief, tape_to_dict, update_tape
from .ltp_coa_exits import (
    OpenTrade,
    can_reenter,
    decide_exit,
    in_entry_window,
)
from .ltp_coa_intel import apply_handbook, atm_oi, intel_to_dict
from .ltp_coa_replay import expiry_token
from .ofa_analyzer import pick_atm_option
from .openalgo_client import OpenAlgoClient
from .tradetron_hands import COA_BUY_CE, COA_BUY_PE, COA_EXIT, CoaHands

logger = logging.getLogger(__name__)


class CoaLiveRunner:
    def __init__(
        self,
        settings: Settings,
        client: OpenAlgoClient | Any,
        hands: CoaHands,
        *,
        analyzer: CoaAnalyzer | None = None,
        board: CoaBoard = NIFTY,
        is_trading_day,
        expiry_date_for=None,
        trading_window=None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._hands = hands
        self._analyzer = analyzer
        self.board = board
        self._is_trading_day = is_trading_day
        self._expiry_date_for = expiry_date_for
        self._trading_window = trading_window
        self._state_path = settings.state_dir / board.state_file
        self._listed_cache: tuple[date, date | None] | None = None

    def step(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(tz=IST)
        sent: list[str] = []
        if self._settings.kill_switch_path.exists():
            logger.info("coa live skipped: kill switch")
            self._touch_heartbeat(now)
            return sent
        window = self._trading_window(now.date()) if self._trading_window is not None else None
        if window is None and not self._is_trading_day(now.date()):
            logger.info("coa live skipped: market closed")
            self._touch_heartbeat(now)
            return sent
        if window is not None:
            start, end = window
            if not (start <= now <= end):
                logger.info("coa live skipped: outside session")
                self._touch_heartbeat(now)
                return sent

        expiry_day = self._expiry_day(now.date())
        if expiry_day is None:
            logger.info("coa live %s skipped: no expiry", self.board.slug)
            self._touch_heartbeat(now)
            return sent
        token = expiry_token(expiry_day)
        payload = self._client.optionchain(
            self.board.index_symbol,
            self.board.index_spot_exchange,
            token,
            strike_count=10,
        )
        chain = payload.get("chain") or []
        spot = float(payload.get("underlying_ltp") or 0)
        atm = float(payload.get("atm_strike") or 0)
        if spot <= 0 or atm <= 0 or not chain:
            logger.info("coa live %s skipped: empty chain", self.board.slug)
            self._touch_heartbeat(now)
            return sent
        snap = evaluate(
            chain,
            spot,
            atm=atm,
            strike_step=self.board.strike_step,
            extension_pts=self.board.extension_pts,
            expiry_kind=self.board.expiry_kind,
        )
        state = _load_state(self._state_path)
        call_oi, put_oi = atm_oi(chain, atm)
        snap, intel = apply_handbook(
            snap,
            state.get("intel"),
            now,
            call_oi=call_oi,
            put_oi=put_oi,
            expiry_kind=self.board.expiry_kind,
        )
        state["intel"] = intel_to_dict(intel)
        clock = now.astimezone(IST).time()
        open_trade = state.get("open")

        if open_trade is not None:
            trade = _trade_from_dict(open_trade)
            ltp = _atm_ltp(chain, trade.strike, trade.side)
            trade.note_ltp(ltp)
            reason = decide_exit(
                clock,
                spot,
                ltp,
                trade,
                snap,
                spot_sl_buffer=self.board.spot_sl_buffer,
                reclaim_trail=self.board.reclaim_trail,
                flatten_at=self.board.flatten_at,
            )
            if reason and ltp > 0:
                self._analyze_exit(state)
                if self._send(COA_EXIT):
                    sent.append(COA_EXIT)
                    logger.info("coa live %s exit %s ltp=%.2f", self.board.slug, reason, ltp)
                    state["open"] = None
                    state["analyzer_symbol"] = ""
                    state["analyzer_qty"] = 0
                    state["last_exit"] = None if "flatten" in reason else now.isoformat()
                self._publish_brief(now, snap, state)
                self._touch_heartbeat(now)
                return sent
            state["open"] = _trade_to_dict(trade)
            self._publish_brief(now, snap, state)
            self._touch_heartbeat(now)
            return sent

        if not self.board.always_open and not in_entry_window(
            clock, start=self.board.entry_open, end=self.board.entry_close
        ):
            self._publish_brief(now, snap, state)
            self._touch_heartbeat(now)
            return sent
        last_exit = _parse_ts(state.get("last_exit"))
        if not can_reenter(now, last_exit):
            self._publish_brief(now, snap, state)
            self._touch_heartbeat(now)
            return sent
        if snap.signal == "BUY_CE":
            ltp = _atm_ltp(chain, atm, "CE")
            if ltp > 0:
                self._analyze_buy(state, chain, atm, "CE", expiry_day)
                if self._send(COA_BUY_CE):
                    sent.append(COA_BUY_CE)
                    state["open"] = _trade_to_dict(_new_trade("CE", ltp, now, atm, snap))
                    logger.info(
                        "coa live %s BUY_CE ltp=%.2f CoA %s",
                        self.board.slug,
                        ltp,
                        snap.scenario,
                    )
        elif snap.signal == "BUY_PE":
            ltp = _atm_ltp(chain, atm, "PE")
            if ltp > 0:
                self._analyze_buy(state, chain, atm, "PE", expiry_day)
                if self._send(COA_BUY_PE):
                    sent.append(COA_BUY_PE)
                    state["open"] = _trade_to_dict(_new_trade("PE", ltp, now, atm, snap))
                    logger.info(
                        "coa live %s BUY_PE ltp=%.2f CoA %s",
                        self.board.slug,
                        ltp,
                        snap.scenario,
                    )
        self._publish_brief(now, snap, state)
        self._touch_heartbeat(now)
        return sent

    def touch_heartbeat(self, now: datetime | None = None) -> None:
        stamp = now or datetime.now(tz=IST)
        path = self._settings.coa_heartbeat_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stamp.astimezone(IST).isoformat(), encoding="utf-8")

    def _touch_heartbeat(self, now: datetime) -> None:
        self.touch_heartbeat(now)

    def _send(self, action: str) -> bool:
        try:
            result = self._hands.send(action, board=self.board.slug)
        except Exception:
            logger.exception("coa hands %s %s failed", self.board.slug, action)
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

    def _analyze_buy(
        self,
        state: dict[str, Any],
        chain: list,
        atm: float,
        side: str,
        expiry_day: date | None,
    ) -> None:
        if not self.board.analyze or self._analyzer is None:
            return
        symbol, lot = _atm_contract(chain, atm, side)
        if (not symbol or lot <= 0) and expiry_day is not None:
            try:
                symbol, resolved_qty = self._analyzer.resolve_atm(
                    side,
                    expiry_day,
                    underlying=self.board.index_symbol,
                    spot_exchange=self.board.index_spot_exchange,
                )
                lots = max(int(self._settings.coa_lots), 1)
                lot = int(resolved_qty or 0) // lots
            except (OpenAlgoError, AnalyzerOff) as exc:
                logger.info("coa analyzer resolve skipped: %s", exc)
                return
        qty = self.board.order_quantity(lot, self._settings.coa_lots) if symbol else 0
        if not symbol or qty <= 0:
            logger.info("coa analyzer skipped: no ATM %s contract", side)
            return
        try:
            self._analyzer.place(
                symbol, "BUY", qty, exchange=self.board.option_exchange
            )
        except AnalyzerOff as exc:
            logger.info("coa analyzer refused: %s", exc)
            return
        except OpenAlgoError:
            logger.exception("coa analyzer BUY %s failed", side)
            return
        state["analyzer_symbol"] = symbol
        state["analyzer_qty"] = qty

    def _analyze_exit(self, state: dict[str, Any]) -> None:
        if not self.board.analyze or self._analyzer is None:
            return
        symbol = str(state.get("analyzer_symbol") or "")
        try:
            qty = int(state.get("analyzer_qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if not symbol or qty <= 0:
            return
        try:
            self._analyzer.place(
                symbol, "SELL", qty, exchange=self.board.option_exchange
            )
        except AnalyzerOff as exc:
            logger.info("coa analyzer refused: %s", exc)
        except OpenAlgoError:
            logger.exception("coa analyzer SELL failed")

    def _publish_brief(self, now: datetime, snap, state: dict) -> None:
        tape = update_tape(state.get("tape"), snap, now)
        state["tape"] = tape_to_dict(tape)
        payload = build_brief(
            snap,
            now,
            tape,
            in_window=(
                True
                if self.board.always_open
                else in_entry_window(
                    now.astimezone(IST).time(),
                    start=self.board.entry_open,
                    end=self.board.entry_close,
                )
            ),
            index_name=self.board.index_name,
            strike_step=self.board.strike_step,
            market=self.board.slug,
            extension_pts=self.board.extension_pts,
            hands_posted=_hands_side(state.get("open")),
        )
        try:
            from .ltp_coa_narrate import polish_brief

            payload = polish_brief(payload, self._settings)
        except Exception:
            logger.exception("coa brief polish skipped")
        brief_path = self._settings.state_dir / self.board.brief_file
        publish_public_brief(brief_path, payload, self._settings)
        _save_state(self._state_path, state)

    def _expiry_day(self, day: date) -> date | None:
        listed = self._listed_expiry(day)
        if listed is not None:
            return listed
        if self._expiry_date_for is not None:
            return self._expiry_date_for(day)
        return expiry_date_for_board(day, self.board, self._is_trading_day)

    def _listed_expiry(self, day: date) -> date | None:
        if self.board.option_exchange == "CRYPTO":
            return None
        cached = self._listed_cache
        if cached is not None and cached[0] == day:
            return cached[1]
        fetch = getattr(self._client, "expiry_dates", None)
        if not callable(fetch):
            return None
        try:
            labels = fetch(self.board.index_symbol, self.board.option_exchange)
        except OpenAlgoError as exc:
            logger.warning("coa live %s expiry list failed: %s", self.board.slug, exc)
            return None
        picked = pick_listed_expiry(day, labels or [], kind=self.board.expiry_kind)
        if picked is None:
            logger.warning("coa live %s expiry list empty after %s", self.board.slug, day)
        self._listed_cache = (day, picked)
        return picked


def make_mcx_runners(
    settings: Settings,
    client: OpenAlgoClient,
    hands: CoaHands,
    *,
    analyzer: CoaAnalyzer | None,
    is_trading_day,
    trading_window=None,
) -> list[CoaLiveRunner]:
    """Crude and Natgas. Same CoA mind and cash Hands token; flags never mix."""
    return [
        CoaLiveRunner(
            settings,
            client,
            hands,
            analyzer=analyzer,
            board=board,
            is_trading_day=is_trading_day,
            expiry_date_for=lambda day, b=board: expiry_date_for_board(
                day, b, is_trading_day
            ),
            trading_window=trading_window,
        )
        for board in MCX_BOARDS
    ]


def make_cash_runners(
    settings: Settings,
    client: OpenAlgoClient,
    hands: CoaHands,
    *,
    analyzer: CoaAnalyzer | None,
    is_trading_day,
    trading_window=None,
) -> list[CoaLiveRunner]:
    """One runner per cash index. Shared Hands token; flags never mix."""
    return [
        CoaLiveRunner(
            settings,
            client,
            hands,
            analyzer=analyzer,
            board=board,
            is_trading_day=is_trading_day,
            expiry_date_for=lambda day, b=board: expiry_date_for_board(
                day, b, is_trading_day
            ),
            trading_window=trading_window,
        )
        for board in CASH_BOARDS
    ]


def make_btc_runner(
    settings: Settings,
    client,
    hands,
) -> CoaLiveRunner:
    """24x7 BTC CoA. Own Hands token. No cash session, no 15:15 flatten."""
    return CoaLiveRunner(
        settings,
        client,
        hands,
        analyzer=None,
        board=BTC,
        is_trading_day=lambda _day: True,
        expiry_date_for=lambda day: expiry_date_for_board(day, BTC, lambda _d: True),
        trading_window=None,
    )


def _hands_side(open_trade: dict | None) -> str | None:
    if not isinstance(open_trade, dict):
        return None
    side = str(open_trade.get("side") or "").upper()
    return side if side in {"CE", "PE"} else None


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


def _atm_contract(chain: list, strike: float, side: str) -> tuple[str, int]:
    try:
        return pick_atm_option({"atm_strike": strike, "chain": chain}, side)
    except OpenAlgoError:
        return "", 0


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
