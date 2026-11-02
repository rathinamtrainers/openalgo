"""Chip-flip bot: adapter, ledger, posts, guards. No live Telegram."""

from __future__ import annotations

import datetime as dt
import html
import importlib
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")
FIXTURE = Path(__file__).parent / "fixtures" / "live_sample.json"
STRIKE_RE = re.compile(r"\b\d{5}\s?(CE|PE)\b", re.I)
FORBIDDEN = ("₹", "%")


def _load(monkeypatch, tmp_path, **extra):
    env = {
        "LIVE_URL": "http://x",
        "TELEGRAM_BOT_TOKEN": "x",
        "TELEGRAM_CHAT_ID": "x",
        "DRY_RUN": "true",
        "DATA_DIR": str(tmp_path),
        "COOLDOWN_SECONDS": "0",
        "INCLUDE_DESK_NOTE": "false",
        "BOARDS": "nifty",
        "HOLIDAYS": "2026-01-26",
        "MAX_TAGS_PER_SESSION": "6",
        "STALE_SECONDS": "60",
        "ALWAYS_OPEN_BOARDS": "",
        "BRIEF_DIR": "",
    }
    env.update(extra)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import zta_telegram_bot as bot

    return importlib.reload(bot)


def _snap(chip, last=22936.75, ts=None, note="desk sentence"):
    if ts is None:
        ts = dt.datetime(2026, 10, 29, 10, 0, tzinfo=IST)
    return {
        "nifty": {
            "chip": chip,
            "last": last,
            "updated_at": ts,
            "note": note,
        }
    }


def _capture(bot, monkeypatch):
    posts: list[str] = []

    def fake_send(text: str) -> bool:
        posts.append(text)
        return True

    monkeypatch.setattr(bot, "send", fake_send)
    return posts


def _body_without_disclaimer(bot, text: str) -> str:
    esc = html.escape(bot.DISCLAIMER)
    return text.replace(esc, "").replace(bot.DISCLAIMER, "")


def _assert_clean_posts(bot, posts: list[str]) -> None:
    for text in posts:
        assert bot.DISCLAIMER in text or html.escape(bot.DISCLAIMER) in text
        body = _body_without_disclaimer(bot, text).lower()
        assert "buy" not in body
        assert "sell" not in body
        for token in FORBIDDEN:
            assert token not in text
        assert STRIKE_RE.search(text) is None


def test_include_desk_note_stays_off(monkeypatch, tmp_path):
    bot = _load(monkeypatch, tmp_path)
    assert bot.INCLUDE_DESK_NOTE is False


def test_adapter_on_real_fixture(monkeypatch, tmp_path):
    bot = _load(monkeypatch, tmp_path)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    boards = bot.extract_boards(payload)
    nifty = boards["nifty"]
    assert nifty["chip"] == "WATCHING"
    assert isinstance(nifty["last"], float)
    assert nifty["last"] == 22936.75
    assert nifty["updated_at"] is not None
    assert nifty["updated_at"].tzinfo is not None
    assert nifty["updated_at"].utcoffset() == dt.timedelta(hours=5, minutes=30)


def test_adapter_uses_market_slug(monkeypatch, tmp_path):
    bot = _load(monkeypatch, tmp_path)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["market"] = "banknifty"
    payload["scenario_label"] = "CALL TAGGED"
    boards = bot.extract_boards(payload)
    assert "banknifty" in boards
    assert "nifty" not in boards
    assert boards["banknifty"]["chip"] == "CALL TAGGED"


def test_fetch_boards_from_brief_dir(monkeypatch, tmp_path):
    briefs = tmp_path / "briefs"
    briefs.mkdir()
    nifty = json.loads(FIXTURE.read_text(encoding="utf-8"))
    nifty["market"] = "nifty"
    bn = dict(nifty)
    bn["market"] = "banknifty"
    bn["scenario_label"] = "CALL TAGGED"
    bn["last"] = 54767.5
    (briefs / "public-brief.json").write_text(json.dumps(nifty), encoding="utf-8")
    (briefs / "public-brief-banknifty.json").write_text(json.dumps(bn), encoding="utf-8")
    bot = _load(
        monkeypatch,
        tmp_path,
        BOARDS="nifty,banknifty",
        BRIEF_DIR=str(briefs),
    )
    boards = bot.fetch_boards()
    assert boards["nifty"]["chip"] == "WATCHING"
    assert boards["banknifty"]["chip"] == "CALL TAGGED"
    assert boards["banknifty"]["last"] == 54767.5


def test_banknifty_first_sight_does_not_post(monkeypatch, tmp_path):
    bot = _load(monkeypatch, tmp_path, BOARDS="nifty,banknifty")
    posts = _capture(bot, monkeypatch)
    now = dt.datetime(2026, 10, 30, 12, 0, tzinfo=IST)
    state = bot.load_state()
    boards = {
        "nifty": {"chip": "WATCHING", "last": 1.0, "updated_at": now, "note": None},
        "banknifty": {"chip": "CALL TAGGED", "last": 2.0, "updated_at": now, "note": None},
    }
    bot.process(state, boards, now)
    assert state["chips"]["banknifty"] == "CALL TAGGED"
    assert posts == []
    assert bot.day_events(now.date()) == []


def test_first_reading_after_start_sets_baseline(monkeypatch, tmp_path):
    bot = _load(monkeypatch, tmp_path)
    posts = _capture(bot, monkeypatch)
    state = bot.load_state()
    now = dt.datetime(2026, 10, 29, 10, 0, tzinfo=IST)
    bot.process(state, _snap("WATCHING", ts=now), now)
    bot.save_state(state)
    assert state["chips"]["nifty"] == "WATCHING"
    assert posts == []
    assert list(bot.LEDGER_DIR.glob("*.jsonl")) == []


def test_watching_to_call_tagged_opens(monkeypatch, tmp_path):
    bot = _load(monkeypatch, tmp_path)
    posts = _capture(bot, monkeypatch)
    now = dt.datetime(2026, 10, 29, 10, 0, tzinfo=IST)
    state = {"chips": {"nifty": "WATCHING"}, "last_tag_at": {}, "tags_today": {}, "day": "2026-10-29"}
    bot.process(state, _snap("CALL TAGGED", ts=now), now)
    events = bot.day_events(now.date())
    assert [e["event"] for e in events] == ["TAG_OPENED"]
    assert len(posts) == 1
    assert "CALL TAGGED" in posts[0]
    _assert_clean_posts(bot, posts)


def test_call_tagged_to_watching_closes_without_map_state(monkeypatch, tmp_path):
    bot = _load(monkeypatch, tmp_path)
    posts = _capture(bot, monkeypatch)
    now = dt.datetime(2026, 10, 29, 11, 0, tzinfo=IST)
    state = {"chips": {"nifty": "CALL TAGGED"}, "last_tag_at": {}, "tags_today": {"nifty": 1}, "day": "2026-10-29"}
    bot.process(state, _snap("WATCHING", ts=now), now)
    events = bot.day_events(now.date())
    assert [e["event"] for e in events] == ["TAG_CLOSED"]
    assert len(posts) == 1
    _assert_clean_posts(bot, posts)


def test_put_tagged_to_call_tagged_closes_then_opens(monkeypatch, tmp_path):
    bot = _load(monkeypatch, tmp_path)
    posts = _capture(bot, monkeypatch)
    now = dt.datetime(2026, 10, 29, 11, 0, tzinfo=IST)
    state = {"chips": {"nifty": "PUT TAGGED"}, "last_tag_at": {}, "tags_today": {"nifty": 1}, "day": "2026-10-29"}
    bot.process(state, _snap("CALL TAGGED", ts=now), now)
    events = bot.day_events(now.date())
    assert [e["event"] for e in events] == ["TAG_CLOSED", "TAG_OPENED"]
    assert events[0]["from"] == "PUT TAGGED" and events[0]["to"] == "CALL TAGGED"
    assert events[1]["from"] == "PUT TAGGED" and events[1]["to"] == "CALL TAGGED"
    assert len(posts) == 2
    _assert_clean_posts(bot, posts)


def test_stale_timestamp_writes_nothing(monkeypatch, tmp_path):
    bot = _load(monkeypatch, tmp_path)
    posts = _capture(bot, monkeypatch)
    now = dt.datetime(2026, 10, 29, 11, 0, tzinfo=IST)
    stale = now - dt.timedelta(seconds=61)
    state = {"chips": {"nifty": "WATCHING"}, "last_tag_at": {}, "tags_today": {}, "day": "2026-10-29"}
    bot.process(state, _snap("CALL TAGGED", ts=stale), now)
    assert state["chips"]["nifty"] == "WATCHING"
    assert posts == []
    assert bot.day_events(now.date()) == []


def test_tag_after_last_alert_written_not_posted(monkeypatch, tmp_path):
    bot = _load(monkeypatch, tmp_path)
    posts = _capture(bot, monkeypatch)
    now = dt.datetime(2026, 10, 29, 15, 21, tzinfo=IST)
    state = {"chips": {"nifty": "WATCHING"}, "last_tag_at": {}, "tags_today": {}, "day": "2026-10-29"}
    bot.process(state, _snap("CALL TAGGED", ts=now), now)
    events = bot.day_events(now.date())
    assert [e["event"] for e in events] == ["TAG_OPENED"]
    assert posts == []


def test_max_tags_extra_written_not_posted(monkeypatch, tmp_path):
    bot = _load(monkeypatch, tmp_path)
    posts = _capture(bot, monkeypatch)
    now = dt.datetime(2026, 10, 29, 11, 0, tzinfo=IST)
    state = {
        "chips": {"nifty": "WATCHING"},
        "last_tag_at": {},
        "tags_today": {"nifty": 6},
        "day": "2026-10-29",
    }
    bot.process(state, _snap("CALL TAGGED", ts=now), now)
    events = bot.day_events(now.date())
    assert [e["event"] for e in events] == ["TAG_OPENED"]
    assert posts == []
    assert state["tags_today"]["nifty"] == 6


def test_recap_once_per_day(monkeypatch, tmp_path):
    bot = _load(monkeypatch, tmp_path)
    posts = _capture(bot, monkeypatch)
    now = dt.datetime(2026, 10, 29, 15, 36, tzinfo=IST)
    state = {
        "chips": {"nifty": "WATCHING"},
        "last_tag_at": {},
        "tags_today": {},
        "day": "2026-10-29",
        "recap_done": None,
    }
    bot.maybe_recap(state, now)
    assert len(posts) == 1
    assert "Desk recap" in posts[0]
    _assert_clean_posts(bot, posts)
    bot.maybe_recap(state, now)
    assert len(posts) == 1


def test_tamper_breaks_chain_at_seq(monkeypatch, tmp_path):
    bot = _load(monkeypatch, tmp_path)
    now = dt.datetime(2026, 10, 29, 10, 0, tzinfo=IST)
    state = {"chips": {"nifty": "WATCHING"}, "last_tag_at": {}, "tags_today": {}, "day": "2026-10-29"}
    _capture(bot, monkeypatch)
    bot.process(state, _snap("CALL TAGGED", ts=now), now)
    path = bot._ledger_path(now.date())
    lines = path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[0])
    seq = entry["seq"]
    entry["to"] = "TAMPERED"
    path.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
    ok, detail = bot.verify_ledger()
    assert ok is False
    assert str(seq) in detail


def test_weekend_and_holiday_skip(monkeypatch, tmp_path):
    bot = _load(monkeypatch, tmp_path)
    posts = _capture(bot, monkeypatch)
    saturday = dt.datetime(2026, 10, 31, 10, 0, tzinfo=IST)
    holiday = dt.datetime(2026, 1, 26, 10, 0, tzinfo=IST)
    assert bot.is_trading_day(saturday) is False
    assert bot.is_trading_day(holiday) is False
    state = {"chips": {}, "last_tag_at": {}, "tags_today": {}, "recap_done": None, "day": None}
    bot.maybe_recap(state, saturday.replace(hour=15, minute=36))
    bot.maybe_recap(state, holiday.replace(hour=15, minute=36))
    assert posts == []
    assert bot.day_events(saturday.date()) == []
    assert bot.day_events(holiday.date()) == []


def test_restart_same_chip_no_duplicate(monkeypatch, tmp_path):
    bot = _load(monkeypatch, tmp_path)
    posts = _capture(bot, monkeypatch)
    now = dt.datetime(2026, 10, 29, 10, 0, tzinfo=IST)
    state = {"chips": {"nifty": "WATCHING"}, "last_tag_at": {}, "tags_today": {}, "day": "2026-10-29"}
    bot.process(state, _snap("CALL TAGGED", ts=now), now)
    bot.save_state(state)
    assert len(posts) == 1
    bot = _load(monkeypatch, tmp_path)
    posts = _capture(bot, monkeypatch)
    state = bot.load_state()
    later = now + dt.timedelta(minutes=1)
    bot.process(state, _snap("CALL TAGGED", ts=later), later)
    assert posts == []
    assert [e["event"] for e in bot.day_events(now.date())] == ["TAG_OPENED"]
