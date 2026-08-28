"""The specialist port: one protocol, one registry, one hard timeout."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Any, Protocol, runtime_checkable

from .errors import SpecialistTimeout, SpecialistUnavailable

logger = logging.getLogger(__name__)

ROLE_REGIME = "regime"
ROLE_STRATEGIST = "strategist"
ROLE_RISK = "risk"

_executor: ThreadPoolExecutor | None = None
_executor_lock = Lock()


def get_executor() -> ThreadPoolExecutor:
    """The process-wide specialist executor. One pool, created once."""
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="specialist")
        return _executor


def shutdown_executor() -> None:
    """Release the pool's threads on service shutdown."""
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=False, cancel_futures=True)
            _executor = None


@dataclass(frozen=True)
class SpecialistRequest:
    tick_id: str
    index_symbol: str
    as_of: datetime
    book: dict[str, Any]


@dataclass(frozen=True)
class SpecialistResult:
    role: str
    payload: dict[str, Any] = field(default_factory=dict)
    model_version: str | None = None
    prompt_version: str | None = None
    token_cost_micros: int = 0


@runtime_checkable
class Specialist(Protocol):
    """What every agent the supervisor delegates to must implement."""

    role: str

    def run(self, request: SpecialistRequest) -> SpecialistResult: ...


class SpecialistRegistry:
    """Holds the specialists available to the supervisor this session."""

    def __init__(self) -> None:
        self._specialists: dict[str, Specialist] = {}
        self._lock = Lock()

    def register(self, specialist: Specialist) -> None:
        role = getattr(specialist, "role", "")
        if not role:
            raise ValueError("a specialist must declare a non-empty role")
        with self._lock:
            self._specialists[role] = specialist
        logger.info("registered specialist for role %r", role)

    def registered_roles(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._specialists))

    def consult(
        self, role: str, request: SpecialistRequest, timeout_seconds: float
    ) -> SpecialistResult:
        """Run one specialist under a hard timeout. Never returns a partial answer."""
        with self._lock:
            specialist = self._specialists.get(role)
        if specialist is None:
            raise SpecialistUnavailable(role, "no specialist registered for this role")

        future = get_executor().submit(specialist.run, request)
        try:
            result = future.result(timeout=timeout_seconds)
        except FuturesTimeout as exc:
            future.cancel()
            raise SpecialistTimeout(role, f"exceeded {timeout_seconds:.1f}s") from exc
        except (SpecialistTimeout, SpecialistUnavailable):
            # A specialist that policed its own deadline keeps its own reason code.
            raise
        except Exception as exc:
            raise SpecialistUnavailable(
                role, f"raised {type(exc).__name__}: {str(exc)[:160]}"
            ) from exc

        if not isinstance(result, SpecialistResult) or result.role != role:
            raise SpecialistUnavailable(role, "returned a malformed result")
        return result
