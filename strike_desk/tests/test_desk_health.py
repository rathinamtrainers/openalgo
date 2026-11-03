from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from strike_desk.desk_health import (
    WATCHED_SIDS,
    build_report,
    cash_session,
    classify_tick,
    collect_host_facts,
    is_live_symbols_crash,
    main,
    render_card,
)


def test_watched_sids_cover_analyzer_coa_and_quadshield():
    by_sid = {row.sid: row for row in WATCHED_SIDS}
    assert by_sid[999431310].book_id == "analyzer-nifty"
    assert by_sid[999357114].kind == "coa"
    assert by_sid[999328734].expected_legs == 4
    assert by_sid[999575526].mapped is False
    assert by_sid[999575526].expected_legs == 4


def test_classify_stand_aside_vs_defect_vs_enter():
    aside = classify_tick("regime-weak-trend", "Declined: weak trend.")
    assert aside.disposition == "stand-aside"
    assert aside.live_symbols_crash is False

    enter = classify_tick("risk-cleared", "Entered: risk cleared.")
    assert enter.disposition == "enter"

    defect = classify_tick(
        "internal-error",
        "Declined: the tick raised TypeError before assembling a decision.",
    )
    assert defect.disposition == "defect"
    assert defect.live_symbols_crash is False


def test_live_symbols_crash_is_named_defect():
    klass = classify_tick(
        "internal-error",
        "TypeError: Journal.live_symbols() takes 1 positional argument but 2 were given",
    )
    assert klass.live_symbols_crash is True
    assert klass.disposition == "defect"
    assert klass.reason_code == "live-symbols-crash"
    assert is_live_symbols_crash("internal-error", "plan node live_symbols(trading_day)")


def test_cash_session_weekend_is_off():
    saturday = datetime(2026, 10, 31, 10, 0, tzinfo=UTC)
    session = cash_session(saturday)
    assert session["cash_session_on"] is False


def test_cash_session_tuesday_morning_is_on():
    # 04:40 UTC = 10:10 IST on Tue 3 Nov 2026
    tuesday = datetime(2026, 11, 3, 4, 40, tzinfo=UTC)
    session = cash_session(tuesday)
    assert session["trading_day"] == "2026-11-03"
    assert session["cash_session_on"] is True
    assert session["analyzer_entry_window"] is True


def _write_journal(path: Path, rows: list[tuple]) -> None:
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
                book_state_json TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO decisions (
                tick_id, created_at_utc, trading_day, index_symbol, trigger,
                outcome, reason_code, reason_text, regime_label, regime_confidence,
                latency_ms, book_state_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def test_report_marks_today_internal_error_as_defect_not_stand_aside(tmp_path):
    journal = tmp_path / "strike_desk.db"
    _write_journal(
        journal,
        [
            (
                "tick-err",
                "2026-11-03T08:26:00+00:00",
                "2026-11-03",
                "NIFTY",
                "schedule",
                "decline",
                "internal-error",
                "Declined: the tick raised TypeError before assembling a decision.",
                None,
                None,
                120,
                "{}",
            ),
            (
                "tick-ok-yesterday",
                "2026-11-02T08:26:00+00:00",
                "2026-11-02",
                "NIFTY",
                "schedule",
                "decline",
                "regime-weak-trend",
                "Declined: weak trend.",
                "trending",
                0.4,
                90,
                "{}",
            ),
        ],
    )
    facts = collect_host_facts(state_dir=tmp_path, journal=journal)
    facts["log_hits"] = [
        {
            "line": (
                "2026-11-03T13:56:00+05:30 host strike-desk[1]: "
                "TypeError: Journal.live_symbols() takes 1 positional argument but 2 were given"
            )
        },
        {
            "line": (
                "2026-11-03T14:30:06+05:30 host strike-desk[1]: tradetron hands exit_all -> 200 Ok"
            )
        },
    ]
    now = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
    report = build_report(facts, now=now)
    assert report.analyzer["ticks"] == 1
    assert report.analyzer["live_symbols_crashes"] == 1
    assert report.analyzer["dispositions"] == {"defect": 1}
    assert report.severity == "critical"
    assert "not a correct stand-aside" in report.headline.lower()
    card = render_card(report)
    assert "999431310" in card
    assert "999575526" in card
    assert report.ages["last_analyzer_hands_s"] is None
    assert report.ages["last_hands_s"] is not None


def test_report_treats_weak_trend_as_correct_stand_aside(tmp_path):
    journal = tmp_path / "strike_desk.db"
    _write_journal(
        journal,
        [
            (
                "tick-1",
                "2026-11-02T04:00:00+00:00",
                "2026-11-02",
                "NIFTY",
                "schedule",
                "decline",
                "regime-weak-trend",
                "Declined: weak trend.",
                "trending",
                0.41,
                100,
                "{}",
            )
        ],
    )
    facts = collect_host_facts(state_dir=tmp_path, journal=journal)
    now = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
    report = build_report(facts, now=now)
    assert report.analyzer["dispositions"] == {"stand-aside": 1}
    assert report.severity in {"ok", "degraded"}
    assert "stood aside correctly" in report.headline


def test_quadshield_flags_three_of_four():
    facts = {
        "source": "journal",
        "source_path": "/tmp/x",
        "decisions": [],
        "strikeshield": {"set1": True, "set2open": True, "open_legs": 3},
        "units": {},
        "log_hits": [],
    }
    now = datetime(2026, 11, 3, 5, 0, tzinfo=UTC)
    report = build_report(facts, now=now)
    assert report.quadshield["expected_legs"] == 4
    assert report.quadshield["observed_legs"] == 3
    assert report.quadshield["flag"]
    assert any("3-of-4" in item for item in report.alerts)


def test_cli_probe_and_card(tmp_path, capsys):
    journal = tmp_path / "strike_desk.db"
    _write_journal(
        journal,
        [
            (
                "tick-enter",
                "2026-10-28T04:15:00+00:00",
                "2026-10-28",
                "NIFTY",
                "schedule",
                "enter",
                "risk-cleared",
                "Entered: risk cleared.",
                "trending",
                0.8,
                200,
                "{}",
            )
        ],
    )
    assert main(["--journal", str(journal), "--state-dir", str(tmp_path), "--probe"]) == 0
    probe = json.loads(capsys.readouterr().out)
    assert probe["source"] == "journal"
    assert len(probe["decisions"]) == 1

    out_path = tmp_path / "report.json"
    code = main(
        [
            "--journal",
            str(journal),
            "--state-dir",
            str(tmp_path),
            "--write",
            str(out_path),
        ]
    )
    assert code in {0, 1}
    card = capsys.readouterr().out
    assert "Strike Desk health" in card
    assert "Monitor only" in card
    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["analyzer"]["sid"] == 999431310
