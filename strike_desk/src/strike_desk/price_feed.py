"""One option symbol's live price, from OpenAlgo's WebSocket proxy.

The proxy speaks JSON over ws://host:8765: authenticate with the API key, subscribe with a
symbol/exchange/mode, then receive {"type": "market_data", "data": {"ltp": ..., ...}} frames.
The desk uses LTP mode because the exit levels are premium levels and nothing here needs depth.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import ClientConnection, connect

from .config import Settings
from .errors import FeedUnavailable

logger = logging.getLogger(__name__)

SOURCE_WEBSOCKET = "websocket"
SOURCE_QUOTES = "quotes"
SOURCE_NONE = "none"

RECONNECT_BACKOFF_SECONDS = (1.0, 2.0, 5.0, 10.0)


@dataclass(frozen=True)
class Tick:
    """One observation: the premium, where it came from, and when we saw it."""

    price: float
    source: str
    received_at_utc: datetime

    def age_ms(self, now_utc: datetime) -> int:
        return max(0, int((now_utc - self.received_at_utc).total_seconds() * 1000))


def _parse_ltp(message: dict[str, Any]) -> float | None:
    """Pull a usable last-traded price out of one market_data frame."""
    if message.get("type") != "market_data":
        return None
    data = message.get("data")
    if not isinstance(data, dict):
        return None
    raw = data.get("ltp", data.get("last_price"))
    try:
        price = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


class PriceFeed:
    """A single-symbol LTP subscription on the OpenAlgo WebSocket proxy.

    One connection, one thread, closed before every reconnect. ``last_tick`` is the only thing
    the monitor reads, so the feed can stall or reconnect without the monitor blocking on it.
    """

    def __init__(self, settings: Settings, symbol: str, exchange: str) -> None:
        self._settings = settings
        self._symbol = symbol
        self._exchange = exchange
        self._api_key = settings.openalgo_api_key.get_secret_value()
        self._lock = threading.Lock()
        self._last: Tick | None = None
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._thread: threading.Thread | None = None
        self._failures = 0

    @property
    def symbol(self) -> str:
        return self._symbol

    def last_tick(self) -> Tick | None:
        with self._lock:
            return self._last

    def is_connected(self) -> bool:
        return self._connected.is_set()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"price-feed-{self._symbol}", daemon=True
        )
        self._thread.start()

    def wait_for_first_tick(self, timeout: float) -> bool:
        """Block briefly at adoption so the position is armed against a real price."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.last_tick() is not None:
                return True
            if self._stop.wait(0.1):
                return False
        return self.last_tick() is not None

    def close(self) -> None:
        """Stop the thread and let the connection close on its own path out."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
            if thread.is_alive():
                logger.warning("price feed thread for %s did not stop within 5s", self._symbol)
        self._thread = None
        self._connected.clear()

    # --- internals ---------------------------------------------------------

    def _record(self, price: float) -> None:
        with self._lock:
            self._last = Tick(
                price=price,
                source=SOURCE_WEBSOCKET,
                received_at_utc=datetime.now(tz=UTC),
            )

    def _handshake(self, websocket: ClientConnection) -> None:
        """Authenticate and subscribe. Anything unexpected is a FeedUnavailable."""
        websocket.send(json.dumps({"action": "authenticate", "api_key": self._api_key}))
        raw = websocket.recv(timeout=self._settings.ws_open_timeout_seconds)
        reply = json.loads(raw)
        if str(reply.get("status", "")).lower() not in {"success", "ok"}:
            raise FeedUnavailable(f"authentication refused: {str(reply.get('message'))[:200]}")
        websocket.send(
            json.dumps(
                {
                    "action": "subscribe",
                    "symbol": self._symbol,
                    "exchange": self._exchange,
                    "mode": "LTP",
                }
            )
        )
        raw = websocket.recv(timeout=self._settings.ws_open_timeout_seconds)
        reply = json.loads(raw)
        if str(reply.get("status", "")).lower() not in {"success", "ok"}:
            raise FeedUnavailable(f"subscribe refused: {str(reply.get('message'))[:200]}")
        logger.info("price feed subscribed to %s on %s", self._symbol, self._exchange)

    def _session(self) -> None:
        """One connection, from handshake to close. Returns when the feed must reconnect."""
        websocket = connect(
            self._settings.ws_url,
            open_timeout=self._settings.ws_open_timeout_seconds,
            close_timeout=5.0,
            max_queue=64,
        )
        try:
            self._handshake(websocket)
            self._connected.set()
            self._failures = 0
            while not self._stop.is_set():
                try:
                    raw = websocket.recv(timeout=1.0)
                except TimeoutError:
                    continue
                try:
                    message = json.loads(raw)
                except ValueError:
                    logger.warning("price feed received a non-JSON frame; ignoring it")
                    continue
                if not isinstance(message, dict):
                    continue
                price = _parse_ltp(message)
                if price is not None:
                    self._record(price)
        finally:
            self._connected.clear()
            try:
                websocket.close()
            except (WebSocketException, OSError):
                logger.debug("price feed close raced the peer; the socket is gone either way")

    def _run(self) -> None:
        """Reconnect forever until closed. Never raises out of the thread."""
        while not self._stop.is_set():
            try:
                self._session()
            except (FeedUnavailable, ConnectionClosed, WebSocketException, OSError, ValueError):
                logger.exception("price feed session for %s ended", self._symbol)
            except Exception:  # noqa: BLE001 — the feed thread must survive anything
                logger.exception("unexpected price feed failure for %s", self._symbol)
            if self._stop.is_set():
                break
            index = min(self._failures, len(RECONNECT_BACKOFF_SECONDS) - 1)
            delay = RECONNECT_BACKOFF_SECONDS[index]
            self._failures += 1
            logger.warning("price feed for %s reconnecting in %.0fs", self._symbol, delay)
            self._stop.wait(delay)
