import json
import sqlite3
from datetime import UTC, datetime

from services.desk_decisions_service import last_decision, list_decisions


def _write_journal(path, rows):
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE decisions (
                id INTEGER PRIMARY KEY,
                tick_id TEXT,
                created_at_utc TEXT,
                trading_day TEXT,
                index_symbol TEXT,
                trigger TEXT,
                outcome TEXT,
                reason_code TEXT,
                reason_text TEXT,
                regime_label TEXT,
                regime_confidence REAL,
                latency_ms INTEGER,
                trace_complete INTEGER,
                book_state_json TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO decisions (
                tick_id, created_at_utc, trading_day, index_symbol, trigger,
                outcome, reason_code, reason_text, regime_label, regime_confidence,
                latency_ms, trace_complete, book_state_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def test_list_decisions_reads_sqlite_journal(tmp_path, monkeypatch):
    journal = tmp_path / "strike_desk.db"
    _write_journal(
        journal,
        [
            (
                "tick-2",
                datetime(2026, 10, 19, 5, 23, tzinfo=UTC).isoformat(),
                "2026-10-19",
                "NIFTY",
                "schedule",
                "decline",
                "regime-weak-trend",
                "Declined: weak trend.",
                "trending",
                0.71,
                1840,
                1,
                '{"flat": true, "open_positions": [], "decisions_today": 8}',
            ),
            (
                "tick-1",
                datetime(2026, 10, 14, 7, 40, tzinfo=UTC).isoformat(),
                "2026-10-14",
                "NIFTY",
                "schedule",
                "enter",
                "risk-cleared",
                "Entered: risk cleared.",
                "trending",
                0.8,
                2100,
                1,
                '{"flat": true, "open_positions": [], "decisions_today": 3}',
            ),
        ],
    )
    monkeypatch.setenv("STRIKE_DESK_JOURNAL", str(journal))
    payload = list_decisions(limit=10)
    assert payload["readable"] is True
    assert payload["source"] == "journal"
    assert payload["counts"]["decline"] == 1
    assert payload["counts"]["enter"] == 1
    assert payload["decisions"][0]["reason_code"] == "regime-weak-trend"
    assert payload["decisions"][0]["created_at_ist"].endswith("IST")
    assert last_decision()["tick_id"] == "tick-2"


def test_list_decisions_filters_day_and_outcome(tmp_path, monkeypatch):
    journal = tmp_path / "strike_desk.db"
    _write_journal(
        journal,
        [
            (
                "tick-d",
                datetime(2026, 10, 19, 5, 23, tzinfo=UTC).isoformat(),
                "2026-10-19",
                "NIFTY",
                "schedule",
                "decline",
                "regime-weak-trend",
                "Declined.",
                None,
                None,
                10,
                1,
                "{}",
            ),
            (
                "tick-e",
                datetime(2026, 10, 14, 7, 40, tzinfo=UTC).isoformat(),
                "2026-10-14",
                "NIFTY",
                "schedule",
                "enter",
                "risk-cleared",
                "Entered.",
                None,
                None,
                10,
                1,
                "{}",
            ),
        ],
    )
    monkeypatch.setenv("STRIKE_DESK_JOURNAL", str(journal))
    payload = list_decisions(day="2026-10-14", outcome="enter")
    assert [row["tick_id"] for row in payload["decisions"]] == ["tick-e"]


def test_jsonl_fallback_when_journal_missing(tmp_path, monkeypatch):
    export = tmp_path / "decisions.jsonl"
    export.write_text(
        json.dumps(
            {
                "tick_id": "tick-x",
                "created_at_utc": "2026-10-19T05:23:00+00:00",
                "trading_day": "2026-10-19",
                "index_symbol": "NIFTY",
                "trigger": "schedule",
                "outcome": "hold",
                "reason_code": "position-open",
                "reason_text": "Held: open PE.",
                "regime_label": None,
                "regime_confidence": None,
                "latency_ms": 900,
                "trace_complete": True,
                "book_state_json": json.dumps(
                    {
                        "flat": False,
                        "open_positions": [{"symbol": "NIFTY19OCT2622400PE"}],
                    }
                ),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("STRIKE_DESK_JOURNAL", raising=False)
    monkeypatch.setenv("STRIKE_DESK_STATE_DIR", str(tmp_path / "missing"))
    monkeypatch.setenv("STRIKE_DESK_DECISIONS_EXPORT", str(export))
    payload = list_decisions()
    assert payload["source"] == "export"
    assert payload["decisions"][0]["reason_code"] == "position-open"
    assert payload["decisions"][0]["book"]["positions"] == ["NIFTY19OCT2622400PE"]
