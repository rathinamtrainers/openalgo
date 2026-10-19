from services.strategy_timeline_service import list_timeline


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


def test_unknown_book_is_error(monkeypatch):
    monkeypatch.setattr("services.strategy_timeline_service.get_book", lambda _book_id: None)
    payload = list_timeline("missing")
    assert payload["status"] == "error"
    assert payload["events"] == []
