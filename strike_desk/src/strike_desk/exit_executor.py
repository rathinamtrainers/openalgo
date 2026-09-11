"""The ungated exit path: the preflight that proves it exists, and the ladder that uses it.

Nothing in this module is reachable from an agent, and it imports no part of the reasoning
plane. That is a structural property the guardrail tests assert, not a convention.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .config import Settings
from .errors import MirrorUnavailable, OpenAlgoError
from .execution_client import ExecutionClient
from .journal import EXIT_FAILED, EXIT_GATED, EXIT_REFUSED, EXIT_SUBMITTED
from .openalgo_client import OpenAlgoClient
from .openalgo_mirror import ORDER_MODE_SEMI_AUTO, OpenAlgoMirror

logger = logging.getLogger(__name__)

PATH_CLOSE_POSITION = "closeposition"
PATH_TARGETED_SELL = "targeted-sell"


@dataclass(frozen=True)
class ExitPathStatus:
    """Whether an exit could be fired right now without a human in the way."""

    ok: bool
    detail: str
    analyze_mode: bool | None = None
    order_mode: str | None = None


@dataclass(frozen=True)
class ExitAttempt:
    """What one rung of the ladder did."""

    status: str
    path: str
    submitted_at_utc: datetime | None
    round_trip_ms: int
    broker_order_id: str | None
    detail: str
    raw: dict[str, Any] = field(default_factory=dict)


def exit_path_status(mirror: OpenAlgoMirror) -> ExitPathStatus:
    """Read OpenAlgo's two switches and say whether an exit would be gated.

    Analyze mode makes closeposition work regardless of order mode; auto order mode makes a
    placed order reach the broker directly. Live plus semi-auto is the one combination in
    which every write path this desk holds would stop at a human, and it is a refusal to
    enter, not a refusal to exit.
    """
    try:
        analyze = mirror.analyze_mode()
        mode = mirror.order_mode()
    except MirrorUnavailable as exc:
        return ExitPathStatus(False, f"exit path unverifiable: {exc}")
    if analyze:
        return ExitPathStatus(
            True, "OpenAlgo is in analyze mode: exits close against the sandbox engine",
            analyze_mode=True, order_mode=mode,
        )
    if mode != ORDER_MODE_SEMI_AUTO:
        return ExitPathStatus(
            True, f"order mode is {mode!r}: exits reach the broker directly",
            analyze_mode=False, order_mode=mode,
        )
    return ExitPathStatus(
        False,
        "OpenAlgo is live and in semi-auto: closeposition is refused and a sell order would "
        "queue for approval, so an exit would need a human",
        analyze_mode=False,
        order_mode=mode,
    )


class ExitExecutor:
    """Fires market exits. Holds no state; every call carries everything it needs."""

    def __init__(
        self,
        settings: Settings,
        execution: ExecutionClient,
        client: OpenAlgoClient,
        strategy: str,
    ) -> None:
        self._settings = settings
        self._execution = execution
        self._client = client
        self._strategy = strategy

    def book_is_exclusively(self, symbol: str) -> bool:
        """True when the only non-zero position at the broker is the one we are managing."""
        try:
            book = self._client.positionbook()
        except OpenAlgoError:
            logger.exception("could not read the position book before an exit")
            return False
        for entry in book:
            try:
                quantity = int(float(entry.get("quantity", 0) or 0))
            except (TypeError, ValueError):
                return False
            if quantity == 0:
                continue
            if str(entry.get("symbol", "")).strip().upper() != symbol.strip().upper():
                return False
        return True

    def fire(self, symbol: str, exchange: str, quantity: int) -> ExitAttempt:
        """One attempt at getting flat. Market only, never queued if the platform allows it."""
        if quantity <= 0:
            return ExitAttempt(
                EXIT_FAILED, PATH_CLOSE_POSITION, None, 0, None,
                f"refusing to exit a non-positive quantity {quantity}",
            )
        if self.book_is_exclusively(symbol):
            attempt = self._close_position()
            if attempt.status == EXIT_SUBMITTED:
                return attempt
            logger.warning("closeposition rung failed (%s); falling back to a targeted sell",
                           attempt.detail)
        return self._targeted_sell(symbol, exchange, quantity)

    def _close_position(self) -> ExitAttempt:
        started = datetime.now(tz=UTC)
        ok, parsed = self._execution.close_position(self._strategy)
        finished = datetime.now(tz=UTC)
        round_trip = int((finished - started).total_seconds() * 1000)
        if ok:
            return ExitAttempt(
                EXIT_SUBMITTED, PATH_CLOSE_POSITION, started, round_trip, None,
                "closeposition accepted", parsed,
            )
        return ExitAttempt(
            EXIT_REFUSED, PATH_CLOSE_POSITION, started, round_trip, None,
            str(parsed.get("message") or "closeposition refused")[:300], parsed,
        )

    def _targeted_sell(self, symbol: str, exchange: str, quantity: int) -> ExitAttempt:
        payload = {
            "strategy": self._strategy,
            "symbol": symbol,
            "exchange": exchange,
            "action": "SELL",
            "quantity": int(quantity),
            "pricetype": "MARKET",
            "product": self._settings.order_product,
            "price": 0,
        }
        started = datetime.now(tz=UTC)
        try:
            receipt = self._execution.place_order(payload)
        except OpenAlgoError as exc:
            finished = datetime.now(tz=UTC)
            return ExitAttempt(
                EXIT_FAILED, PATH_TARGETED_SELL, started,
                int((finished - started).total_seconds() * 1000), None,
                f"exit order refused: {exc}",
            )
        finished = datetime.now(tz=UTC)
        round_trip = int((finished - started).total_seconds() * 1000)
        if receipt.queued:
            logger.critical(
                "EXIT QUEUED FOR APPROVAL: %s x %s is waiting as pending order %s — approve it "
                "or close the position by hand now",
                quantity, symbol, receipt.pending_order_id,
            )
            return ExitAttempt(
                EXIT_GATED, PATH_TARGETED_SELL, started, round_trip, None,
                f"exit queued as pending order {receipt.pending_order_id}", receipt.raw,
            )
        return ExitAttempt(
            EXIT_SUBMITTED, PATH_TARGETED_SELL, started, round_trip,
            receipt.broker_order_id, "exit order placed", receipt.raw,
        )
