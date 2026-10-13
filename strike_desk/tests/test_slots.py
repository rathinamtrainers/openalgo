"""Named strategy slots: isolation, default overlay, and shared-host safety."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from freezegun import freeze_time
from pydantic import ValidationError

from strike_desk.book_state import read_book_state
from strike_desk.config import Settings
from strike_desk.ema120 import format_trailer
from strike_desk.graph import _decide_outcome, _wants_a_contract
from strike_desk.service import StrikeDeskService
from strike_desk.slots import (
    bind_slot,
    isolation_active,
    owns_strategy_tag,
    parse_slot_ids,
    resolve_slot_ids,
)
from tests.conftest import position
from tests.position_fixtures import seed_armed_position, seed_chain
from tests.test_ema120 import WEAK_ADX
from tests.test_position_monitor import (
    FakeFeed,
    _patch_price_feed,
    build,
    tick,
)


def test_default_slots_is_overlay_only(settings: Settings):
    assert settings.slots == "overlay"
    assert settings.strategy_id == "overlay"
    assert parse_slot_ids(settings.slots) == ("overlay",)
    assert isolation_active(settings) is False


def test_unknown_slots_are_rejected_at_settings(tmp_path):
    """Preventive: a typo in STRIKE_DESK_SLOTS must fail closed, not silently overlay-only."""
    with pytest.raises(ValidationError, match="unknown strike-desk slot"):
        Settings(
            openalgo_api_key="test-key-0123456789",
            state_dir=tmp_path / "state",
            slots="overlay,unknown",
            tick_budget_seconds=90.0,
            specialist_timeout_seconds=25.0,
            strategist_timeout_seconds=35.0,
        )


def test_legacy_playbook_mode_is_a_single_ema_slot(settings: Settings):
    settings.playbook_mode = "ema120"
    assert resolve_slot_ids(settings) == ("ema120",)


def test_bind_slot_keeps_the_host_journal_on_overlay(settings: Settings):
    host = settings.state_dir
    overlay = bind_slot(settings.model_copy(update={"slots": "overlay,ema120"}), "overlay")
    ema = bind_slot(settings.model_copy(update={"slots": "overlay,ema120"}), "ema120")
    assert overlay.state_dir == host
    assert overlay.playbook_mode == "overlay"
    assert ema.state_dir == host / "ema120"
    assert ema.playbook_mode == "ema120"
    assert ema.order_product == "NRML"
    assert ema.order_strategy_prefix == "sd-ema120"
    assert isolation_active(ema) is True


def test_overlay_prefix_still_owns_historic_tags(settings: Settings):
    assert owns_strategy_tag(settings, "strike-desk:abcd1234") is True
    assert owns_strategy_tag(settings, "sd-ema120:ffff0000") is False


def test_the_host_builds_a_runtime_per_listed_slot(settings: Settings):
    host = settings.model_copy(update={"slots": "overlay,ema120", "anthropic_api_key": None})
    service = StrikeDeskService(host)
    try:
        assert [item.slot_id for item in service._slots] == ["overlay", "ema120"]
        assert service._slots[0].settings.playbook_mode == "overlay"
        assert service._slots[1].settings.playbook_mode == "ema120"
        assert service._slots[1].settings.order_product == "NRML"
        assert service._slots[1].settings.order_strategy_prefix == "sd-ema120"
    finally:
        service.shutdown()


def test_overlay_still_binds_weak_adx_when_slots_default(settings: Settings):
    assert settings.slots == "overlay"
    state = {
        "book": {"open_positions": []},
        "regime_label": "trending",
        "regime_confidence": 0.80,
        "trend_snapshot": WEAK_ADX,
    }
    assert _wants_a_contract(state, settings) is False
    outcome, code, _text = _decide_outcome(state, settings)
    assert (outcome, code) == ("decline", "regime-weak-trend")


def test_isolated_ema_book_ignores_an_overlay_lot(client, journal, settings, openalgo):
    isolated = settings.model_copy(
        update={"slots": "overlay,ema120", "strategy_id": "ema120", "playbook_mode": "ema120"}
    )
    openalgo.post("/api/v1/positionbook").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": [position(symbol="NIFTY28JUL2624500CE")],
            },
        )
    )
    book = read_book_state(client, journal, isolated, "2026-07-22", datetime.now(tz=UTC))
    assert book.flat is True
    assert book.open_positions == ()


def test_isolated_overlay_still_sees_its_journalled_lot(client, journal, settings, openalgo):
    isolated = settings.model_copy(update={"slots": "overlay,ema120"})
    seed_armed_position(journal, symbol="NIFTY28JUL2624500CE", trading_day="2026-07-22")
    openalgo.post("/api/v1/positionbook").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "data": [position(symbol="NIFTY28JUL2624500CE")]},
        )
    )
    book = read_book_state(client, journal, isolated, "2026-07-22", datetime.now(tz=UTC))
    assert book.flat is False
    assert book.open_positions[0].symbol == "NIFTY28JUL2624500CE"


def test_ema120_reaches_the_strategist_while_overlay_is_in_a_trade(settings: Settings):
    ema = settings.model_copy(
        update={"slots": "overlay,ema120", "strategy_id": "ema120", "playbook_mode": "ema120"}
    )
    state = {
        "book": {"open_positions": []},
        "regime_label": "trending",
        "regime_confidence": 1.0,
        "trend_snapshot": WEAK_ADX,
        "ema_signal": {
            "side": "long",
            "ema_high": 25010.0,
            "ema_low": 24820.0,
            "close": 25040.0,
        },
        "ema_no_signal": False,
    }
    assert _wants_a_contract(state, ema) is True
    _outcome, code, _text = _decide_outcome(state, ema)
    assert code != "position-open"
    assert code != "position-unmanaged"
    assert code != "regime-weak-trend"
    assert code == "specialist-unavailable"


def test_overlay_fifteen_does_not_flatten_an_ema_lot(
    oa_http, monitor_settings, journal, monkeypatch
):
    isolated = monitor_settings.model_copy(update={"slots": "overlay,ema120"})
    monitor = build(isolated, journal)
    oa_http.post("/api/v1/positionbook").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {
                        "symbol": "NIFTY30SEP2625000CE",
                        "quantity": 75,
                        "average_price": 102.5,
                        "strategy": "sd-ema120:deadbeef",
                    }
                ],
            },
        )
    )
    close = oa_http.post("/api/v1/closeposition").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    with freeze_time("2026-09-08T09:35:00+00:00", ignore=["threading"]):
        monitor._adopt_or_flatten_book()
    assert close.call_count == 0
    monitor.close()


def test_ema120_band_exit_does_not_close_an_overlay_lot(
    oa_http, monitor_settings, journal, monkeypatch
):
    trailer = format_trailer("long", 25100.0, 24900.0)
    ids = seed_chain(
        journal,
        rationale=f"EMA120 ATM CE. {trailer}",
        symbol="NIFTY30SEP2625000CE",
        stop=80.0,
        target=140.0,
    )
    oa_http.post("/api/v1/positionbook").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {
                        "symbol": "NIFTY30SEP2625000CE",
                        "quantity": 75,
                        "average_price": 102.5,
                    },
                    {
                        "symbol": "NIFTY30SEP2624800PE",
                        "quantity": 75,
                        "average_price": 90.0,
                        "strategy": "strike-desk:overlay1",
                    },
                ],
            },
        )
    )
    _patch_price_feed(monkeypatch, lambda *a, **k: FakeFeed(tick(110.0)))
    oa_http.post("/api/v1/quotes").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"ltp": 24750.0, "low": 24750.0, "high": 25000.0},
            },
        )
    )
    monitor = build(monitor_settings, journal)
    monitor._adopt(ids["order_id"])
    managed = monitor._position
    assert managed is not None
    assert managed.symbol == "NIFTY30SEP2625000CE"
    trigger = monitor._ema_trigger(managed, tick(110.0), datetime.now(tz=UTC))
    assert trigger is not None
    assert managed.symbol != "NIFTY30SEP2624800PE"
    monitor.close()
