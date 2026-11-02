import json
import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.strategy_books_db import SEED_BOOKS
from services.stack_status_service import write_desk_snapshot
from services.strategy_books_service import (
    TARGET_LABELS,
    is_analyzer_order_strategy,
    is_coa_order_strategy,
    is_ofa_order_strategy,
    label_order_strategies,
    list_strategy_books,
)
from services.strategy_timeline_service import list_bank_board


@pytest.fixture(autouse=True)
def _empty_sandbox_performance(monkeypatch):
    monkeypatch.setattr(
        "services.strategy_book_performance._load_sandbox_rows",
        lambda: ([], []),
    )


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


def test_analyzer_dual_exec_label():
    assert TARGET_LABELS["openalgo-tradetron"] == "OpenAlgo Analyze + Tradetron Hands"


def test_ofa_book_live_entered_from_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "stack.json"
    monkeypatch.setattr("services.stack_status_service.STACK_STATUS_PATH", path)
    write_desk_snapshot(
        path,
        openalgo="active",
        strike_desk="active",
        strikeshield_enabled=False,
        strikeshield_heartbeat_age_s=None,
        ofa_enabled=True,
        ofa_active="active",
        ofa_heartbeat_age_s=3,
        ofa_signal="LONG",
    )
    _seed(
        monkeypatch,
        [
            {
                "id": "ofa-nifty",
                "name": "Strike Desk OFA NIFTY",
                "kind": "ofa",
                "underlying": "NIFTY",
                "execution_target": "tradetron-hands",
                "tt_sid": 999405618,
                "tt_template_id": 999214946,
                "parked": False,
                "notes": "",
                "sort_order": 8,
            }
        ],
    )
    rows = list_strategy_books()
    assert rows[0]["run_status"] == "live-entered"
    assert rows[0]["detail"] == "signal LONG"
    assert rows[0]["execution_label"] == "Tradetron Hands"
    assert rows[0]["tt_sid"] == 999405618
    assert rows[0]["audit_href"] == "/strategy/books/ofa-nifty"


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


def test_order_strategy_labels_map_desk_tags():
    assert label_order_strategies(["OFA"]) == "Strike Desk OFA NIFTY"
    assert label_order_strategies(["strike-desk-NIFTY"]) == "Strike Desk NIFTY Analyzer"
    assert label_order_strategies(["strike-desk:4a74469e"]) == "Strike Desk NIFTY Analyzer"
    assert label_order_strategies(["OFA", "OFA", "strike-desk-NIFTY"]) == (
        "Strike Desk OFA NIFTY · Strike Desk NIFTY Analyzer"
    )
    assert label_order_strategies(["", None]) == ""
    assert label_order_strategies(["CustomBot"]) == "CustomBot"
    assert is_analyzer_order_strategy("strike-desk:4a74469e")
    assert is_analyzer_order_strategy("strike-desk-NIFTY")
    assert not is_analyzer_order_strategy("OFA")
    assert not is_analyzer_order_strategy("")
    assert is_ofa_order_strategy("OFA")
    assert is_ofa_order_strategy("OFA:4a74469e")
    assert is_ofa_order_strategy("OFA-NIFTY")
    assert not is_ofa_order_strategy("strike-desk-NIFTY")
    assert label_order_strategies(["OFA:4a74469e"]) == "Strike Desk OFA NIFTY"
    assert is_coa_order_strategy("COA")
    assert is_coa_order_strategy("COA:tick")
    assert is_coa_order_strategy("COA-NIFTY")
    assert not is_coa_order_strategy("OFA")
    assert not is_coa_order_strategy("strike-desk-NIFTY")
    assert label_order_strategies(["COA"]) == "Strike Desk CoA NIFTY"
    assert label_order_strategies(["COA:tick"]) == "Strike Desk CoA NIFTY"


def _coa_book():
    return {
        "id": "coa-nifty",
        "name": "Strike Desk CoA NIFTY",
        "kind": "coa",
        "underlying": "NIFTY",
        "execution_target": "openalgo-tradetron",
        "tt_sid": 999357114,
        "tt_template_id": 999183250,
        "parked": False,
        "notes": "Live Offline",
        "sort_order": 6,
    }


def test_seed_includes_coa_not_live_auto():
    ids = {row["book_id"] for row in SEED_BOOKS}
    assert "coa-nifty" in ids
    assert all(row.get("tt_sid") != 999039070 for row in SEED_BOOKS)
    assert all(row.get("tt_template_id") != 8792589 for row in SEED_BOOKS)
    coa = next(row for row in SEED_BOOKS if row["book_id"] == "coa-nifty")
    assert coa["name"] == "Strike Desk CoA NIFTY"
    assert coa["execution_target"] == "openalgo-tradetron"
    assert coa["tt_sid"] == 999357114
    assert coa["tt_template_id"] == 999183250
    assert "never toggles live" in coa["notes"]


def test_coa_book_waiting_from_fresh_heartbeat(tmp_path, monkeypatch):
    path = tmp_path / "stack.json"
    monkeypatch.setattr("services.stack_status_service.STACK_STATUS_PATH", path)
    write_desk_snapshot(
        path,
        openalgo="active",
        strike_desk="down",
        strikeshield_enabled=False,
        strikeshield_heartbeat_age_s=None,
    )
    monkeypatch.setenv("STRIKE_DESK_STATE_DIR", str(tmp_path))
    (tmp_path / "coa.heartbeat").write_text("2026-10-22T10:30:00+05:30", encoding="utf-8")
    (tmp_path / "coa_live.json").write_text(
        json.dumps({"open": None, "last_exit": None}), encoding="utf-8"
    )
    _seed(monkeypatch, [_coa_book()])
    rows = list_strategy_books()
    assert rows[0]["run_status"] == "active"
    assert rows[0]["mind"] == "up"
    assert "waiting" in rows[0]["detail"].lower()
    assert rows[0]["kind_label"] == "CoA"
    assert rows[0]["execution_label"] == "OpenAlgo Analyze + Tradetron Hands"


def test_coa_book_live_entered_ce(tmp_path, monkeypatch):
    path = tmp_path / "stack.json"
    monkeypatch.setattr("services.stack_status_service.STACK_STATUS_PATH", path)
    write_desk_snapshot(
        path,
        openalgo="active",
        strike_desk="active",
        strikeshield_enabled=True,
        strikeshield_heartbeat_age_s=4,
    )
    monkeypatch.setenv("STRIKE_DESK_STATE_DIR", str(tmp_path))
    (tmp_path / "coa.heartbeat").write_text("2026-10-22T10:30:00+05:30", encoding="utf-8")
    (tmp_path / "coa_live.json").write_text(
        json.dumps({"open": {"side": "CE", "strike": 22700, "scenario": "EOS"}, "last_exit": None}),
        encoding="utf-8",
    )
    _seed(monkeypatch, [_coa_book()])
    rows = list_strategy_books()
    assert rows[0]["run_status"] == "live-entered"
    assert rows[0]["detail"] == "holding ATM CE"


def test_coa_book_mind_down_without_heartbeat(tmp_path, monkeypatch):
    path = tmp_path / "stack.json"
    monkeypatch.setattr("services.stack_status_service.STACK_STATUS_PATH", path)
    write_desk_snapshot(
        path,
        openalgo="active",
        strike_desk="active",
        strikeshield_enabled=True,
        strikeshield_heartbeat_age_s=4,
    )
    monkeypatch.setenv("STRIKE_DESK_STATE_DIR", str(tmp_path))
    _seed(monkeypatch, [_coa_book()])
    rows = list_strategy_books()
    assert rows[0]["run_status"] == "mind-down"
    assert "heartbeat" in rows[0]["detail"].lower()


def test_bank_board_attaches_plain_language_snippets(monkeypatch):
    monkeypatch.setattr(
        "services.strategy_books_service.list_strategy_books",
        lambda: [
            {
                "id": "coa-nifty",
                "name": "Strike Desk CoA NIFTY",
                "kind": "coa",
                "kind_label": "CoA",
                "underlying": "NIFTY",
                "execution_target": "openalgo-tradetron",
                "execution_label": "OpenAlgo Analyze + Tradetron Hands",
                "tt_sid": 999357114,
                "tt_template_id": 999183250,
                "parked": False,
                "notes": "Live Offline",
                "run_status": "active",
                "detail": "mind up — waiting (no EOS/EOR latch)",
                "mind": "up",
                "audit_href": "/strategy/books/coa-nifty",
            }
        ],
    )
    event = {
        "event_id": "coa-nifty:coa-hold:2026-10-22",
        "book_id": "coa-nifty",
        "created_at_utc": "2026-10-22T05:00:00+00:00",
        "created_at_ist": "2026-10-22 10:30:00 IST",
        "trading_day": "2026-10-22",
        "kind": "status",
        "title": "Waiting — no CoA latch",
        "outcome": "hold",
        "reason_code": "coa-hold",
        "explanation": "CoA is watching the chain every 5 minutes. No EOS/EOR latch, so Analyze stays idle and Hands ltp flags stay at 0.",
        "detail": "Live Offline",
    }
    monkeypatch.setattr(
        "services.strategy_timeline_service.list_timeline",
        lambda _book_id, **_kwargs: {
            "latest": event,
            "sections": {
                "key_decisions": [event],
                "event_log": [event],
                "audit_trail": [event],
            },
        },
    )
    rows = list_bank_board(snippet=5)
    assert rows[0]["status_plain"].startswith("CoA is watching")
    assert rows[0]["latest"]["reason_code"] == "coa-hold"
    assert len(rows[0]["sections"]["key_decisions"]) == 1
    assert len(rows[0]["sections"]["event_log"]) == 1
    assert len(rows[0]["sections"]["audit_trail"]) == 1
