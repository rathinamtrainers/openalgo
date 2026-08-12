"""Specialist registry: registration, timeout, and malformed answers."""

from __future__ import annotations

import time

import pytest

from strike_desk.errors import SpecialistTimeout, SpecialistUnavailable
from strike_desk.specialists import SpecialistRequest, SpecialistResult
from tests.conftest import StubSpecialist


def a_request() -> SpecialistRequest:
    from datetime import UTC, datetime

    return SpecialistRequest(
        tick_id="tick-1", index_symbol="NIFTY", as_of=datetime.now(tz=UTC), book={}
    )


def test_unregistered_role_is_unavailable(registry):
    with pytest.raises(SpecialistUnavailable) as info:
        registry.consult("regime", a_request(), 1.0)
    assert info.value.role == "regime"


def test_registered_specialist_answers(registry):
    registry.register(StubSpecialist(payload={"label": "trending", "confidence": 0.8}))
    result = registry.consult("regime", a_request(), 1.0)
    assert result.payload["label"] == "trending"
    assert registry.registered_roles() == ("regime",)


def test_slow_specialist_times_out(registry):
    class Slow:
        role = "regime"

        def run(self, request):
            time.sleep(2.0)
            return SpecialistResult(role="regime")

    registry.register(Slow())
    with pytest.raises(SpecialistTimeout):
        registry.consult("regime", a_request(), 0.2)


def test_raising_specialist_is_unavailable(registry):
    class Broken:
        role = "regime"

        def run(self, request):
            raise RuntimeError("boom")

    registry.register(Broken())
    with pytest.raises(SpecialistUnavailable, match="RuntimeError"):
        registry.consult("regime", a_request(), 1.0)


def test_wrong_role_in_result_is_unavailable(registry):
    registry.register(StubSpecialist(role="regime"))
    registry._specialists["regime"] = StubSpecialist(role="something-else")  # noqa: SLF001
    with pytest.raises(SpecialistUnavailable, match="malformed"):
        registry.consult("regime", a_request(), 1.0)


def test_a_specialist_without_a_role_cannot_register(registry):
    class Nameless:
        role = ""

        def run(self, request):
            return SpecialistResult(role="")

    with pytest.raises(ValueError, match="non-empty role"):
        registry.register(Nameless())
