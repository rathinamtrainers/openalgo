import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.strategy_timeline_service import list_timeline


@pytest.fixture(autouse=True)
def _empty_sandbox_performance(monkeypatch):
    monkeypatch.setattr(
        "services.strategy_book_performance._load_sandbox_rows",
        lambda: ([], []),
    )


def _analyzer_book():
    return {
        "id": "analyzer-nifty",
        "name": "Strike Desk NIFTY Analyzer",
        "kind": "analyzer",
        "underlying": "NIFTY",
        "execution_target": "openalgo",
        "parked": False,
        "notes": "15-min",
    }


def test_analyzer_timeline_explains_weak_trend(monkeypatch):
    monkeypatch.setattr("services.strategy_timeline_service.get_book", lambda _book_id: _analyzer_book())
    monkeypatch.setattr(
        "services.strategy_timeline_service.list_decisions",
        lambda **_kwargs: {
            "decisions": [
                {
                    "tick_id": "t1",
                    "created_at_utc": "2026-10-19T05:38:00+00:00",
                    "trading_day": "2026-10-19",
                    "outcome": "decline",
                    "reason_code": "regime-weak-trend",
                    "reason_text": "Declined: weak trend.",
                    "regime_label": "trending",
                    "book": {"flat": True, "positions": []},
                }
            ]
        },
    )
    monkeypatch.setattr("services.strategy_timeline_service._analyzer_fill_events", lambda _limit: [])
    payload = list_timeline("analyzer-nifty")
    event = payload["events"][0]
    assert event["title"].startswith("Stood aside")
    assert "weak trend" in event["explanation"]
    assert event["kind"] == "decision"


def test_parked_overlay_explains_do_not_redeploy(monkeypatch):
    stored: list[dict] = []
    monkeypatch.setattr(
        "services.strategy_timeline_service.get_book",
        lambda _book_id: {
            "id": "ema120-bn",
            "name": "PARKED — EMA120 BANKNIFTY",
            "kind": "overlay",
            "underlying": "BANKNIFTY",
            "execution_target": "none",
            "parked": True,
            "notes": "Parked. Do not redeploy.",
        },
    )
    monkeypatch.setattr("services.strategy_timeline_service.latest_event", lambda _book_id: None)
    monkeypatch.setattr(
        "services.strategy_timeline_service.append_event",
        lambda fields: stored.append(fields) or True,
    )
    monkeypatch.setattr(
        "services.strategy_timeline_service.list_events",
        lambda _book_id, limit=200: list(stored),
    )
    monkeypatch.setattr("services.strategy_timeline_service._read_desk_snapshot", lambda: {})
    payload = list_timeline("ema120-bn")
    assert payload["events"]
    assert "Do not redeploy" in payload["events"][0]["explanation"]
    assert payload["events"][0]["reason_code"] == "overlay-parked"
    keys = payload["sections"]["key_decisions"]
    assert keys
    assert keys[0]["reason_code"] == "overlay-parked"


def test_quadshield_hold_explains_supervising(monkeypatch, tmp_path):
    stored: list[dict] = []
    monkeypatch.setattr(
        "services.strategy_timeline_service.get_book",
        lambda _book_id: {
            "id": "qs-nifty",
            "name": "QuadShield",
            "kind": "strikeshield",
            "underlying": "NIFTY",
            "execution_target": "tradetron-hands",
            "parked": False,
            "notes": "Hands",
        },
    )
    monkeypatch.setattr(
        "services.strategy_timeline_service._read_desk_snapshot",
        lambda: {"set1": True, "set2open": True, "strike_desk": "active"},
    )
    monkeypatch.setattr("services.strategy_timeline_service._shield_state_file", lambda: {})
    monkeypatch.setattr("services.strategy_timeline_service.latest_event", lambda _book_id: None)
    monkeypatch.setattr(
        "services.strategy_timeline_service.append_event",
        lambda fields: stored.append(fields) or True,
    )
    monkeypatch.setattr(
        "services.strategy_timeline_service.list_events",
        lambda _book_id, limit=200: list(stored),
    )
    payload = list_timeline("qs-nifty")
    assert payload["events"][0]["reason_code"] == "wings-shorts"
    assert "supervising" in payload["events"][0]["explanation"]
    keys = payload["sections"]["key_decisions"]
    assert keys
    assert keys[0]["reason_code"] == "wings-shorts"
    assert keys[0]["kind"] == "status"


def test_ofa_timeline_includes_journal_and_status(monkeypatch):
    stored: list[dict] = []
    monkeypatch.setattr(
        "services.strategy_timeline_service.get_book",
        lambda _book_id: {
            "id": "ofa-nifty",
            "name": "Strike Desk OFA NIFTY",
            "kind": "ofa",
            "underlying": "NIFTY",
            "execution_target": "openalgo",
            "parked": False,
            "notes": "Analyze CE/PE",
        },
    )
    monkeypatch.setattr(
        "services.strategy_timeline_service._read_desk_snapshot",
        lambda: {
            "ofa_enabled": True,
            "ofa_active": "active",
            "ofa_heartbeat_age_s": 2,
            "ofa_signal": "LONG",
        },
    )
    monkeypatch.setattr(
        "services.strategy_timeline_service._ofa_journal_events",
        lambda _limit: [
            {
                "event_id": "ofa:t1:ofa-long",
                "book_id": "ofa-nifty",
                "created_at_utc": "2026-10-19T05:40:00+00:00",
                "created_at_ist": "2026-10-19 11:10:00 IST",
                "trading_day": "2026-10-19",
                "kind": "decision",
                "title": "Buy CE · LONG",
                "outcome": "enter",
                "reason_code": "ofa-long",
                "explanation": "OFA read aggressive buyers on Nifty fut depth and bought ATM CE in Analyze.",
                "detail": "signal=LONG",
            }
        ],
    )
    monkeypatch.setattr("services.strategy_timeline_service._ofa_fill_events", lambda _limit: [])
    monkeypatch.setattr("services.strategy_timeline_service.latest_event", lambda _book_id: None)
    monkeypatch.setattr(
        "services.strategy_timeline_service.append_event",
        lambda fields: stored.append(fields) or True,
    )
    monkeypatch.setattr(
        "services.strategy_timeline_service.list_events",
        lambda _book_id, limit=200: list(stored),
    )
    payload = list_timeline("ofa-nifty")
    assert payload["book_id"] == "ofa-nifty"
    kinds = {row["kind"] for row in payload["events"]}
    assert "decision" in kinds
    assert "status" in kinds
    decision = next(row for row in payload["events"] if row["kind"] == "decision")
    assert decision["reason_code"] == "ofa-long"
    assert "ATM CE" in decision["explanation"]
    status = next(row for row in payload["events"] if row["kind"] == "status")
    assert status["reason_code"] == "ofa-long"
    keys = payload["sections"]["key_decisions"]
    assert any(row["kind"] == "decision" and row["reason_code"] == "ofa-long" for row in keys)
    assert any(row["kind"] == "status" and row["reason_code"] == "ofa-long" for row in keys)


def test_analyzer_tick_tagged_fill_lands_in_key_decisions(monkeypatch):
    monkeypatch.setattr(
        "services.strategy_timeline_service.get_book", lambda _book_id: _analyzer_book()
    )
    monkeypatch.setattr(
        "services.strategy_timeline_service.list_decisions",
        lambda **_kwargs: {"decisions": []},
    )
    monkeypatch.setattr(
        "services.strategy_timeline_service._analyzer_fill_events",
        lambda _limit: [
            {
                "event_id": "fill:34",
                "book_id": "analyzer-nifty",
                "created_at_utc": "2026-10-21T04:53:20+00:00",
                "created_at_ist": "2026-10-21 10:23:20 IST",
                "trading_day": "2026-10-21",
                "kind": "fill",
                "title": "Sandbox BUY NIFTY27OCT2622950PE",
                "outcome": "enter",
                "reason_code": "placeorder",
                "explanation": "OpenAlgo Analyze accepted a sandbox order from this book.",
                "detail": "success · NIFTY27OCT2622950PE · BUY",
            }
        ],
    )
    payload = list_timeline("analyzer-nifty")
    assert payload["latest"]["title"].startswith("Sandbox BUY")
    keys = payload["sections"]["key_decisions"]
    assert keys
    assert keys[0]["event_id"] == "fill:34"
    assert keys[0]["kind"] == "fill"


def test_analyzer_key_decisions_keep_enter_fill_when_kind_is_decision(monkeypatch):
    monkeypatch.setattr(
        "services.strategy_timeline_service.get_book", lambda _book_id: _analyzer_book()
    )
    monkeypatch.setattr(
        "services.strategy_timeline_service.list_decisions",
        lambda **_kwargs: {
            "decisions": [
                {
                    "tick_id": "4a74469e",
                    "created_at_utc": "2026-10-21T04:53:20+00:00",
                    "trading_day": "2026-10-21",
                    "outcome": "enter",
                    "reason_code": "risk-cleared",
                    "reason_text": "Entered: risk cleared.",
                    "regime_label": "trending",
                    "book": {"flat": False, "positions": ["NIFTY27OCT2622950PE"]},
                }
            ]
        },
    )
    monkeypatch.setattr(
        "services.strategy_timeline_service._analyzer_fill_events",
        lambda _limit: [
            {
                "event_id": "fill:34",
                "book_id": "analyzer-nifty",
                "created_at_utc": "2026-10-21T04:53:20.9+00:00",
                "created_at_ist": "2026-10-21 10:23:20 IST",
                "trading_day": "2026-10-21",
                "kind": "fill",
                "title": "Sandbox BUY NIFTY27OCT2622950PE",
                "outcome": "enter",
                "reason_code": "placeorder",
                "explanation": "OpenAlgo Analyze accepted a sandbox order from this book.",
                "detail": "success · NIFTY27OCT2622950PE · BUY",
            }
        ],
    )
    payload = list_timeline("analyzer-nifty", kind="decision")
    assert payload["latest"]["title"].startswith("Sandbox BUY")
    kinds = {row["kind"] for row in payload["events"]}
    assert kinds == {"decision"}
    keys = payload["sections"]["key_decisions"]
    assert any(row["event_id"] == "fill:34" for row in keys)
    assert any(row["kind"] == "decision" for row in keys)


def test_unknown_book_is_error(monkeypatch):
    monkeypatch.setattr("services.strategy_timeline_service.get_book", lambda _book_id: None)
    payload = list_timeline("missing")
    assert payload["status"] == "error"
    assert payload["events"] == []


def test_coa_timeline_explains_hold(monkeypatch):
    stored: list[dict] = []
    monkeypatch.setattr(
        "services.strategy_timeline_service.get_book",
        lambda _book_id: {
            "id": "coa-nifty",
            "name": "Strike Desk CoA NIFTY",
            "kind": "coa",
            "underlying": "NIFTY",
            "execution_target": "openalgo-tradetron",
            "parked": False,
            "notes": "Live Offline",
        },
    )
    monkeypatch.setattr("services.strategy_timeline_service._read_desk_snapshot", lambda: {})
    monkeypatch.setattr("services.strategy_timeline_service._coa_fill_events", lambda _limit: [])
    monkeypatch.setattr("services.strategy_timeline_service.coa_heartbeat_age_s", lambda: 12.0)
    monkeypatch.setattr(
        "services.strategy_timeline_service.coa_live_state",
        lambda: {"open": None, "last_exit": None},
    )
    monkeypatch.setattr("services.strategy_timeline_service.latest_event", lambda _book_id: None)
    monkeypatch.setattr(
        "services.strategy_timeline_service.append_event",
        lambda fields: stored.append(fields) or True,
    )
    monkeypatch.setattr(
        "services.strategy_timeline_service.list_events",
        lambda _book_id, limit=200: list(stored),
    )
    payload = list_timeline("coa-nifty")
    assert payload["events"]
    assert payload["events"][0]["reason_code"] == "coa-hold"
    assert "ltp flags stay at 0" in payload["events"][0]["explanation"]
    keys = payload["sections"]["key_decisions"]
    assert keys
    assert keys[0]["reason_code"] == "coa-hold"


def test_coa_timeline_explains_holding_ce(monkeypatch):
    stored: list[dict] = []
    monkeypatch.setattr(
        "services.strategy_timeline_service.get_book",
        lambda _book_id: {
            "id": "coa-nifty",
            "name": "Strike Desk CoA NIFTY",
            "kind": "coa",
            "underlying": "NIFTY",
            "execution_target": "openalgo-tradetron",
            "parked": False,
            "notes": "Live Offline",
        },
    )
    monkeypatch.setattr("services.strategy_timeline_service._read_desk_snapshot", lambda: {})
    monkeypatch.setattr("services.strategy_timeline_service._coa_fill_events", lambda _limit: [])
    monkeypatch.setattr("services.strategy_timeline_service.coa_heartbeat_age_s", lambda: 8.0)
    monkeypatch.setattr(
        "services.strategy_timeline_service.coa_live_state",
        lambda: {"open": {"side": "CE", "strike": 22700, "scenario": "EOS"}, "last_exit": None},
    )
    monkeypatch.setattr("services.strategy_timeline_service.latest_event", lambda _book_id: None)
    monkeypatch.setattr(
        "services.strategy_timeline_service.append_event",
        lambda fields: stored.append(fields) or True,
    )
    monkeypatch.setattr(
        "services.strategy_timeline_service.list_events",
        lambda _book_id, limit=200: list(stored),
    )
    payload = list_timeline("coa-nifty")
    assert payload["events"][0]["reason_code"] == "coa-buy-ce"
    assert "ltp_buy_ce" in payload["events"][0]["explanation"]
    assert payload["events"][0]["outcome"] == "enter"
