"""The book-state snapshot: what the desk holds and what it can deploy, right now."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from .config import Settings
from .errors import BookStateUnavailable, OpenAlgoError
from .journal import Journal
from .openalgo_client import OpenAlgoClient


@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    exchange: str
    product: str
    quantity: int
    average_price: float
    ltp: float
    pnl: float


@dataclass(frozen=True)
class BookState:
    captured_at_utc: datetime
    flat: bool
    open_positions: tuple[OpenPosition, ...]
    available_cash: float
    utilised_margin: float
    realised_pnl: float
    unrealised_pnl: float
    decisions_today: int

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["captured_at_utc"] = self.captured_at_utc.isoformat()
        payload["open_positions"] = [asdict(position) for position in self.open_positions]
        return payload


def _as_float(value: Any, field: str) -> float:
    try:
        return float(str(value).replace(",", "").strip() or 0.0)
    except (TypeError, ValueError) as exc:
        raise BookStateUnavailable(f"field {field!r} was not numeric: {value!r}") from exc


def _as_int(value: Any, field: str) -> int:
    try:
        return int(float(str(value).replace(",", "").strip() or 0))
    except (TypeError, ValueError) as exc:
        raise BookStateUnavailable(f"field {field!r} was not an integer: {value!r}") from exc


def read_book_state(
    client: OpenAlgoClient,
    journal: Journal,
    settings: Settings,
    trading_day: str,
    now_utc: datetime,
) -> BookState:
    """Read positions and funds from OpenAlgo and fold them into one snapshot."""
    try:
        raw_positions = client.positionbook()
        raw_funds = client.funds()
    except OpenAlgoError as exc:
        raise BookStateUnavailable(str(exc)) from exc

    index = settings.index_symbol.upper()
    exchange = settings.option_exchange.upper()
    positions: list[OpenPosition] = []

    for row in raw_positions:
        if not isinstance(row, dict):
            raise BookStateUnavailable("position book contained a non-object row")
        symbol = str(row.get("symbol", "")).upper()
        if str(row.get("exchange", "")).upper() != exchange or not symbol.startswith(index):
            continue
        quantity = _as_int(row.get("quantity", 0), "quantity")
        if quantity == 0:
            continue
        positions.append(
            OpenPosition(
                symbol=symbol,
                exchange=exchange,
                product=str(row.get("product", "")),
                quantity=quantity,
                average_price=_as_float(row.get("average_price", 0), "average_price"),
                ltp=_as_float(row.get("ltp", 0), "ltp"),
                pnl=_as_float(row.get("pnl", 0), "pnl"),
            )
        )

    return BookState(
        captured_at_utc=now_utc,
        flat=not positions,
        open_positions=tuple(positions),
        available_cash=_as_float(raw_funds.get("availablecash", 0), "availablecash"),
        utilised_margin=_as_float(raw_funds.get("utiliseddebits", 0), "utiliseddebits"),
        realised_pnl=_as_float(raw_funds.get("m2mrealized", 0), "m2mrealized"),
        unrealised_pnl=_as_float(raw_funds.get("m2munrealized", 0), "m2munrealized"),
        decisions_today=journal.count_decisions(trading_day),
    )
