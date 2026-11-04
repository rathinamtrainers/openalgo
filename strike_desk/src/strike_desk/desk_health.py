"""Read-only Strike Desk health monitor. Never places or cancels orders.

This is a monitor-agent surface. It classifies Analyzer journal ticks
(defect vs correct stand-aside), watches mapped Hands SIDs, and reports
session / last-Hands / last-fill age. It does not pulse Tradetron, toggle
Analyze, or patch the live desk.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

STAND_ASIDE_REASONS = frozenset(
    {
        "regime-weak-trend",
        "regime-not-tradeable",
        "regime-low-confidence",
        "specialist-unavailable",
    }
)
DEFECT_REASONS = frozenset({"internal-error", "regime-ungrounded"})
WARN_REASONS = frozenset(
    {
        "data-quality",
        "tick-timeout",
        "specialist-timeout",
        "approval-queue-stale",
    }
)
ENTER_REASON = "risk-cleared"
HOLD_REASON = "position-open"

LIVE_SYMBOLS_MARKERS = (
    "live_symbols",
    "takes 1 positional argument but 2 were given",
)

HANDS_NEEDLES = (
    "analyzer hands",
    "an_ce",
    "an_pe",
    "an_exit",
    "ltp_buy",
    "ltp_exit",
    "buy_wings",
    "sell_shorts",
    "exit_shorts",
    "exit_all",
    "coa analyzer",
    "hands posted",
)
ANALYZER_HANDS_NEEDLES = ("analyzer hands", "an_ce", "an_pe", "an_exit")
CRASH_NEEDLES = ("live_symbols", "TypeError")

DEFAULT_STATE_DIR = Path("/var/lib/strike-desk")
DEFAULT_JOURNAL = DEFAULT_STATE_DIR / "strike_desk.db"
DEFAULT_EXPORT = Path("/var/tmp/strike-desk-decisions.jsonl")
DEFAULT_STACK = Path("/var/tmp/strike-desk-stack.json")
GCE_INSTANCE = "strike-desk"
GCE_ZONE = "asia-south1-a"
GCE_PROJECT = "ztartcopenalgo"

# Analyzer cash window used by SID 999431310 (first trade after 09:30, flatten 15:15).
CASH_OPEN = dtime(9, 15)
CASH_CLOSE = dtime(15, 30)
ANALYZER_ENTRY_OPEN = dtime(9, 30)
ANALYZER_ENTRY_CLOSE = dtime(15, 10)

HEARTBEAT_STALE_S = 20 * 60  # two missed 15-min ticks
COA_HEARTBEAT_STALE_S = 10 * 60
HANDS_STALE_S = 30 * 60
BRIEF_STALE_S = 180
HOURLY_SECONDS = 3600
TICK_SECONDS = 15 * 60
# Confirm even when aligned. After this IST day, fishy/broken only.
STATUS_PING_DAY = "2026-11-04"
MCX_OPEN = dtime(9, 20)
MCX_CLOSE = dtime(23, 15)

SECRET_ENV_KEYS = (
    "TELEGRAM_CHAT_BOT_TOKEN",
    "DESK_HEALTH_TELEGRAM_TOKEN",
    "TELEGRAM_BOT_TOKEN",
)
CHAT_ENV_KEYS = ("DESK_HEALTH_CHAT_ID", "ARJUN_ADMIN_IDS", "OWNER_CHAT_ID")
TOKEN_FILE_CANDIDATES = (
    Path("/opt/zta-desk/config.env"),
    Path(os.environ.get("DESK_HEALTH_TELEGRAM_ENV") or ""),
    Path.home() / ".zta-desk.env",
)
SUBSCRIBER_DB_CANDIDATES = (
    Path("/var/lib/zta-desk/arjun_subscribers.db"),
    Path("/opt/zta-desk/arjun_subscribers.db"),
)


@dataclass(frozen=True)
class SidWatch:
    """One mapped (or explicitly unmapped) Hands / Live Auto SID."""

    book_id: str
    name: str
    sid: int
    template_id: int | None
    kind: str
    expected_legs: int
    flags: str
    notes: str
    mapped: bool = True


WATCHED_SIDS: tuple[SidWatch, ...] = (
    SidWatch(
        book_id="analyzer-nifty",
        name="Strike Desk NIFTY Analyzer",
        sid=999431310,
        template_id=999229670,
        kind="analyzer",
        expected_legs=1,
        flags="an_ce / an_pe / an_exit",
        notes="15-min mind. Live Offline. Seed books row has tt_sid=None; this is the live SID.",
    ),
    SidWatch(
        book_id="coa-nifty",
        name="Strike Desk CoA NIFTY Hands",
        sid=999357114,
        template_id=999183250,
        kind="coa",
        expected_legs=1,
        flags="ltp_buy_ce / ltp_buy_pe / ltp_exit",
        notes="CoA cash Live Offline. Never the Live Auto QuadShield row.",
    ),
    SidWatch(
        book_id="coa-nifty-live-auto",
        name="CoA cash Live Auto twin",
        sid=999578034,
        template_id=999183250,
        kind="coa",
        expected_legs=1,
        flags="ltp_buy_ce / ltp_buy_pe / ltp_exit",
        notes="Live Auto twin of the cash CoA Hands template. Watch only.",
    ),
    SidWatch(
        book_id="qs-nifty",
        name="Strike Desk QuadShield NIFTY Hands",
        sid=999328734,
        template_id=999159502,
        kind="strikeshield",
        expected_legs=4,
        flags="buy_wings / sell_shorts / exit_shorts / exit_all",
        notes="Books-map Hands SID. Public page may be stale; expect 4 legs when wings+shorts.",
    ),
    SidWatch(
        book_id="qs-nifty-env",
        name="QuadShield Hands (.env.example)",
        sid=999309190,
        template_id=999159502,
        kind="strikeshield",
        expected_legs=4,
        flags="buy_wings / sell_shorts / exit_shorts / exit_all",
        notes="SID printed in strike_desk/.env.example.",
    ),
    SidWatch(
        book_id="qs-live-auto",
        name="QuadShield Live Auto",
        sid=999611338,
        template_id=999159502,
        kind="strikeshield",
        expected_legs=4,
        flags="wings / shorts (TT engine)",
        notes="Live Auto adjustments / wings. Watch 4-leg vs 3-leg leftovers.",
    ),
    SidWatch(
        book_id="qs-unmapped",
        name="Unmapped 4-leg candidate",
        sid=999575526,
        template_id=None,
        kind="strikeshield",
        expected_legs=4,
        flags="unknown - not in tt_map / SEED_BOOKS",
        notes="Host showed 3 of 4. Not in the git SID map. Public TT page 404.",
        mapped=False,
    ),
    SidWatch(
        book_id="ofa-nifty",
        name="Strike Desk OFA NIFTY",
        sid=999405618,
        template_id=999214946,
        kind="ofa",
        expected_legs=1,
        flags="of_long / of_short / of_exit",
        notes="Depth mind. Dual-exec Analyze + Hands.",
    ),
    SidWatch(
        book_id="ema120-nifty",
        name="Strike Desk EMA120 NIFTY",
        sid=999308846,
        template_id=999159334,
        kind="overlay",
        expected_legs=1,
        flags="_el overlay",
        notes="LLM overlay fan-out. Does not buy until the mind accepts.",
    ),
    SidWatch(
        book_id="coa-mcx",
        name="CoA MCX Hands",
        sid=999581814,
        template_id=999310646,
        kind="coa",
        expected_legs=1,
        flags="ltp_buy_* (MCX token)",
        notes="Own token - never reuse NSE/BSE CoA.",
    ),
    SidWatch(
        book_id="coa-btc",
        name="CoA BTC Hands",
        sid=999567130,
        template_id=999305026,
        kind="coa",
        expected_legs=1,
        flags="ltp_buy_* (BTC token)",
        notes="Never reuse on ETH.",
    ),
    SidWatch(
        book_id="coa-eth",
        name="CoA ETH Hands (skill)",
        sid=999591354,
        template_id=999323662,
        kind="coa",
        expected_legs=1,
        flags="ltp_buy_* (ETH token)",
        notes="Skill SID. Active twin is 999591522.",
    ),
    SidWatch(
        book_id="coa-eth-active",
        name="CoA ETH Hands (active twin)",
        sid=999591522,
        template_id=999323662,
        kind="coa",
        expected_legs=1,
        flags="ltp_buy_* (ETH token)",
        notes="Active ETH twin. Never fold into BTC 999305026.",
    ),
)


@dataclass
class TickClass:
    disposition: str
    reason_code: str
    detail: str
    live_symbols_crash: bool = False


@dataclass
class HealthReport:
    collected_at_ist: str
    source: str
    source_path: str
    severity: str
    headline: str
    session: dict[str, Any]
    analyzer: dict[str, Any]
    ages: dict[str, Any]
    sids: list[dict[str, Any]]
    books: list[dict[str, Any]]
    quadshield: dict[str, Any]
    alerts: list[str]
    raw: dict[str, Any] = field(default_factory=dict)


def now_ist(moment: datetime | None = None) -> datetime:
    stamp = moment or datetime.now(tz=UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(IST)


def cash_session(moment: datetime | None = None) -> dict[str, Any]:
    """Heuristic cash session. Does not call OpenAlgo (monitor must run without keys)."""
    ist = now_ist(moment)
    weekday = ist.weekday()
    trading_day = weekday < 5
    in_hours = CASH_OPEN <= ist.time() <= CASH_CLOSE
    in_entry = ANALYZER_ENTRY_OPEN <= ist.time() < ANALYZER_ENTRY_CLOSE
    on = trading_day and in_hours
    return {
        "as_of_ist": ist.strftime("%Y-%m-%d %H:%M:%S IST"),
        "trading_day": ist.date().isoformat(),
        "weekday": ist.strftime("%A"),
        "cash_session_on": on,
        "analyzer_entry_window": trading_day and in_entry,
        "note": (
            "Heuristic Mon-Fri 09:15-15:30 IST. Holidays are not loaded here; "
            "a missing journal on a holiday is not a defect."
        ),
    }


def _haystack(*parts: Any) -> str:
    return " ".join(str(part or "") for part in parts).lower()


def is_live_symbols_crash(reason_code: str | None, *texts: Any) -> bool:
    """True only from THIS tick's text — not a 2-day journalctl blob."""
    blob = _haystack(reason_code, *texts)
    if "live_symbols" not in blob:
        return False
    return "typeerror" in blob or "positional argument" in blob


def classify_tick(
    reason_code: str | None,
    reason_text: str = "",
    extra: str = "",
) -> TickClass:
    code = str(reason_code or "").strip() or "unknown"
    if is_live_symbols_crash(code, reason_text, extra):
        return TickClass(
            disposition="defect",
            reason_code="live-symbols-crash",
            detail=(
                "Journal.live_symbols() arity crash. No regime, no an_ce/an_pe, not a stand-aside."
            ),
            live_symbols_crash=True,
        )
    if code == ENTER_REASON:
        return TickClass("enter", code, "Risk cleared - Analyze buy + Hands fan-out.")
    if code == HOLD_REASON:
        return TickClass("hold", code, "Book already open; tick manages, does not add.")
    if code in STAND_ASIDE_REASONS:
        return TickClass(
            "stand-aside",
            code,
            "Correct no-trade: the playbook refused the tape.",
        )
    if code in DEFECT_REASONS:
        return TickClass("defect", code, "Tick failed closed. Not a stand-aside.")
    if code in WARN_REASONS:
        return TickClass("warn", code, "Degraded tick - data, budget, or queue.")
    return TickClass("unknown", code, "Unmapped reason_code.")


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri()
    last_error: sqlite3.Error | None = None
    for suffix in ("?mode=ro", "?mode=ro&immutable=1"):
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(f"{uri}{suffix}", uri=True, timeout=5)
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
            return conn
        except sqlite3.Error as exc:
            last_error = exc
            if conn is not None:
                conn.close()
    raise last_error or sqlite3.OperationalError(f"cannot open {path}")


def read_decisions(journal: Path, *, limit: int = 400) -> list[dict[str, Any]]:
    conn = _connect_readonly(journal)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT tick_id, created_at_utc, trading_day, index_symbol, trigger, "
            "outcome, reason_code, substr(reason_text,1,240) AS reason_text, "
            "regime_label, regime_confidence, latency_ms FROM decisions "
            "ORDER BY created_at_utc DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]
    finally:
        conn.close()


def read_jsonl_decisions(path: Path, *, limit: int = 400) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    parsed: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    parsed.append(item)
    except OSError:
        return []
    parsed.sort(key=lambda row: str(row.get("created_at_utc") or ""), reverse=True)
    return parsed[:limit]


def _parse_stamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":
            text = f"{text[:-2]}:{text[-2:]}"
        try:
            stamp = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp


def _age_s(stamp: datetime | None, now: datetime | None = None) -> float | None:
    if stamp is None:
        return None
    current = now or datetime.now(tz=UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return max(0.0, (current.astimezone(UTC) - stamp.astimezone(UTC)).total_seconds())


def _file_meta(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        stat = path.stat()
        text = ""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            text = ""
        return {
            "path": str(path),
            "age_s": max(0.0, time.time() - stat.st_mtime),
            "mtime_utc": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            "text": text,
        }
    except OSError:
        return None


def _load_json_text(path: Path) -> dict[str, Any]:
    blob = path.read_bytes()
    if blob.startswith(b"\xff\xfe") or blob.startswith(b"\xfe\xff"):
        raw = blob.decode("utf-16")
    else:
        raw = blob.decode("utf-8")
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in {path}")
    payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required in {path}")
    return payload


def _load_json_file(path: Path) -> Any:
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _redact(value: Any) -> Any:
    banned = ("token", "secret", "password", "apikey", "api_key", "auth")
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if any(word in str(key).lower() for word in banned):
                out[key] = "<redacted>"
            else:
                out[key] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _systemctl(*units: str) -> dict[str, str]:
    if os.name == "nt":
        return {}
    out: dict[str, str] = {}
    for unit in units:
        try:
            proc = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            out[unit] = (proc.stdout or proc.stderr or "").strip() or "unknown"
        except (OSError, subprocess.TimeoutExpired):
            out[unit] = "unavailable"
    return out


def _journalctl_hits(*, since: str = "2 days ago", limit: int = 20) -> list[dict[str, str]]:
    if os.name == "nt":
        return []
    cmd = [
        "journalctl",
        "-u",
        "strike-desk.service",
        "-u",
        "strike-desk-coa.service",
        "--since",
        since,
        "--no-pager",
        "-o",
        "short-iso",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    crash_needles = tuple(item.lower() for item in CRASH_NEEDLES)
    analyzer_needles = ("analyzer hands", "an_ce", "an_pe", "an_exit")
    hands_needles = tuple(item.lower() for item in HANDS_NEEDLES)
    crash: list[dict[str, str]] = []
    analyzer: list[dict[str, str]] = []
    hands: list[dict[str, str]] = []
    for raw in (proc.stdout or "").splitlines():
        lower = raw.lower()
        row = {"line": raw[:400]}
        if any(needle in lower for needle in crash_needles):
            crash.append(row)
        elif any(needle in lower for needle in analyzer_needles):
            analyzer.append(row)
        elif any(needle in lower for needle in hands_needles):
            hands.append(row)
    return crash[-limit:] + analyzer[-limit:] + hands[-limit:]


def collect_host_facts(
    *,
    state_dir: Path | None = None,
    journal: Path | None = None,
) -> dict[str, Any]:
    """Read local host files. Safe on HPZBook (missing paths become empty)."""
    state = state_dir or Path(os.getenv("STRIKE_DESK_STATE_DIR") or DEFAULT_STATE_DIR)
    journal_path = journal or Path(os.getenv("STRIKE_DESK_JOURNAL") or state / "strike_desk.db")
    export = Path(os.getenv("STRIKE_DESK_DECISIONS_EXPORT") or DEFAULT_EXPORT)
    stack_path = Path(os.getenv("STRIKE_DESK_STACK_STATUS") or DEFAULT_STACK)

    decisions: list[dict[str, Any]] = []
    source = "missing"
    source_path = str(journal_path)
    if journal_path.is_file():
        try:
            decisions = read_decisions(journal_path)
            source = "journal"
        except sqlite3.Error:
            decisions = []
    if not decisions and export.is_file():
        decisions = read_jsonl_decisions(export)
        if decisions:
            source = "export"
            source_path = str(export)

    shield = _redact(_load_json_file(state / "strikeshield.json"))
    coa_live = _redact(_load_json_file(state / "coa_live.json"))
    events = _redact(_load_json_file(state / "events.json"))
    stack = _redact(_load_json_file(stack_path))
    briefs: dict[str, Any] = {}
    for name in (
        "public-brief.json",
        "public-brief-banknifty.json",
        "public-brief-sensex.json",
        "public-brief-crudeoil.json",
        "public-brief-natgas.json",
        "public-brief-btc-options.json",
        "public-brief-eth-options.json",
    ):
        meta = _file_meta(state / name)
        payload = _redact(_load_json_file(state / name))
        if meta is not None:
            if isinstance(payload, dict):
                meta["as_of_ist"] = payload.get("as_of_ist") or payload.get("computed_at")
            briefs[name] = meta

    return {
        "collected_at_utc": datetime.now(tz=UTC).isoformat(),
        "source": source,
        "source_path": source_path,
        "state_dir": str(state),
        "units": _systemctl("strike-desk", "strike-desk-coa", "openalgo", "strike-desk-ofa"),
        "heartbeat": _file_meta(state / "heartbeat"),
        "coa_heartbeat": _file_meta(state / "coa.heartbeat"),
        "strikeshield_heartbeat": _file_meta(state / "strikeshield.heartbeat"),
        "ofa_heartbeat": _file_meta(state / "ofa.heartbeat"),
        "kill_switch": _file_meta(state / "KILL"),
        "strikeshield": shield,
        "coa_live": coa_live,
        "ofa": _redact(_load_json_file(state / "ofa.json")),
        "events": events,
        "stack": stack,
        "briefs": briefs,
        "decisions": decisions,
        "log_hits": _journalctl_hits(),
    }


def _log_blob(facts: dict[str, Any]) -> str:
    return " ".join(
        str(hit.get("line") or "") for hit in (facts.get("log_hits") or []) if isinstance(hit, dict)
    )


def _classify_day(facts: dict[str, Any], day: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in facts.get("decisions") or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("trading_day") or "") != day:
            continue
        klass = classify_tick(
            str(raw.get("reason_code") or ""),
            str(raw.get("reason_text") or ""),
        )
        item = dict(raw)
        item["disposition"] = klass.disposition
        item["classified_reason"] = klass.reason_code
        item["live_symbols_crash"] = klass.live_symbols_crash
        item["class_detail"] = klass.detail
        rows.append(item)
    return rows


def _hands_age_s(
    facts: dict[str, Any],
    now: datetime,
    *,
    needles: tuple[str, ...] = HANDS_NEEDLES,
    empty: str = "no Hands pulse in journalctl/events",
) -> tuple[float | None, str]:
    candidates: list[tuple[float, str]] = []
    for hit in facts.get("log_hits") or []:
        line = str(hit.get("line") or "")
        lower = line.lower()
        if not any(needle in lower for needle in needles):
            continue
        # journalctl short-iso: 2026-11-03T09:45:01+05:30 host ...
        token = line.split(" ", 1)[0]
        stamp = _parse_stamp(token)
        age = _age_s(stamp, now)
        if age is not None:
            candidates.append((age, line[:160]))
    events = facts.get("events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            blob = _haystack(event)
            if not any(needle in blob for needle in needles):
                continue
            stamp = _parse_stamp(event.get("created_at_utc") or event.get("ts") or event.get("at"))
            age = _age_s(stamp, now)
            if age is not None:
                candidates.append((age, str(event.get("title") or event.get("kind") or "event")))
    if not candidates:
        return None, empty
    age, detail = min(candidates, key=lambda item: item[0])
    return age, detail


def _fill_age_s(
    day_rows: list[dict[str, Any]],
    facts: dict[str, Any],
    now: datetime,
) -> tuple[float | None, str]:
    for row in day_rows:
        if row.get("disposition") == "enter" or row.get("reason_code") == ENTER_REASON:
            stamp = _parse_stamp(row.get("created_at_utc"))
            return _age_s(stamp, now), f"last enter {row.get('reason_code')}"
    for row in facts.get("decisions") or []:
        if not isinstance(row, dict):
            continue
        if row.get("reason_code") == ENTER_REASON or row.get("outcome") == "enter":
            stamp = _parse_stamp(row.get("created_at_utc"))
            day = row.get("trading_day")
            return _age_s(stamp, now), f"last enter on {day}"
    live = facts.get("coa_live")
    if isinstance(live, dict):
        opened = live.get("open")
        if isinstance(opened, dict):
            stamp = _parse_stamp(opened.get("in_at") or opened.get("opened_at"))
            age = _age_s(stamp, now)
            if age is not None:
                return age, "coa_live open"
        stamp = _parse_stamp(live.get("last_exit"))
        age = _age_s(stamp, now)
        if age is not None:
            return age, "coa_live last_exit"
    return None, "no Analyze/Hands fill in journal"


def _quadshield(facts: dict[str, Any]) -> dict[str, Any]:
    shield = facts.get("strikeshield")
    stack = facts.get("stack") if isinstance(facts.get("stack"), dict) else {}
    set1 = False
    set2 = False
    if isinstance(shield, dict):
        set1 = bool(shield.get("set1"))
        set2 = bool(shield.get("set2open") or shield.get("set2"))
    elif isinstance(stack, dict):
        set1 = bool(stack.get("set1"))
        set2 = bool(stack.get("set2open"))

    if set1 and set2:
        mind, expected = "wings + shorts", 4
    elif set1:
        mind, expected = "wings open", 2
    elif set2:
        mind, expected = "shorts open (wings missing)", 2
    else:
        mind, expected = "flat", 0

    observed = stack.get("open_legs") if isinstance(stack, dict) else None
    if observed is None and isinstance(shield, dict):
        observed = shield.get("open_legs") or shield.get("leg_count")

    flag = None
    try:
        observed_n = int(observed) if observed is not None else None
    except (TypeError, ValueError):
        observed_n = None
    if observed_n == 3:
        flag = "3-of-4 leftover - TT/broker skip, not a desk pair-exit"
    elif set1 and set2 and observed_n is not None and observed_n != 4:
        flag = f"mind says wings+shorts but observed {observed_n} legs"
    elif set2 and not set1:
        flag = "shorts latched without wings - unexpected"

    return {
        "mind": mind,
        "expected_legs": expected,
        "observed_legs": observed_n,
        "set1": set1,
        "set2open": set2,
        "flag": flag,
        "sids": [sid.sid for sid in WATCHED_SIDS if sid.kind == "strikeshield"],
    }


def _book_profile(sid: SidWatch) -> dict[str, Any]:
    if sid.book_id in {"coa-mcx"}:
        session, unit, beat, needles = (
            "mcx",
            "strike-desk-coa",
            "coa",
            ("ltp_buy", "ltp_exit", "coa analyzer"),
        )
        brief = "public-brief-crudeoil.json"
    elif sid.book_id in {"coa-btc"}:
        session, unit, beat, needles = (
            "crypto",
            "strike-desk-coa",
            "coa",
            ("ltp_buy", "ltp_exit"),
        )
        brief = "public-brief-btc-options.json"
    elif sid.book_id.startswith("coa-eth"):
        session, unit, beat, needles = (
            "crypto",
            "strike-desk-coa",
            "coa",
            ("ltp_buy", "ltp_exit"),
        )
        brief = "public-brief-eth-options.json"
    elif sid.kind == "coa":
        session, unit, beat, needles = (
            "cash",
            "strike-desk-coa",
            "coa",
            ("ltp_buy", "ltp_exit", "coa analyzer"),
        )
        brief = "public-brief.json"
    elif sid.kind == "strikeshield":
        session, unit, beat, needles = (
            "cash",
            "strike-desk",
            "shield",
            ("buy_wings", "sell_shorts", "exit_shorts", "exit_all"),
        )
        brief = None
    elif sid.kind == "ofa":
        session, unit, beat, needles = (
            "cash",
            "strike-desk-ofa",
            "ofa",
            ("of_long", "of_short", "of_exit"),
        )
        brief = None
    elif sid.kind == "overlay":
        session, unit, beat, needles = ("cash", "strike-desk", "desk", ("_el",))
        brief = None
    else:
        session, unit, beat, needles = (
            "cash",
            "strike-desk",
            "desk",
            ANALYZER_HANDS_NEEDLES,
        )
        brief = None
    return {
        "session": session,
        "unit": unit,
        "heartbeat": beat,
        "hands_needles": needles,
        "brief_file": brief,
    }


def _session_on_for(kind: str, now: datetime) -> bool:
    ist = now_ist(now)
    if kind == "crypto":
        return True
    if kind == "mcx":
        return ist.weekday() < 6 and MCX_OPEN <= ist.time() <= MCX_CLOSE
    return bool(cash_session(now)["cash_session_on"])


def _heartbeat_age(facts: dict[str, Any], which: str) -> float | None:
    key = {
        "desk": "heartbeat",
        "coa": "coa_heartbeat",
        "shield": "strikeshield_heartbeat",
        "ofa": "ofa_heartbeat",
    }.get(which)
    if not key:
        return None
    meta = facts.get(key)
    if isinstance(meta, dict):
        try:
            return float(meta.get("age_s"))
        except (TypeError, ValueError):
            return None
    return None


def align_books(
    facts: dict[str, Any],
    *,
    now: datetime,
    qs: dict[str, Any],
    analyzer: dict[str, Any],
) -> list[dict[str, Any]]:
    """One alignment row per catalogued SID. Never places orders."""
    units = facts.get("units") or {}
    stack = facts.get("stack") if isinstance(facts.get("stack"), dict) else {}
    rows: list[dict[str, Any]] = []
    for sid in WATCHED_SIDS:
        profile = _book_profile(sid)
        session_on = _session_on_for(profile["session"], now)
        unit_state = str(units.get(profile["unit"]) or "")
        beat_age = _heartbeat_age(facts, profile["heartbeat"])
        hands_age, hands_detail = _hands_age_s(
            facts,
            now,
            needles=profile["hands_needles"],
            empty=f"no {sid.book_id} Hands pulse",
        )
        brief_meta = (facts.get("briefs") or {}).get(profile["brief_file"] or "")
        brief_age = brief_meta.get("age_s") if isinstance(brief_meta, dict) else None
        issues: list[str] = []
        alignment = "ok"

        if unit_state and unit_state != "active" and session_on:
            if profile["unit"] == "strike-desk-ofa" and not stack.get("ofa_enabled"):
                pass
            else:
                issues.append(f"unit {profile['unit']}={unit_state or 'missing'}")
                alignment = "broken"

        if sid.kind == "analyzer":
            last_disp = analyzer.get("last_disposition")
            last_reason = analyzer.get("last_reason_code") or "unknown"
            if analyzer.get("last_live_symbols_crash"):
                issues.append("latest tick live_symbols crash")
                alignment = "broken"
            elif last_disp == "defect":
                issues.append(f"latest tick {last_reason} / defect")
                alignment = "broken"
            elif last_disp in {"enter", "hold"}:
                issues.append(f"latest tick {last_reason} ({last_disp})")
            elif last_disp == "stand-aside":
                issues.append(f"latest tick stand-aside {last_reason}")
            earlier = int(analyzer.get("earlier_defects") or 0)
            if earlier and alignment == "ok":
                issues.append(f"{earlier} earlier defect(s) today; latest recovered")
            if session_on and isinstance(beat_age, (int, float)) and beat_age > HEARTBEAT_STALE_S:
                issues.append(f"desk heartbeat stale {int(beat_age)}s")
                alignment = "broken" if alignment == "broken" else "fishy"
            if last_disp == "enter" and hands_age is None:
                issues.append("risk-cleared but no Analyzer Hands")
                alignment = "broken" if alignment == "broken" else "fishy"

        elif sid.kind == "strikeshield":
            if qs.get("flag"):
                issues.append(str(qs["flag"]))
                alignment = "broken"
            shield_on = bool(stack.get("strikeshield_enabled")) or qs.get("mind") != "flat"
            stale_lim = 90 if shield_on else HEARTBEAT_STALE_S
            if (
                session_on
                and shield_on
                and isinstance(beat_age, (int, float))
                and beat_age > stale_lim
            ):
                issues.append(f"shield heartbeat stale {int(beat_age)}s")
                alignment = "broken" if alignment == "broken" else "fishy"
            if not sid.mapped and alignment == "ok":
                issues.append("unmapped catalog - no TT read")

        elif sid.kind == "coa":
            stale = COA_HEARTBEAT_STALE_S if profile["session"] != "crypto" else BRIEF_STALE_S
            if session_on and isinstance(beat_age, (int, float)) and beat_age > stale:
                issues.append(f"CoA heartbeat stale {int(beat_age)}s")
                alignment = "broken" if alignment == "broken" else "fishy"
            if session_on and isinstance(brief_age, (int, float)) and brief_age > BRIEF_STALE_S:
                issues.append(f"brief stale {int(brief_age)}s")
                alignment = "broken" if alignment == "broken" else "fishy"

        elif sid.kind == "ofa":
            enabled = bool(stack.get("ofa_enabled"))
            if enabled and session_on and isinstance(beat_age, (int, float)) and beat_age > 90:
                issues.append(f"OFA heartbeat stale {int(beat_age)}s")
                alignment = "broken" if alignment == "broken" else "fishy"
            elif not enabled:
                issues.append("OFA off")

        elif sid.kind == "overlay":
            if session_on and isinstance(beat_age, (int, float)) and beat_age > HEARTBEAT_STALE_S:
                issues.append(f"desk heartbeat stale {int(beat_age)}s")
                alignment = "fishy"

        if session_on and alignment == "ok" and not issues:
            issues.append("aligned")

        rows.append(
            {
                "book_id": sid.book_id,
                "name": sid.name,
                "sid": sid.sid,
                "kind": sid.kind,
                "mapped": sid.mapped,
                "expected_legs": sid.expected_legs,
                "session": profile["session"],
                "session_on": session_on,
                "unit": profile["unit"],
                "unit_state": unit_state or "n/a",
                "alignment": alignment,
                "issues": issues,
                "heartbeat_s": beat_age,
                "hands_s": hands_age,
                "hands_detail": hands_detail,
                "brief_s": brief_age,
            }
        )
    return rows


def _status_ping_today(report: HealthReport) -> bool:
    return str((report.analyzer or {}).get("day") or "") == STATUS_PING_DAY


def should_alert(report: HealthReport) -> bool:
    if report.severity == "critical":
        return True
    if any(book.get("alignment") in {"fishy", "broken"} for book in report.books):
        return True
    return _status_ping_today(report)


def _latest_tick(day_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Newest Analyzer tick by created_at_utc. History stays in day_rows."""
    dated: list[tuple[datetime, dict[str, Any]]] = []
    for row in day_rows:
        stamp = _parse_stamp(row.get("created_at_utc"))
        if stamp is not None:
            dated.append((stamp, row))
    if dated:
        dated.sort(key=lambda item: item[0])
        return dated[-1][1]
    return day_rows[0] if day_rows else None


def _tick_summaries(day_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every Analyzer tick for the trading day, oldest first."""
    dated: list[tuple[datetime, dict[str, Any]]] = []
    undated: list[dict[str, Any]] = []
    for row in day_rows:
        stamp = _parse_stamp(row.get("created_at_utc"))
        if stamp is None:
            undated.append(row)
        else:
            dated.append((stamp, row))
    dated.sort(key=lambda item: item[0])
    out: list[dict[str, Any]] = []
    for stamp, row in dated:
        out.append(
            {
                "at": row.get("created_at_utc"),
                "at_ist": now_ist(stamp).strftime("%H:%M IST"),
                "outcome": row.get("outcome"),
                "reason_code": row.get("reason_code"),
                "disposition": row.get("disposition"),
                "live_symbols_crash": bool(row.get("live_symbols_crash")),
            }
        )
    for row in undated:
        out.append(
            {
                "at": row.get("created_at_utc"),
                "at_ist": None,
                "outcome": row.get("outcome"),
                "reason_code": row.get("reason_code"),
                "disposition": row.get("disposition"),
                "live_symbols_crash": bool(row.get("live_symbols_crash")),
            }
        )
    return out


def render_alert(report: HealthReport) -> str:
    """Compact private Telegram text. Never an order."""
    broken = [book for book in report.books if book.get("alignment") == "broken"]
    fishy = [book for book in report.books if book.get("alignment") == "fishy"]
    ok_n = sum(1 for book in report.books if book.get("alignment") == "ok")
    ticks = (report.analyzer or {}).get("ticks_today") or []
    lines = [
        f"Desk health {report.collected_at_ist} [{report.severity}]",
        report.headline,
        f"books: {len(broken)} broken / {len(fishy)} fishy / {ok_n} ok",
    ]
    if _status_ping_today(report) and not broken and not fishy:
        lines.append("status: confirming (today-only ping even if aligned)")
    for book in (*broken, *fishy):
        detail = "; ".join(book.get("issues") or []) or book.get("alignment")
        lines.append(f"- {book['sid']} {book['book_id']}: {book['alignment']} ({detail})")
    lines.append(f"Analyzer ticks today ({len(ticks)}):")
    if ticks:
        for tick in ticks:
            when = tick.get("at_ist") or tick.get("at") or "?"
            flag = " crash" if tick.get("live_symbols_crash") else ""
            lines.append(
                f"  {when} {tick.get('outcome')} {tick.get('reason_code')} "
                f"{tick.get('disposition')}{flag}"
            )
    else:
        lines.append("  none yet")
    if report.alerts:
        lines.append("alerts: " + " | ".join(report.alerts[:6]))
    lines.append("Monitor only. No orders.")
    return "\n".join(lines)


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        if not path.is_file():
            return out
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip("'").strip('"')
    except OSError:
        return {}
    return out


def _private_chat_ids(raw: str | None) -> list[str]:
    """Keep private user chats. Skip public channels (-100...). Never log the ids."""
    out: list[str] = []
    text = str(raw or "").strip()
    for part in text.replace(";", ",").split(","):
        item = part.strip()
        if not item or not item.lstrip("-").isdigit():
            continue
        if item.startswith("-100"):
            continue
        if item not in out:
            out.append(item)
    return out


def _first_user_chat(raw: str | None) -> str:
    chats = _private_chat_ids(raw)
    return chats[0] if chats else ""


def _subscriber_db_candidates() -> list[Path]:
    extra = os.environ.get("DESK_HEALTH_SUBSCRIBER_DB") or ""
    data_dir = os.environ.get("DATA_DIR") or ""
    out: list[Path] = []
    if extra:
        out.append(Path(extra))
    if data_dir:
        out.append(Path(data_dir) / "arjun_subscribers.db")
    out.extend(path for path in SUBSCRIBER_DB_CANDIDATES if str(path))
    return out


def _subscriber_chats() -> list[str]:
    """Active private chats from arjun_subscribers.db. Ids stay on this host."""
    seen: list[str] = []
    for path in _subscriber_db_candidates():
        if not path.is_file():
            continue
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            try:
                rows = conn.execute(
                    "SELECT telegram_id FROM arjun_subscribers "
                    "WHERE status = 'active' AND telegram_id > 0"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            continue
        for (tid,) in rows:
            item = str(int(tid))
            if item not in seen:
                seen.append(item)
        if seen:
            return seen
    return seen


def resolve_telegram_secrets() -> dict[str, Any]:
    """Find ZTA_Arjun_Bot token + private chat(s). Never print the token or ids."""
    env = {key: os.environ.get(key, "") for key in (*SECRET_ENV_KEYS, *CHAT_ENV_KEYS)}
    source = "env"
    if not any(env.get(key) for key in SECRET_ENV_KEYS):
        for path in TOKEN_FILE_CANDIDATES:
            if not str(path) or not path.is_file():
                continue
            loaded = _parse_env_file(path)
            env.update({key: loaded.get(key, env.get(key, "")) for key in env})
            source = str(path)
            if any(env.get(key) for key in SECRET_ENV_KEYS):
                break
    token = (
        env.get("TELEGRAM_CHAT_BOT_TOKEN")
        or env.get("DESK_HEALTH_TELEGRAM_TOKEN")
        or env.get("TELEGRAM_BOT_TOKEN")
        or ""
    ).strip()
    chats = (
        _private_chat_ids(os.environ.get("DESK_HEALTH_CHAT_ID"))
        or _private_chat_ids(env.get("DESK_HEALTH_CHAT_ID"))
        or _private_chat_ids(env.get("ARJUN_ADMIN_IDS"))
        or _private_chat_ids(env.get("OWNER_CHAT_ID"))
    )
    chat_source = "env"
    if not chats:
        chats = _subscriber_chats()
        chat_source = "arjun_subscribers" if chats else "missing"
    return {
        "token": token,
        "chat_id": chats[0] if chats else "",
        "chat_ids": chats,
        "source": source if token else "missing",
        "chat_source": chat_source,
        "ok": "yes" if token and chats else "no",
    }


def send_telegram(text: str, *, token: str, chat_id: str) -> dict[str, Any]:
    """Private sendMessage. Token is only used in the request, never logged."""
    body = json.dumps(
        {
            "chat_id": chat_id,
            "text": text[:3500],
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")[:200]
        return {"ok": False, "error": f"HTTP {exc.code}", "body": err}
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": type(exc).__name__}
    return {
        "ok": bool(payload.get("ok")),
        "message_id": (payload.get("result") or {}).get("message_id"),
    }


def send_telegram_many(text: str, *, token: str, chat_ids: list[str]) -> dict[str, Any]:
    """Fan out one alert to private chats. Never returns the chat ids."""
    sent = 0
    failed = 0
    last_id = None
    last_err = None
    for chat_id in chat_ids:
        result = send_telegram(text, token=token, chat_id=chat_id)
        if result.get("ok"):
            sent += 1
            last_id = result.get("message_id")
        else:
            failed += 1
            last_err = result.get("error") or result.get("body")
    return {
        "ok": sent > 0,
        "message_id": last_id,
        "sent": sent,
        "failed": failed,
        "chat_count": len(chat_ids),
        "error": None if sent else last_err,
    }


def telegram_status_payload(secrets: dict[str, Any] | None = None) -> dict[str, Any]:
    """Public status only. No token, no chat ids."""
    resolved = secrets or resolve_telegram_secrets()
    return {
        "ok": resolved["ok"],
        "source": resolved["source"],
        "chat_configured": bool(resolved.get("chat_id")),
        "chat_count": len(resolved.get("chat_ids") or []),
        "chat_source": resolved.get("chat_source") or "missing",
        "token_configured": bool(resolved.get("token")),
    }


def deliver_alert(text: str, *, prefer_gce: bool = False) -> dict[str, Any]:
    """Send via local Arjun token, or GCE /opt/zta-desk/config.env. No orders."""
    secrets = resolve_telegram_secrets()
    if secrets["ok"] == "yes" and not prefer_gce:
        result = send_telegram_many(text, token=secrets["token"], chat_ids=secrets["chat_ids"])
        result["via"] = secrets["source"]
        result["chat_source"] = secrets["chat_source"]
        return result
    if os.name == "nt" or prefer_gce:
        return _deliver_alert_via_gce(text)
    if secrets["ok"] == "yes":
        result = send_telegram_many(text, token=secrets["token"], chat_ids=secrets["chat_ids"])
        result["via"] = secrets["source"]
        result["chat_source"] = secrets["chat_source"]
        return result
    return {"ok": False, "error": "no Telegram token/chat (ZTA_Arjun_Bot)", "via": "missing"}


def _gce_scp(local: Path, remote: str) -> None:
    subprocess.run(
        [
            _gcloud_bin(),
            "compute",
            "scp",
            str(local),
            f"{GCE_INSTANCE}:{remote}",
            f"--zone={GCE_ZONE}",
            f"--project={GCE_PROJECT}",
            "--quiet",
        ],
        check=True,
        timeout=90,
    )


def _gce_ssh(command: str, *, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _gcloud_bin(),
            "compute",
            "ssh",
            GCE_INSTANCE,
            f"--zone={GCE_ZONE}",
            f"--project={GCE_PROJECT}",
            "--quiet",
            "--command",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _parse_remote_json(raw: str, *, fallback: str) -> dict[str, Any]:
    text = (raw or "").strip()
    try:
        return json.loads(text[text.find("{") : text.rfind("}") + 1])
    except (json.JSONDecodeError, ValueError):
        return {"ok": False, "error": text[:240] or fallback}


def _gce_telegram_status() -> dict[str, Any]:
    """Status only. Runs as zta-desk so config.env is readable. No secrets returned."""
    remote_py = "/tmp/desk_health_probe.py"
    _gce_scp(Path(__file__).resolve(), remote_py)
    cmd = (
        f"chmod 644 {remote_py} && "
        "sudo -u zta-desk bash -lc "
        f"'set -a && . /opt/zta-desk/config.env && set +a && "
        f"python3 {remote_py} --telegram-status'"
    )
    proc = _gce_ssh(cmd)
    payload = _parse_remote_json(
        (proc.stdout or "").strip() or (proc.stderr or "").strip(),
        fallback=f"exit {proc.returncode}",
    )
    payload["via"] = "gce:/opt/zta-desk/config.env"
    return payload


def _deliver_alert_via_gce(text: str) -> dict[str, Any]:
    """Send on GCE using /opt/zta-desk/config.env. Token never copied back."""
    remote_alert = "/tmp/desk_health_alert.txt"
    remote_py = "/tmp/desk_health_probe.py"
    local = Path(os.environ.get("TEMP") or "/tmp") / "desk_health_alert.txt"
    local.write_text(text, encoding="utf-8")
    _gce_scp(Path(__file__).resolve(), remote_py)
    _gce_scp(local, remote_alert)
    cmd = (
        f"chmod 644 {remote_py} {remote_alert} && "
        "sudo -u zta-desk bash -lc "
        f"'set -a && . /opt/zta-desk/config.env && set +a && "
        f"python3 {remote_py} --send-alert --alert-file {remote_alert}'"
    )
    proc = _gce_ssh(cmd)
    payload = _parse_remote_json(
        (proc.stdout or "").strip() or (proc.stderr or "").strip(),
        fallback=f"exit {proc.returncode}",
    )
    payload["via"] = "gce:/opt/zta-desk/config.env"
    return payload


def build_report(facts: dict[str, Any], *, now: datetime | None = None) -> HealthReport:
    current = now or datetime.now(tz=UTC)
    session = cash_session(current)
    day = session["trading_day"]
    day_rows = _classify_day(facts, day)

    reasons = Counter(str(row.get("reason_code") or "unknown") for row in day_rows)
    dispositions = Counter(str(row.get("disposition") or "unknown") for row in day_rows)
    live_hits = sum(1 for row in day_rows if row.get("live_symbols_crash"))

    last = _latest_tick(day_rows)
    last_stamp = _parse_stamp(last.get("created_at_utc")) if last else None
    last_tick_age = _age_s(last_stamp, current)
    last_disp = last.get("disposition") if last else None
    last_reason = last.get("reason_code") if last else None
    last_crash = bool(last.get("live_symbols_crash")) if last else False
    earlier_defects = max(
        0,
        int(dispositions.get("defect") or 0) - (1 if last_disp == "defect" else 0),
    )

    hb = facts.get("heartbeat") or {}
    coa_hb = facts.get("coa_heartbeat") or {}
    desk_age = hb.get("age_s") if isinstance(hb, dict) else None
    coa_age = coa_hb.get("age_s") if isinstance(coa_hb, dict) else None
    hands_age, hands_detail = _hands_age_s(facts, current)
    analyzer_hands_age, analyzer_hands_detail = _hands_age_s(
        facts,
        current,
        needles=ANALYZER_HANDS_NEEDLES,
        empty="no Analyzer Hands (an_ce/an_pe/an_exit) today",
    )
    fill_age, fill_detail = _fill_age_s(day_rows, facts, current)
    units = facts.get("units") or {}
    killed = bool(facts.get("kill_switch"))
    session_on = bool(session["cash_session_on"]) and not killed

    alerts: list[str] = []
    if killed:
        alerts.append("Kill switch is engaged.")
    if last_crash:
        alerts.append("SID 999431310 latest tick is a live_symbols crash - not a stand-aside.")
    elif last_disp == "defect":
        alerts.append(f"SID 999431310 latest tick is {last_reason} / defect.")
    if session_on and not day_rows and units.get("strike-desk") == "active":
        alerts.append("Cash session on and desk unit active, but no Analyzer ticks today.")
    if session_on and isinstance(desk_age, (int, float)) and desk_age > HEARTBEAT_STALE_S:
        alerts.append(f"strike-desk heartbeat stale ({int(desk_age)}s).")
    if session_on and isinstance(coa_age, (int, float)) and coa_age > COA_HEARTBEAT_STALE_S:
        alerts.append(f"strike-desk-coa heartbeat stale ({int(coa_age)}s).")
    if session_on and analyzer_hands_age is None and last_disp == "enter":
        alerts.append("No Analyzer Hands pulse after a risk-cleared enter.")
    qs = _quadshield(facts)
    if qs.get("flag"):
        alerts.append(f"QuadShield: {qs['flag']}")
    for name, state in units.items():
        if state and state != "active":
            alerts.append(f"unit {name} is {state}")

    analyzer_block = {
        "sid": 999431310,
        "day": day,
        "ticks": len(day_rows),
        "reasons": dict(reasons),
        "dispositions": dict(dispositions),
        "live_symbols_crashes": live_hits,
        "last_disposition": last_disp,
        "last_reason_code": last_reason,
        "last_live_symbols_crash": last_crash,
        "earlier_defects": earlier_defects,
    }
    books = align_books(
        facts,
        now=current,
        qs=qs,
        analyzer=analyzer_block,
    )
    if any(book.get("alignment") == "broken" for book in books):
        for book in books:
            if book.get("alignment") == "broken":
                alerts.append(f"{book['book_id']}: " + "; ".join(book.get("issues") or ["broken"]))

    broken_books = any(book.get("alignment") == "broken" for book in books)
    fishy_books = any(book.get("alignment") == "fishy" for book in books)
    latest_broken = last_crash or last_disp == "defect"
    if latest_broken or broken_books:
        severity = "critical"
    elif alerts or fishy_books:
        severity = "degraded"
    elif day_rows or not session_on:
        severity = "ok"
    else:
        severity = "unknown"

    if last_crash:
        headline = (
            "NIFTY Analyzer SID 999431310 latest tick is a live_symbols crash. "
            "Not a correct stand-aside."
        )
    elif last_disp == "defect":
        headline = (
            f"NIFTY Analyzer SID 999431310 latest tick is {last_reason}. Not a correct stand-aside."
        )
    elif last_disp == "hold":
        headline = f"NIFTY Analyzer SID 999431310 latest tick is holding ({last_reason})."
    elif last_disp == "enter":
        headline = f"NIFTY Analyzer SID 999431310 latest tick entered ({last_reason})."
    elif last_disp == "stand-aside":
        headline = (
            f"NIFTY Analyzer SID 999431310 latest tick stood aside correctly ({last_reason})."
        )
    elif last:
        headline = f"NIFTY Analyzer SID 999431310 latest tick: {last_reason}."
    elif not session_on:
        headline = "Cash session off - heartbeat-only is expected for Analyzer / cash CoA."
    else:
        headline = "No Analyzer journal ticks readable for today."

    sids = [asdict(sid) for sid in WATCHED_SIDS]
    return HealthReport(
        collected_at_ist=now_ist(current).strftime("%Y-%m-%d %H:%M:%S IST"),
        source=str(facts.get("source") or "missing"),
        source_path=str(facts.get("source_path") or ""),
        severity=severity,
        headline=headline,
        session={
            **session,
            "on": session_on,
            "kill_switch": killed,
            "units": units,
        },
        analyzer={
            "sid": 999431310,
            "day": day,
            "ticks": len(day_rows),
            "reasons": dict(reasons),
            "dispositions": dict(dispositions),
            "live_symbols_crashes": live_hits,
            "last_disposition": last_disp,
            "last_reason_code": last_reason,
            "last_live_symbols_crash": last_crash,
            "earlier_defects": earlier_defects,
            "last_tick": {
                "at": last.get("created_at_utc") if last else None,
                "age_s": last_tick_age,
                "outcome": last.get("outcome") if last else None,
                "reason_code": last.get("reason_code") if last else None,
                "disposition": last.get("disposition") if last else None,
                "regime_label": last.get("regime_label") if last else None,
                "reason_text": (last.get("reason_text") or "")[:200] if last else None,
            },
            "ticks_today": _tick_summaries(day_rows),
            "ticks_preview": [
                {
                    "at": row.get("created_at_utc"),
                    "outcome": row.get("outcome"),
                    "reason_code": row.get("reason_code"),
                    "disposition": row.get("disposition"),
                    "live_symbols_crash": row.get("live_symbols_crash"),
                }
                for row in day_rows[:8]
            ],
        },
        ages={
            "desk_heartbeat_s": desk_age,
            "coa_heartbeat_s": coa_age,
            "last_hands_s": hands_age,
            "last_hands_detail": hands_detail,
            "last_analyzer_hands_s": analyzer_hands_age,
            "last_analyzer_hands_detail": analyzer_hands_detail,
            "last_fill_s": fill_age,
            "last_fill_detail": fill_detail,
            "last_analyzer_tick_s": last_tick_age,
        },
        sids=sids,
        books=books,
        quadshield=qs,
        alerts=alerts,
        raw={"log_hits": (facts.get("log_hits") or [])[-8:]},
    )


def render_card(report: HealthReport) -> str:
    def age(value: Any) -> str:
        if value is None:
            return "n/a"
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            return str(value)
        if seconds < 90:
            return f"{int(seconds)}s"
        if seconds < 3600:
            return f"{seconds / 60:.1f}m"
        if seconds < 86400:
            return f"{seconds / 3600:.1f}h"
        return f"{seconds / 86400:.1f}d"

    session = report.session
    analyzer = report.analyzer
    lines = [
        f"Strike Desk health  {report.collected_at_ist}  [{report.severity}]",
        report.headline,
        f"source: {report.source}  {report.source_path}",
        "",
        "Session",
        f"  cash: {'ON' if session.get('on') else 'OFF'}  "
        f"entry_window={session.get('analyzer_entry_window')}  "
        f"kill={session.get('kill_switch')}",
        f"  units: {session.get('units') or '{}'}",
        "",
        "SID 999431310 NIFTY Analyzer",
        f"  ticks today: {analyzer.get('ticks')}  "
        f"reasons={analyzer.get('reasons')}  "
        f"disp={analyzer.get('dispositions')}",
        f"  live_symbols crashes: {analyzer.get('live_symbols_crashes')}",
        f"  last tick: {analyzer.get('last_tick')}",
        "",
        "Ages",
        f"  desk heartbeat : {age(report.ages.get('desk_heartbeat_s'))}",
        f"  CoA heartbeat  : {age(report.ages.get('coa_heartbeat_s'))}",
        f"  last Hands     : {age(report.ages.get('last_hands_s'))}  "
        f"({report.ages.get('last_hands_detail')})",
        f"  Analyzer Hands : {age(report.ages.get('last_analyzer_hands_s'))}  "
        f"({report.ages.get('last_analyzer_hands_detail')})",
        f"  last fill      : {age(report.ages.get('last_fill_s'))}  "
        f"({report.ages.get('last_fill_detail')})",
        "",
        "QuadShield",
        f"  mind={report.quadshield.get('mind')}  "
        f"expected={report.quadshield.get('expected_legs')}  "
        f"observed={report.quadshield.get('observed_legs')}  "
        f"flag={report.quadshield.get('flag')}",
        "",
        "Books",
    ]
    for book in report.books:
        mapped = "mapped" if book.get("mapped") else "UNMAPPED"
        detail = "; ".join(book.get("issues") or [])
        lines.append(
            f"  {book['alignment']:<7}  {book['sid']}  {book['book_id']:<22}  "
            f"{book['kind']:<12}  {mapped}  {detail}"
        )
    if report.alerts:
        lines.append("")
        lines.append("Alerts")
        lines.extend(f"  - {item}" for item in report.alerts)
    lines.append("")
    lines.append("Monitor only - no orders, no Hands pulse, no live_symbols patch.")
    return "\n".join(lines)


def _gcloud_bin() -> str:
    """Resolve gcloud on Windows (gcloud.cmd) and POSIX."""
    for name in ("gcloud.cmd", "gcloud"):
        found = shutil.which(name)
        if found:
            return found
    win = Path.home() / "AppData/Local/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"
    if win.is_file():
        return str(win)
    return "gcloud"


def fetch_gce_facts(
    *,
    instance: str = GCE_INSTANCE,
    zone: str = GCE_ZONE,
    project: str = GCE_PROJECT,
) -> dict[str, Any]:
    """Upload this module and run --probe on GCE. Read-only."""
    remote = "/tmp/desk_health_probe.py"
    here = Path(__file__).resolve()
    gcloud = _gcloud_bin()
    scp = [
        gcloud,
        "compute",
        "scp",
        str(here),
        f"{instance}:{remote}",
        f"--zone={zone}",
        f"--project={project}",
        "--quiet",
    ]
    ssh = [
        gcloud,
        "compute",
        "ssh",
        instance,
        f"--zone={zone}",
        f"--project={project}",
        "--quiet",
        "--command",
        f"python3 {remote} --probe",
    ]
    subprocess.run(scp, check=True, timeout=90)
    proc = subprocess.run(ssh, check=True, capture_output=True, text=True, timeout=90)
    payload = json.loads(proc.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("GCE probe returned a non-object")
    return payload


def _exit_code(report: HealthReport) -> int:
    if report.severity == "critical":
        return 2
    if report.severity == "degraded":
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="desk-health",
        description="Read-only Strike Desk health monitor. Never places orders.",
    )
    parser.add_argument("--probe", action="store_true", help="dump raw host facts as JSON")
    parser.add_argument("--json", action="store_true", help="print the classified report as JSON")
    parser.add_argument("--journal", type=Path, help="strike_desk.db path")
    parser.add_argument("--state-dir", type=Path, help="override /var/lib/strike-desk")
    parser.add_argument("--from-json", type=Path, help="classify a previously saved probe")
    parser.add_argument(
        "--remote",
        action="store_true",
        help="gcloud ssh to GCE strike-desk and probe there",
    )
    parser.add_argument("--loop", type=int, metavar="SECONDS", help="repeat every N seconds")
    parser.add_argument(
        "--hourly",
        action="store_true",
        help="check now, then every 3600s (legacy alignment loop)",
    )
    parser.add_argument(
        "--every-15",
        dest="every_15",
        action="store_true",
        help="check now, then every 900s (Analyzer tick cadence)",
    )
    parser.add_argument(
        "--alert",
        action="store_true",
        help="send Telegram on fishy/broken, and on 2026-11-04 even if aligned",
    )
    parser.add_argument(
        "--dry-run-alert",
        action="store_true",
        help="print the Telegram alert text without sending",
    )
    parser.add_argument(
        "--telegram-status",
        action="store_true",
        help="print whether a ZTA_Arjun_Bot token/chat is resolvable (no secrets)",
    )
    parser.add_argument(
        "--send-alert",
        action="store_true",
        help="send --alert-file using env tokens (host-side helper)",
    )
    parser.add_argument("--alert-file", type=Path, help="alert body for --send-alert")
    parser.add_argument(
        "--write",
        type=Path,
        help="also write the latest report JSON to this path",
    )
    args = parser.parse_args(argv)

    if args.telegram_status:
        if args.remote:
            payload = _gce_telegram_status()
            print(json.dumps(payload))
            return 0 if payload.get("ok") == "yes" else 1
        payload = telegram_status_payload()
        print(json.dumps(payload))
        return 0 if payload["ok"] == "yes" else 1

    if args.send_alert:
        body = args.alert_file.read_text(encoding="utf-8") if args.alert_file else sys.stdin.read()
        secrets = resolve_telegram_secrets()
        if secrets["ok"] != "yes":
            print(json.dumps({**telegram_status_payload(secrets), "error": "no token/chat"}))
            return 1
        result = send_telegram_many(body, token=secrets["token"], chat_ids=secrets["chat_ids"])
        print(
            json.dumps(
                {
                    **{k: v for k, v in result.items() if k != "token"},
                    "chat_source": secrets["chat_source"],
                }
            )
        )
        return 0 if result.get("ok") else 1

    def once() -> tuple[int, str]:
        if args.probe:
            facts = collect_host_facts(state_dir=args.state_dir, journal=args.journal)
            text = json.dumps(facts, default=str)
            print(text)
            return 0, text
        if args.from_json:
            facts = _load_json_text(args.from_json)
        elif args.remote:
            facts = fetch_gce_facts()
        else:
            facts = collect_host_facts(state_dir=args.state_dir, journal=args.journal)
        report = build_report(facts)
        payload = asdict(report)
        text = json.dumps(payload, default=str, indent=2) if args.json else render_card(report)
        print(text)
        if args.write:
            args.write.parent.mkdir(parents=True, exist_ok=True)
            args.write.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
        alert = render_alert(report)
        if args.dry_run_alert:
            print("")
            print("--- telegram (dry run) ---")
            print(alert)
        elif args.alert and should_alert(report):
            result = deliver_alert(alert)
            print(
                f"telegram: ok={result.get('ok')} via={result.get('via')} "
                f"id={result.get('message_id')} err={result.get('error')}"
            )
        elif args.alert:
            print("telegram: skipped (all books aligned)")
        return _exit_code(report), text

    interval = TICK_SECONDS if args.every_15 else HOURLY_SECONDS if args.hourly else args.loop
    if interval:
        last_code = 0
        try:
            while True:
                last_code, _ = once()
                time.sleep(max(15, int(interval)))
        except KeyboardInterrupt:
            return last_code
        return last_code
    code, _ = once()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
