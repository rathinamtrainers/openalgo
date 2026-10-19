import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.stack_status_service import write_desk_snapshot
from services.strategy_books_service import list_strategy_books


def _seed(monkeypatch, books):
    monkeypatch.setattr("services.strategy_books_service.list_books", lambda: books)


def test_quadshield_live_entered_from_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "stack.json"
    monkeypatch.setattr("services.stack_status_service.STACK_STATUS_PATH", path)
    write_desk_snapshot(
        path,
        openalgo="active",
        strike_desk="active",
        strikeshield_enabled=True,
        strikeshield_heartbeat_age_s=4,
        set1=True,
        set2open=True,
    )
    _seed(
        monkeypatch,
        [
            {
                "id": "qs-nifty",
                "name": "QuadShield",
                "kind": "strikeshield",
                "underlying": "NIFTY",
                "execution_target": "tradetron-hands",
                "tt_sid": 999328734,
                "tt_template_id": 999159502,
                "parked": False,
                "notes": "",
                "sort_order": 10,
            }
        ],
    )
    rows = list_strategy_books()
    assert rows[0]["run_status"] == "live-entered"
    assert rows[0]["detail"] == "wings + shorts"
    assert rows[0]["execution_label"] == "Tradetron Hands"


def test_analyzer_book_uses_last_tick_and_audit_link(tmp_path, monkeypatch):
    path = tmp_path / "stack.json"
    monkeypatch.setattr("services.stack_status_service.STACK_STATUS_PATH", path)
    write_desk_snapshot(
        path,
        openalgo="active",
        strike_desk="active",
        strikeshield_enabled=True,
        strikeshield_heartbeat_age_s=4,
    )
    monkeypatch.setattr(
        "services.strategy_books_service.last_decision",
        lambda: {
            "outcome": "decline",
            "reason_code": "regime-weak-trend",
            "created_at_ist": "2026-10-19 10:53:00 IST",
        },
    )
    _seed(
        monkeypatch,
        [
            {
                "id": "analyzer-nifty",
                "name": "Strike Desk NIFTY Analyzer",
                "kind": "analyzer",
                "underlying": "NIFTY",
                "execution_target": "openalgo",
                "tt_sid": None,
                "tt_template_id": None,
                "parked": False,
                "notes": "",
                "sort_order": 5,
            }
        ],
    )
    rows = list_strategy_books()
    assert rows[0]["run_status"] == "active"
    assert rows[0]["execution_label"] == "OpenAlgo Analyze"
    assert rows[0]["audit_href"] == "/strategy/books/analyzer-nifty"
    assert "regime-weak-trend" in rows[0]["detail"]


def test_parked_book_stays_parked_when_mind_up(tmp_path, monkeypatch):
    path = tmp_path / "stack.json"
    monkeypatch.setattr("services.stack_status_service.STACK_STATUS_PATH", path)
    write_desk_snapshot(
        path,
        openalgo="active",
        strike_desk="active",
        strikeshield_enabled=True,
        strikeshield_heartbeat_age_s=4,
    )
    _seed(
        monkeypatch,
        [
            {
                "id": "ema120-bn",
                "name": "PARKED BN",
                "kind": "overlay",
                "underlying": "BANKNIFTY",
                "execution_target": "none",
                "tt_sid": 999308850,
                "tt_template_id": 999159338,
                "parked": True,
                "notes": "Parked",
                "sort_order": 90,
            }
        ],
    )
    rows = list_strategy_books()
    assert rows[0]["run_status"] == "parked"
    assert rows[0]["mind"] == "up"
    assert rows[0]["audit_href"] == "/strategy/books/ema120-bn"
