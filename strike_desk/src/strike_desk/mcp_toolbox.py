"""The MCP toolbox: OpenAlgo's tool surface, scoped per specialist role."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Callable, Coroutine, Mapping
from concurrent.futures import Future as ThreadFuture
from concurrent.futures import TimeoutError as FuturesTimeout
from types import MappingProxyType
from typing import Any, Protocol

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import StdioConnection
from langchain_mcp_adapters.tools import load_mcp_tools

from .config import Settings
from .errors import McpUnavailable
from .specialists import ROLE_REGIME, ROLE_STRATEGIST

logger = logging.getLogger(__name__)

SERVER_NAME = "openalgo"

#: Everything the Regime Analyst may reach. Unchanged from iteration 02.
REGIME_TOOLS: tuple[str, ...] = (
    "get_quote",
    "get_historical_data",
    "get_trend_snapshot",
    "get_momentum_snapshot",
    "get_volatility_snapshot",
    "get_expiry_dates",
)

#: Everything the Options Strategist may reach. It reads the chain, resolves a symbol and
#: prices the Greeks; it forms its own directional view from trend and momentum. Nothing
#: here can place, modify, cancel or square off an order, and nothing here can send a
#: message.
STRATEGIST_TOOLS: tuple[str, ...] = (
    "get_quote",
    "get_expiry_dates",
    "get_option_chain",
    "get_option_symbol",
    "get_option_greeks",
    "get_trend_snapshot",
    "get_momentum_snapshot",
)

#: The union, loaded once from one session.
REQUIRED_TOOLS: tuple[str, ...] = tuple(sorted(set(REGIME_TOOLS) | set(STRATEGIST_TOOLS)))

TOOLS_BY_ROLE: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {ROLE_REGIME: REGIME_TOOLS, ROLE_STRATEGIST: STRATEGIST_TOOLS}
)

#: A whitelisted tool name may never begin with a verb that changes state at the broker.
FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "place",
    "modify",
    "cancel",
    "close",
    "square",
    "send",
    "set",
    "toggle",
)


def _assert_read_only(names: tuple[str, ...]) -> None:
    """Import-time guardrail: a mutating tool cannot reach a whitelist by accident."""
    offenders = sorted(name for name in names if name.startswith(FORBIDDEN_PREFIXES))
    if offenders:
        raise ValueError(
            f"whitelisted tools must be read-only; these are not: {', '.join(offenders)}"
        )


_assert_read_only(REQUIRED_TOOLS)


class ToolSource(Protocol):
    """What a specialist needs from whatever holds its tools."""

    def ensure_started(self) -> None: ...

    def tools(self, role: str) -> list[BaseTool]: ...

    def submit(self, factory: Callable[[], Coroutine[Any, Any, Any]], timeout: float) -> Any: ...


def select_tools(loaded: list[BaseTool]) -> list[BaseTool]:
    """Keep exactly the union whitelist, in order, and fail closed if one is absent."""
    by_name = {tool.name: tool for tool in loaded}
    missing = [name for name in REQUIRED_TOOLS if name not in by_name]
    if missing:
        raise McpUnavailable(f"MCP server is missing required tools: {', '.join(missing)}")
    return [by_name[name] for name in REQUIRED_TOOLS]


def tools_for_role(loaded: list[BaseTool], role: str) -> list[BaseTool]:
    """The slice of the loaded union that one role may reach."""
    allowed = TOOLS_BY_ROLE.get(role)
    if allowed is None:
        raise McpUnavailable(f"no tool whitelist is defined for role {role!r}")
    by_name = {tool.name: tool for tool in loaded}
    return [by_name[name] for name in allowed if name in by_name]


class McpToolbox:
    """Owns the MCP subprocess, its session and its event loop. One per process."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._serving: ThreadFuture | None = None
        self._shutdown: asyncio.Event | None = None
        self._ready = threading.Event()
        self._tools: list[BaseTool] = []
        self._error: BaseException | None = None

    # -- lifecycle ----------------------------------------------------------

    def ensure_started(self) -> None:
        """Start the session, or restart it when the previous one has died."""
        with self._lock:
            healthy = (
                self._thread is not None
                and self._thread.is_alive()
                and self._error is None
                and self._serving is not None
                and not self._serving.done()
            )
        if healthy:
            return
        self.close()
        self.start()

    def start(self) -> None:
        """Spawn the server, open one session, and block until its tools are loaded."""
        connection = self._connection()
        with self._lock:
            self._ready.clear()
            self._error = None
            self._tools = []
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=self._run_loop, args=(loop,), name="mcp-loop", daemon=True
            )
            thread.start()
            self._loop = loop
            self._thread = thread
            self._serving = asyncio.run_coroutine_threadsafe(self._serve(connection), loop)

        if not self._ready.wait(self._settings.mcp_startup_timeout_seconds):
            self.close()
            raise McpUnavailable(
                f"the MCP server did not become ready within "
                f"{self._settings.mcp_startup_timeout_seconds:.0f}s"
            )
        if self._error is not None:
            error = self._error
            self.close()
            raise McpUnavailable(f"MCP session failed to open: {type(error).__name__}: {error}")
        logger.info(
            "MCP session open with %d tools: %s",
            len(self._tools),
            ", ".join(tool.name for tool in self._tools),
        )

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def _connection(self) -> StdioConnection:
        python = self._settings.mcp_python
        script = self._settings.mcp_server_script
        if not python.is_file():
            raise McpUnavailable(f"no MCP interpreter at {python}")
        if not script.is_file():
            raise McpUnavailable(f"no MCP server script at {script}")
        return {
            "transport": "stdio",
            "command": str(python),
            "args": [
                str(script),
                self._settings.openalgo_api_key.get_secret_value(),
                self._settings.openalgo_base_url.rstrip("/"),
            ],
            "cwd": str(script.parent),
            "env": {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(self._settings.state_dir),
                "TZ": "Asia/Kolkata",
                "PYTHONUNBUFFERED": "1",
            },
        }

    async def _serve(self, connection: StdioConnection) -> None:
        """Hold the session open in the task that entered it, until shutdown."""
        self._shutdown = asyncio.Event()
        try:
            client = MultiServerMCPClient({SERVER_NAME: connection})
            async with client.session(SERVER_NAME) as session:
                self._tools = select_tools(await load_mcp_tools(session, server_name=SERVER_NAME))
                self._ready.set()
                await self._shutdown.wait()
        except Exception as exc:  # noqa: BLE001 — surfaced to the caller, never swallowed
            self._error = exc
            logger.warning("MCP session ended: %s: %s", type(exc).__name__, exc)
            self._ready.set()

    def close(self) -> None:
        """Shut the session, stop the loop, join the thread. Safe to call twice."""
        with self._lock:
            loop, thread = self._loop, self._thread
            serving, shutdown = self._serving, self._shutdown
            self._loop = self._thread = self._serving = self._shutdown = None
            self._tools = []
            self._error = None
            self._ready.clear()
        if loop is None:
            return
        if shutdown is not None:
            loop.call_soon_threadsafe(shutdown.set)
        if serving is not None:
            try:
                serving.result(timeout=10)
            except Exception:  # noqa: BLE001 — shutdown is best effort by design
                serving.cancel()
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=10)
        loop.close()
        logger.info("MCP session closed")

    # -- use ----------------------------------------------------------------

    def tools(self, role: str) -> list[BaseTool]:
        with self._lock:
            if not self._tools:
                raise McpUnavailable("no MCP tools are loaded")
            loaded = list(self._tools)
        selected = tools_for_role(loaded, role)
        if not selected:
            raise McpUnavailable(f"no tools are loaded for role {role!r}")
        return selected

    def submit(self, factory: Callable[[], Coroutine[Any, Any, Any]], timeout: float) -> Any:
        """Run one coroutine on the MCP loop, cancelling it if it outstays its deadline."""
        with self._lock:
            loop = self._loop
        if loop is None:
            raise McpUnavailable("the MCP session is not running")
        future = asyncio.run_coroutine_threadsafe(factory(), loop)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout:
            future.cancel()
            raise TimeoutError(f"exceeded the {timeout:.1f}s analyst deadline") from None
