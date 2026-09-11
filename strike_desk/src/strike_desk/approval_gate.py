"""The human gate: build an order from a cleared intent, queue it, and settle what happens.

The gate decides nothing about the trade. Every number in the payload was decided upstream —
the contract by the strategist and the playbook, the size by the Risk Officer — and every
number is recomputed here from the journal's own fields rather than read out of prose.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from .autonomy import check_mode
from .config import Settings
from .errors import (
    AlreadyJournalled,
    AutonomyMismatch,
    ExecutionFailed,
    InvalidOrderPayload,
    JournalWriteError,
    MirrorUnavailable,
    OpenAlgoError,
    UnpriceableBand,
)
from .execution_client import ExecutionClient
from .journal import (
    APPROVAL_APPROVED,
    APPROVAL_AUTO_APPROVED,
    APPROVAL_DEFECTS,
    APPROVAL_GATE_BYPASSED,
    APPROVAL_GATE_UNAVAILABLE,
    APPROVAL_LATE,
    APPROVAL_PENDING,
    APPROVAL_SUBMIT_FAILED,
    APPROVAL_UNPRICEABLE,
    SCHEMA_VERSION,
    Journal,
)
from .openalgo_mirror import GateHealth, OpenAlgoMirror
from .session import engage_kill_switch

logger = logging.getLogger(__name__)

ACTION_BUY = "BUY"
PRICE_TYPE_LIMIT = "LIMIT"

WITHDRAWAL_NOT_QUEUED = "not-queued"
WITHDRAWAL_PERMITTED = "permitted"
WITHDRAWAL_REFUSED = "refused"
WITHDRAWAL_NOT_ATTEMPTED = "not-attempted"


def align_price(band_low: float, band_high: float, tick: float) -> float:
    """The highest price on the tick grid that lies inside the band.

    A buy is priced at the top of the band and never above it, so the high is floored to the
    grid. If that lands under the band, the low is raised to the grid instead. A band that
    holds no grid price at all is refused — nudging it would price a trade the Risk Officer
    never adjudicated.
    """
    grid = Decimal(str(tick))
    low = Decimal(str(band_low))
    high = Decimal(str(band_high))
    if grid <= 0:
        raise UnpriceableBand(f"tick size {tick!r} is not positive")
    if low > high:
        raise UnpriceableBand(f"band {band_low} to {band_high} is inverted")

    floored = (high / grid).to_integral_value(rounding=ROUND_FLOOR) * grid
    if floored >= low and floored > 0:
        return float(floored)
    raised = (low / grid).to_integral_value(rounding=ROUND_CEILING) * grid
    if raised <= high and raised > 0:
        return float(raised)
    raise UnpriceableBand(f"no multiple of {tick} lies between {band_low} and {band_high}")


def strategy_tag(settings: Settings, tick_id: str) -> str:
    """The label the Action Center shows the trader, tying the click to the tick."""
    return f"{settings.order_strategy_prefix}:{tick_id[:8]}"


@dataclass(frozen=True)
class TickContext:
    """Everything the gate needs from a tick, lifted out of graph state."""

    tick_id: str
    trace_id: str
    trading_day: str
    proposal: dict[str, Any]
    risk: dict[str, Any]
    proposal_id: str | None
    verdict_id: str | None


@dataclass(frozen=True)
class OrderIntent:
    """A cleared intent, priced and sized, ready to be queued."""

    approval_id: str
    tick_id: str
    trace_id: str
    trading_day: str
    index_symbol: str
    symbol: str
    exchange: str
    action: str
    product: str
    lots: int
    lot_size: int
    quantity: int
    limit_price: float
    band_low: float
    band_high: float
    strategy: str
    deadline_utc: datetime
    proposal_id: str | None
    verdict_id: str | None

    def payload(self) -> dict[str, Any]:
        """The order body OpenAlgo's placeorder schema expects."""
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "action": self.action,
            "quantity": self.quantity,
            "pricetype": PRICE_TYPE_LIMIT,
            "product": self.product,
            "price": self.limit_price,
        }


def build_intent(context: TickContext, settings: Settings, now_utc: datetime) -> OrderIntent:
    """Turn a cleared tick into a priced, sized intent. Raises rather than guessing."""
    proposal = context.proposal or {}
    risk = context.risk or {}
    try:
        lots = int(risk["lots_cleared"])
        lot_size = int(proposal["lot_size"])
        band_low = float(proposal["entry_price_low"])
        band_high = float(proposal["entry_price_high"])
        symbol = str(proposal["symbol"]).strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidOrderPayload(f"the cleared intent is unreadable: {exc}") from exc
    if lots <= 0 or lot_size <= 0 or not symbol:
        raise InvalidOrderPayload(
            f"the cleared intent is unusable: lots={lots} lot_size={lot_size} symbol={symbol!r}"
        )

    return OrderIntent(
        approval_id=str(uuid.uuid4()),
        tick_id=context.tick_id,
        trace_id=context.trace_id,
        trading_day=context.trading_day,
        index_symbol=settings.index_symbol,
        symbol=symbol,
        exchange=settings.option_exchange,
        action=ACTION_BUY,
        product=settings.order_product,
        lots=lots,
        lot_size=lot_size,
        quantity=lots * lot_size,
        limit_price=align_price(band_low, band_high, settings.price_tick),
        band_low=band_low,
        band_high=band_high,
        strategy=strategy_tag(settings, context.tick_id),
        deadline_utc=now_utc + timedelta(seconds=settings.approval_deadline_seconds),
        proposal_id=context.proposal_id,
        verdict_id=context.verdict_id,
    )


@dataclass(frozen=True)
class SubmitResult:
    """What the submit node puts into tick state."""

    approval_id: str
    status: str
    detail: str
    pending_order_id: int | None = None
    quantity: int = 0
    limit_price: float = 0.0
    symbol: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Resolution:
    """How one approval ended. Plain data, because it travels through a graph resume."""

    approval_id: str
    tick_id: str
    status: str
    detail: str = ""
    approved_by: str | None = None
    resolved_at_ist: str | None = None
    broker_order_id: str | None = None
    withdrawal: str | None = None
    wait_seconds: float = 0.0
    order_status: str | None = None
    average_price: float | None = None
    order_raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Resolution:
        known = {key: payload.get(key) for key in cls.__dataclass_fields__ if key in payload}
        known.setdefault("order_raw", {})
        return cls(**known)  # type: ignore[arg-type]


def _base_row(intent_row: Any) -> dict[str, Any]:
    """The contract fields every state of one approval repeats, copied from its pending row."""
    return {
        "approval_id": intent_row.approval_id,
        "tick_id": intent_row.tick_id,
        "trading_day": intent_row.trading_day,
        "index_symbol": intent_row.index_symbol,
        "symbol": intent_row.symbol,
        "exchange": intent_row.exchange,
        "action": intent_row.action,
        "product": intent_row.product,
        "lots": intent_row.lots,
        "lot_size": intent_row.lot_size,
        "quantity": intent_row.quantity,
        "limit_price": intent_row.limit_price,
        "band_low": intent_row.band_low,
        "band_high": intent_row.band_high,
        "pending_order_id": intent_row.pending_order_id,
        "strategy_tag": intent_row.strategy_tag,
        "deadline_utc": intent_row.deadline_utc,
        "proposal_id": intent_row.proposal_id,
        "verdict_id": intent_row.verdict_id,
        "tick_trace_id": intent_row.tick_trace_id,
        "payload_json": intent_row.payload_json,
        "schema_version": SCHEMA_VERSION,
    }


def settle_approval(journal: Journal, resolution: Resolution, trace_id: str) -> bool:
    """Append the terminal rows for one approval. The only writer, called from two places.

    Returns False when there was nothing to settle or the settlement had already been
    written — both of which are ordinary outcomes of a retried resume, not errors.
    """
    head = journal.approval_row(resolution.approval_id, APPROVAL_PENDING)
    if head is None:
        logger.error("approval %s has no pending row to settle", resolution.approval_id)
        return False

    if resolution.broker_order_id and resolution.status in {APPROVAL_APPROVED, APPROVAL_LATE}:
        try:
            journal.record_order(
                order_id=str(uuid.uuid4()),
                approval_id=head.approval_id,
                tick_id=head.tick_id,
                trace_id=trace_id,
                created_at_utc=datetime.now(tz=UTC),
                trading_day=head.trading_day,
                pending_order_id=head.pending_order_id,
                broker_order_id=resolution.broker_order_id,
                symbol=head.symbol,
                exchange=head.exchange,
                action=head.action,
                product=head.product,
                price_type=PRICE_TYPE_LIMIT,
                limit_price=head.limit_price,
                lots=head.lots,
                lot_size=head.lot_size,
                quantity=head.quantity,
                order_status=str(resolution.order_status or "unknown"),
                average_price=resolution.average_price,
                raw_json=json.dumps(resolution.order_raw, default=str, sort_keys=True),
                schema_version=SCHEMA_VERSION,
            )
        except AlreadyJournalled:
            logger.info(
                "order row for approval %s at status %s already exists",
                resolution.approval_id,
                resolution.order_status,
            )

    row = _base_row(head)
    row.update(
        {
            "status": resolution.status,
            "trace_id": trace_id,
            "created_at_utc": datetime.now(tz=UTC),
            "wait_seconds": float(resolution.wait_seconds),
            "approved_by": resolution.approved_by,
            "resolved_at_ist": resolution.resolved_at_ist,
            "broker_order_id": resolution.broker_order_id,
            "withdrawal": resolution.withdrawal,
            "detail": resolution.detail,
            "response_json": json.dumps(resolution.order_raw, default=str, sort_keys=True),
            "defect": resolution.status in APPROVAL_DEFECTS,
        }
    )
    try:
        journal.record_approval(**row)
    except AlreadyJournalled:
        logger.info("approval %s was already settled as %s", head.approval_id, resolution.status)
        return False
    logger.info(
        "approval %s settled as %s after %.0fs",
        head.approval_id,
        resolution.status,
        resolution.wait_seconds,
    )
    return True


class ApprovalGate:
    """Preflight, submit, and the refusals that never reach the wire."""

    def __init__(
        self,
        settings: Settings,
        journal: Journal,
        mirror: OpenAlgoMirror,
        client: ExecutionClient,
    ) -> None:
        self._settings = settings
        self._journal = journal
        self._mirror = mirror
        self._client = client

    def health(self) -> GateHealth:
        """Whether a submission is allowed to be attempted at all."""
        return self._mirror.health()

    def submit(self, context: TickContext) -> SubmitResult:
        """Queue one cleared intent for approval, or journal exactly why it was not."""
        now = datetime.now(tz=UTC)
        try:
            intent = build_intent(context, self._settings, now)
        except UnpriceableBand as exc:
            return self._refuse_before_intent(context, APPROVAL_UNPRICEABLE, str(exc), now)
        except InvalidOrderPayload as exc:
            return self._refuse_before_intent(context, APPROVAL_SUBMIT_FAILED, str(exc), now)

        verdict = check_mode(self._settings, self._mirror)
        if not verdict.ok:
            self._record(intent, APPROVAL_GATE_UNAVAILABLE, verdict.detail, response={})
            return SubmitResult(
                intent.approval_id,
                APPROVAL_GATE_UNAVAILABLE,
                verdict.detail,
                quantity=intent.quantity,
                limit_price=intent.limit_price,
                symbol=intent.symbol,
            )

        try:
            receipt = self._client.place_order(intent.payload())
        except (OpenAlgoError, InvalidOrderPayload, MirrorUnavailable) as exc:
            detail = f"{type(exc).__name__}: {exc}"
            logger.error("placement for tick %s failed: %s", intent.tick_id, detail)
            self._record(intent, APPROVAL_SUBMIT_FAILED, detail, response={})
            return SubmitResult(
                intent.approval_id,
                APPROVAL_SUBMIT_FAILED,
                detail,
                quantity=intent.quantity,
                limit_price=intent.limit_price,
                symbol=intent.symbol,
            )

        pending_id = receipt.pending_order_id
        broker_id = receipt.broker_order_id
        if self._settings.unattended:
            if pending_id is not None or receipt.queued:
                detail = f"unattended mode but OpenAlgo queued the order as pending {pending_id}"
                engage_kill_switch(self._settings, "unattended submission was queued for approval")
                self._record(
                    intent,
                    APPROVAL_GATE_BYPASSED,
                    detail,
                    response=receipt.raw,
                    pending_order_id=pending_id,
                )
                raise AutonomyMismatch(detail)
            if broker_id is None:
                detail = "unattended submission returned no order id"
                self._record(intent, APPROVAL_SUBMIT_FAILED, detail, response=receipt.raw)
                raise ExecutionFailed(detail)
            self._record(
                intent,
                APPROVAL_AUTO_APPROVED,
                f"unattended entry placed as order {broker_id}",
                response=receipt.raw,
                broker_order_id=broker_id,
                approved_by="strike-desk",
            )
            logger.warning(
                "unattended entry: %s x%d at %s — stop %s target %s time-stop %s — %s",
                intent.symbol,
                intent.quantity,
                intent.limit_price,
                (context.proposal or {}).get("stop_price"),
                (context.proposal or {}).get("target_price"),
                (context.proposal or {}).get("time_stop_ist"),
                (context.proposal or {}).get("rationale") or intent.tick_id,
            )
            try:
                self._journal.record_order(
                    order_id=str(uuid.uuid4()),
                    approval_id=intent.approval_id,
                    tick_id=intent.tick_id,
                    trace_id=intent.trace_id,
                    created_at_utc=datetime.now(tz=UTC),
                    trading_day=intent.trading_day,
                    pending_order_id=None,
                    broker_order_id=broker_id,
                    symbol=intent.symbol,
                    exchange=intent.exchange,
                    action=intent.action,
                    product=intent.product,
                    price_type=PRICE_TYPE_LIMIT,
                    limit_price=intent.limit_price,
                    lots=intent.lots,
                    lot_size=intent.lot_size,
                    quantity=intent.quantity,
                    order_status="complete",
                    average_price=intent.limit_price,
                    raw_json=json.dumps(receipt.raw, default=str, sort_keys=True),
                    schema_version=SCHEMA_VERSION,
                )
            except AlreadyJournalled:
                logger.info(
                    "unattended order for approval %s already journalled", intent.approval_id
                )
            return SubmitResult(
                intent.approval_id,
                APPROVAL_AUTO_APPROVED,
                "placed without a human approval",
                quantity=intent.quantity,
                limit_price=intent.limit_price,
                symbol=intent.symbol,
            )

        if not receipt.queued:
            detail = (
                f"OpenAlgo answered mode={receipt.mode!r} "
                f"orderid={receipt.broker_order_id!r}: the approval gate was not in the path"
            )
            self._record(
                intent,
                APPROVAL_GATE_BYPASSED,
                detail,
                response=receipt.raw,
                broker_order_id=receipt.broker_order_id,
            )
            logger.critical(
                "APPROVAL GATE BYPASSED for tick %s — an order may have reached the broker "
                "without a human approval. %s",
                intent.tick_id,
                detail,
            )
            if receipt.broker_order_id:
                permitted, cancel_detail = self._client.cancel_order(
                    receipt.broker_order_id, intent.strategy
                )
                logger.critical(
                    "APPROVAL GATE BYPASSED: cancel of order %s %s (%s)",
                    receipt.broker_order_id,
                    "permitted" if permitted else "refused",
                    cancel_detail,
                )
            engage_kill_switch(
                self._settings, f"approval gate bypassed on tick {intent.tick_id}: {detail}"
            )
            return SubmitResult(
                intent.approval_id,
                APPROVAL_GATE_BYPASSED,
                detail,
                quantity=intent.quantity,
                limit_price=intent.limit_price,
                symbol=intent.symbol,
            )

        try:
            self._record(
                intent,
                APPROVAL_PENDING,
                f"queued as pending order {receipt.pending_order_id} for approval by "
                f"{self._settings.openalgo_user}",
                response=receipt.raw,
                pending_order_id=receipt.pending_order_id,
            )
        except JournalWriteError:
            logger.critical(
                "UNJOURNALLED PENDING ORDER %s: it was queued but its approval row could not "
                "be written, so nothing is watching it. Reject it in the Action Center.",
                receipt.pending_order_id,
            )
            raise
        logger.info(
            "queued %d x %s at %.2f as pending order %s (tick %s)",
            intent.quantity,
            intent.symbol,
            intent.limit_price,
            receipt.pending_order_id,
            intent.tick_id,
        )
        return SubmitResult(
            intent.approval_id,
            APPROVAL_PENDING,
            "awaiting the trader's approval",
            pending_order_id=receipt.pending_order_id,
            quantity=intent.quantity,
            limit_price=intent.limit_price,
            symbol=intent.symbol,
        )

    def record_failure(self, context: TickContext, detail: str) -> SubmitResult:
        """Best-effort journalling of a submission that raised. Never raises itself.

        Called by the graph's ``submit`` node, which must always return a terminal result:
        an exception escaping that node would be journalled by the runner under the tick's
        own id, and the decision row for that id already exists.
        """
        try:
            return self._refuse_before_intent(
                context, APPROVAL_SUBMIT_FAILED, detail, datetime.now(tz=UTC)
            )
        except Exception:  # noqa: BLE001 — the log is the last resort and must not raise
            logger.exception("could not journal the failed submission for tick %s", context.tick_id)
            return SubmitResult(str(uuid.uuid4()), APPROVAL_SUBMIT_FAILED, detail)

    def _refuse_before_intent(
        self, context: TickContext, status: str, detail: str, now: datetime
    ) -> SubmitResult:
        """Journal a refusal for an intent that could not even be built."""
        proposal = context.proposal or {}
        approval_id = str(uuid.uuid4())
        logger.error("tick %s could not be turned into an order: %s", context.tick_id, detail)
        self._journal.record_approval(
            approval_id=approval_id,
            status=status,
            tick_id=context.tick_id,
            trace_id=context.trace_id,
            tick_trace_id=context.trace_id,
            proposal_id=context.proposal_id,
            verdict_id=context.verdict_id,
            created_at_utc=now,
            trading_day=context.trading_day,
            index_symbol=self._settings.index_symbol,
            symbol=str(proposal.get("symbol") or ""),
            exchange=self._settings.option_exchange,
            action=ACTION_BUY,
            product=self._settings.order_product,
            lots=int((context.risk or {}).get("lots_cleared") or 0),
            lot_size=int(proposal.get("lot_size") or 0),
            quantity=0,
            limit_price=0.0,
            band_low=float(proposal.get("entry_price_low") or 0.0),
            band_high=float(proposal.get("entry_price_high") or 0.0),
            pending_order_id=None,
            strategy_tag=strategy_tag(self._settings, context.tick_id),
            deadline_utc=now,
            wait_seconds=0.0,
            approved_by=None,
            resolved_at_ist=None,
            broker_order_id=None,
            withdrawal=WITHDRAWAL_NOT_QUEUED,
            detail=detail,
            payload_json="{}",
            response_json="{}",
            defect=True,
            schema_version=SCHEMA_VERSION,
        )
        return SubmitResult(approval_id, status, detail)

    def _record(
        self,
        intent: OrderIntent,
        status: str,
        detail: str,
        *,
        response: dict[str, Any],
        pending_order_id: int | None = None,
        broker_order_id: str | None = None,
        approved_by: str | None = None,
    ) -> None:
        """Append one approval row for an intent that was built."""
        self._journal.record_approval(
            approval_id=intent.approval_id,
            status=status,
            tick_id=intent.tick_id,
            trace_id=intent.trace_id,
            tick_trace_id=intent.trace_id,
            proposal_id=intent.proposal_id,
            verdict_id=intent.verdict_id,
            created_at_utc=datetime.now(tz=UTC),
            trading_day=intent.trading_day,
            index_symbol=intent.index_symbol,
            symbol=intent.symbol,
            exchange=intent.exchange,
            action=intent.action,
            product=intent.product,
            lots=intent.lots,
            lot_size=intent.lot_size,
            quantity=intent.quantity,
            limit_price=intent.limit_price,
            band_low=intent.band_low,
            band_high=intent.band_high,
            pending_order_id=pending_order_id,
            strategy_tag=intent.strategy,
            deadline_utc=intent.deadline_utc,
            wait_seconds=0.0,
            approved_by=approved_by,
            resolved_at_ist=None,
            broker_order_id=broker_order_id,
            withdrawal=None if status == APPROVAL_PENDING else WITHDRAWAL_NOT_QUEUED,
            detail=detail,
            payload_json=json.dumps(intent.payload(), sort_keys=True),
            response_json=json.dumps(response, default=str, sort_keys=True),
            defect=status in APPROVAL_DEFECTS,
            schema_version=SCHEMA_VERSION,
        )
