from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from strike_desk.desk_health import (
    STATUS_PING_DAY,
    WATCHED_SIDS,
    build_report,
    cash_session,
    classify_tick,
    collect_host_facts,
    is_live_symbols_crash,
    group_tick_rows,
    latest_tick_severity,
    main,
    public_tick_rows,
    render_alert,
    render_card,
    render_ticks_html,
    resolve_telegram_secrets,
    should_alert,
    telegram_status_payload,
    write_ticks,
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
    assert is_live_symbols_crash(
        "internal-error",
        "live_symbols TypeError: takes 1 positional argument but 2 were given",
    )
    assert is_live_symbols_crash("internal-error", "plan node live_symbols(trading_day)") is False
    assert (
        is_live_symbols_crash(
            "internal-error",
            "Declined: the tick raised AttributeError before assembling a decision.",
        )
        is False
    )


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
                "TypeError: Journal.live_symbols() takes 1 positional argument but 2 were given",
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


def test_stale_journalctl_does_not_relabel_attributeerror(tmp_path):
    journal = tmp_path / "strike_desk.db"
    _write_journal(
        journal,
        [
            (
                "tick-ae",
                "2026-11-04T05:29:24+00:00",
                "2026-11-04",
                "NIFTY",
                "schedule",
                "decline",
                "internal-error",
                "Declined: the tick raised AttributeError before assembling a decision.",
                None,
                None,
                90,
                "{}",
            )
        ],
    )
    facts = collect_host_facts(state_dir=tmp_path, journal=journal)
    facts["log_hits"] = [
        {
            "line": (
                "2026-11-03T13:56:00+05:30 host strike-desk[1]: "
                "TypeError: Journal.live_symbols() takes 1 positional argument but 2 were given"
            )
        }
    ]
    now = datetime(2026, 11, 4, 6, 0, tzinfo=UTC)
    report = build_report(facts, now=now)
    assert report.analyzer["ticks"] == 1
    assert report.analyzer["live_symbols_crashes"] == 0
    assert report.analyzer["dispositions"] == {"defect": 1}


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
    assert {row["sid"] for row in saved["books"]} == {sid.sid for sid in WATCHED_SIDS}


def test_every_catalogued_book_has_alignment():
    facts = {
        "source": "journal",
        "source_path": "/tmp/x",
        "decisions": [],
        "units": {
            "strike-desk": "active",
            "strike-desk-coa": "active",
            "openalgo": "active",
        },
        "strikeshield": {"set1": False, "set2open": False},
        "log_hits": [],
        "coa_heartbeat": {"age_s": 9},
        "heartbeat": {"age_s": 40},
    }
    now = datetime(2026, 11, 3, 5, 0, tzinfo=UTC)
    report = build_report(facts, now=now)
    assert [book["sid"] for book in report.books] == [sid.sid for sid in WATCHED_SIDS]
    kinds = {book["kind"] for book in report.books}
    assert kinds == {"analyzer", "coa", "strikeshield", "ofa", "overlay"}


def test_alert_text_lists_broken_books_and_skips_when_aligned(tmp_path):
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
                "TypeError: Journal.live_symbols() takes 1 positional argument but 2 were given",
                None,
                None,
                120,
                "{}",
            )
        ],
    )
    facts = collect_host_facts(state_dir=tmp_path, journal=journal)
    facts["units"] = {"strike-desk": "failed", "strike-desk-coa": "active"}
    facts["strikeshield"] = {"set1": True, "set2open": True, "open_legs": 3}
    now = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
    report = build_report(facts, now=now)
    assert should_alert(report) is True
    text = render_alert(report)
    assert "999431310" in text
    assert "live_symbols" in text
    assert "3-of-4" in text
    assert "Monitor only" in text
    assert "999575526" in text
    assert "Analyzer ticks today (1):" in text
    assert "internal-error" in text


def test_status_ping_today_lists_every_tick_even_when_ok(tmp_path):
    journal = tmp_path / "strike_desk.db"
    _write_journal(
        journal,
        [
            (
                "tick-a",
                "2026-11-04T04:00:00+00:00",
                STATUS_PING_DAY,
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
            (
                "tick-b",
                "2026-11-04T04:15:00+00:00",
                STATUS_PING_DAY,
                "NIFTY",
                "schedule",
                "decline",
                "regime-weak-trend",
                "Declined: weak trend.",
                "trending",
                0.41,
                88,
                "{}",
            ),
        ],
    )
    facts = collect_host_facts(state_dir=tmp_path, journal=journal)
    facts["units"] = {
        "strike-desk": "active",
        "strike-desk-coa": "active",
        "openalgo": "active",
    }
    facts["strikeshield"] = {"set1": False, "set2open": False}
    facts["coa_heartbeat"] = {"age_s": 9}
    facts["heartbeat"] = {"age_s": 40}
    now = datetime(2026, 11, 4, 5, 0, tzinfo=UTC)
    report = build_report(facts, now=now)
    assert report.analyzer["day"] == STATUS_PING_DAY
    assert report.severity in {"ok", "degraded"}
    assert should_alert(report) is True
    text = render_alert(report)
    assert "today-only ping even if aligned" in text
    assert "Analyzer ticks today (2):" in text
    assert text.index("09:30 IST") < text.index("09:45 IST")
    assert text.count("regime-weak-trend") >= 2


def test_other_days_skip_telegram_when_aligned(tmp_path):
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
    facts["units"] = {
        "strike-desk": "active",
        "strike-desk-coa": "active",
        "openalgo": "active",
    }
    facts["strikeshield"] = {"set1": False, "set2open": False}
    now = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
    report = build_report(facts, now=now)
    assert report.analyzer["day"] == "2026-11-02"
    assert report.severity in {"ok", "degraded"}
    assert should_alert(report) is False


def test_telegram_secrets_missing_without_env(monkeypatch):
    for key in (
        "TELEGRAM_CHAT_BOT_TOKEN",
        "DESK_HEALTH_TELEGRAM_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "DESK_HEALTH_CHAT_ID",
        "ARJUN_ADMIN_IDS",
        "OWNER_CHAT_ID",
        "DESK_HEALTH_TELEGRAM_ENV",
        "DESK_HEALTH_SUBSCRIBER_DB",
        "DATA_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    secrets = resolve_telegram_secrets()
    assert secrets["ok"] == "no"
    assert secrets["token"] == ""
    status = telegram_status_payload(secrets)
    assert status["token_configured"] is False
    assert status["chat_configured"] is False
    assert "chat_id" not in status


def test_telegram_skips_public_channel(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_BOT_TOKEN", "dummy:token")
    monkeypatch.setenv("OWNER_CHAT_ID", "-1001234567890")
    for key in (
        "DESK_HEALTH_CHAT_ID",
        "ARJUN_ADMIN_IDS",
        "DESK_HEALTH_SUBSCRIBER_DB",
        "DATA_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    secrets = resolve_telegram_secrets()
    assert secrets["ok"] == "no"
    assert secrets["chat_id"] == ""
    assert secrets["chat_ids"] == []


def test_telegram_falls_back_to_active_subscribers(tmp_path, monkeypatch):
    db = tmp_path / "arjun_subscribers.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            CREATE TABLE arjun_subscribers (
                telegram_id INTEGER PRIMARY KEY,
                status TEXT,
                plan TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO arjun_subscribers VALUES (?, ?, ?)",
            [
                (123456789, "active", "free"),
                (987654321, "active", "free"),
                (111, "revoked", "free"),
                (-100555, "active", "free"),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setenv("TELEGRAM_CHAT_BOT_TOKEN", "dummy:token")
    monkeypatch.setenv("OWNER_CHAT_ID", "-1001234567890")
    monkeypatch.setenv("DESK_HEALTH_SUBSCRIBER_DB", str(db))
    for key in ("DESK_HEALTH_CHAT_ID", "ARJUN_ADMIN_IDS", "DATA_DIR"):
        monkeypatch.delenv(key, raising=False)
    secrets = resolve_telegram_secrets()
    assert secrets["ok"] == "yes"
    assert secrets["chat_source"] == "arjun_subscribers"
    assert secrets["chat_ids"] == ["123456789", "987654321"]
    status = telegram_status_payload(secrets)
    assert status["chat_count"] == 2
    assert status["chat_source"] == "arjun_subscribers"
    assert "123456789" not in json.dumps(status)


def test_dry_run_alert_prints_without_sending(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_BOT_TOKEN", raising=False)
    journal = tmp_path / "strike_desk.db"
    _write_journal(journal, [])
    code = main(
        [
            "--journal",
            str(journal),
            "--state-dir",
            str(tmp_path),
            "--dry-run-alert",
        ]
    )
    assert code in {0, 1}
    out = capsys.readouterr().out
    assert "telegram (dry run)" in out
    assert "Monitor only" in out


def _aligned_facts(journal: Path, tmp_path: Path) -> dict:
    facts = collect_host_facts(state_dir=tmp_path, journal=journal)
    facts["units"] = {
        "strike-desk": "active",
        "strike-desk-coa": "active",
        "openalgo": "active",
    }
    facts["strikeshield"] = {"set1": False, "set2open": False}
    facts["coa_heartbeat"] = {"age_s": 9}
    facts["heartbeat"] = {"age_s": 40}
    return facts


def test_public_ticks_newest_first_header_uses_latest_only(tmp_path):
    journal = tmp_path / "strike_desk.db"
    _write_journal(
        journal,
        [
            (
                "tick-broken",
                "2026-11-04T05:44:00+00:00",
                "2026-11-04",
                "NIFTY",
                "schedule",
                "decline",
                "internal-error",
                "Declined: the tick raised TypeError before assembling a decision.",
                None,
                None,
                90,
                "{}",
            ),
            (
                "tick-hold",
                "2026-11-04T06:28:00+00:00",
                "2026-11-04",
                "NIFTY",
                "schedule",
                "hold",
                "position-open",
                "Book already open.",
                "trending",
                0.7,
                80,
                "{}",
            ),
            (
                "tick-enter",
                "2026-11-04T06:13:00+00:00",
                "2026-11-04",
                "NIFTY",
                "schedule",
                "enter",
                "risk-cleared",
                "Entered: risk cleared.",
                "trending",
                0.7,
                85,
                "{}",
            ),
        ],
    )
    facts = _aligned_facts(journal, tmp_path)
    now = datetime(2026, 11, 4, 7, 0, tzinfo=UTC)
    report = build_report(facts, now=now)
    rows = public_tick_rows(report)
    analyzer = [row for row in rows if row["kind"] == "analyzer-tick"]
    assert [row["outcome"] for row in analyzer] == ["hold", "enter", "decline"]
    assert analyzer[0]["severity"] == "ok"
    assert analyzer[-1]["severity"] == "broken"
    last_time, last_severity = latest_tick_severity(rows)
    assert last_time == "11:58"
    assert last_severity == "ok"
    books = {row["sid"] for row in rows}
    assert books == {sid.sid for sid in WATCHED_SIDS}
    page = render_ticks_html(rows, collected_at_ist=report.collected_at_ist)
    assert "Time IST" in page
    assert "<span class=\"sev ok\">ok</span>" in page
    assert "internal-error" in page
    assert "CoA" not in page
    assert "Hands" not in page
    groups = group_tick_rows(rows)
    probe = next(group for group in groups if len(group["rows"]) > 1)
    assert probe["severity"] == "ok"
    assert probe["open"] is False
    assert 'tbody class="minute"' in page
    assert 'tbody class="minute open"' not in page
    assert page.count("tr class=\"summary\"") == 1


def test_probe_minute_auto_expands_when_fishy(tmp_path):
    journal = tmp_path / "strike_desk.db"
    _write_journal(journal, [])
    facts = _aligned_facts(journal, tmp_path)
    facts["coa_heartbeat"] = {"age_s": 900}
    now = datetime(2026, 11, 4, 7, 0, tzinfo=UTC)
    report = build_report(facts, now=now)
    rows = public_tick_rows(report)
    groups = group_tick_rows(rows)
    probe = next(group for group in groups if len(group["rows"]) > 1)
    assert probe["severity"] in {"fishy", "broken"}
    assert probe["open"] is True
    page = render_ticks_html(rows, collected_at_ist=report.collected_at_ist)
    assert 'tbody class="minute open"' in page


def test_write_ticks_cli_writes_jsonl_and_html(tmp_path, capsys):
    journal = tmp_path / "strike_desk.db"
    _write_journal(
        journal,
        [
            (
                "tick-hold",
                "2026-11-04T06:28:00+00:00",
                "2026-11-04",
                "NIFTY",
                "schedule",
                "hold",
                "position-open",
                "Book already open.",
                "trending",
                0.7,
                80,
                "{}",
            )
        ],
    )
    jsonl = tmp_path / "desk-health-ticks.jsonl"
    code = main(
        [
            "--journal",
            str(journal),
            "--state-dir",
            str(tmp_path),
            "--write-ticks",
            str(jsonl),
        ]
    )
    assert code in {0, 1}
    capsys.readouterr()
    lines = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line]
    analyzer = next(row for row in lines if row["book"] == "Analyzer NIFTY")
    assert analyzer["sid"] == 999431310
    assert analyzer["outcome"] == "hold"
    assert {row["book"] for row in lines} >= {"Analyzer NIFTY", "NIFTY", "BTC"}
    html_page = jsonl.with_suffix(".html").read_text(encoding="utf-8")
    assert "<table>" in html_page
    assert "999431310" in html_page
    facts = _aligned_facts(journal, tmp_path)
    written = write_ticks(build_report(facts, now=datetime(2026, 11, 4, 7, 0, tzinfo=UTC)), jsonl)
    assert Path(written["jsonl"]).is_file()
    assert Path(written["html"]).is_file()
