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
import time
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
    blob = _haystack(reason_code, *texts)
    if "live_symbols" in blob:
        return True
    return reason_code == "internal-error" and "typeerror" in blob and "positional argument" in blob


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

    return {
        "collected_at_utc": datetime.now(tz=UTC).isoformat(),
        "source": source,
        "source_path": source_path,
        "state_dir": str(state),
        "units": _systemctl("strike-desk", "strike-desk-coa", "openalgo"),
        "heartbeat": _file_meta(state / "heartbeat"),
        "coa_heartbeat": _file_meta(state / "coa.heartbeat"),
        "kill_switch": _file_meta(state / "KILL"),
        "strikeshield": shield,
        "coa_live": coa_live,
        "events": events,
        "stack": stack,
        "decisions": decisions,
        "log_hits": _journalctl_hits(),
    }


def _log_blob(facts: dict[str, Any]) -> str:
    return " ".join(
        str(hit.get("line") or "") for hit in (facts.get("log_hits") or []) if isinstance(hit, dict)
    )


def _classify_day(facts: dict[str, Any], day: str) -> list[dict[str, Any]]:
    extra = _log_blob(facts)
    log_crash = "live_symbols" in extra.lower()
    rows: list[dict[str, Any]] = []
    for raw in facts.get("decisions") or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("trading_day") or "") != day:
            continue
        klass = classify_tick(
            str(raw.get("reason_code") or ""),
            str(raw.get("reason_text") or ""),
            extra,
        )
        item = dict(raw)
        if log_crash and item.get("reason_code") == "internal-error":
            klass = classify_tick("internal-error", "live_symbols TypeError", extra)
        item["disposition"] = klass.disposition
        item["classified_reason"] = klass.reason_code
        item["live_symbols_crash"] = klass.live_symbols_crash
        item["class_detail"] = klass.detail
        rows.append(item)
    return rows


ANALYZER_HANDS_NEEDLES = ("analyzer hands", "an_ce", "an_pe", "an_exit")


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


def build_report(facts: dict[str, Any], *, now: datetime | None = None) -> HealthReport:
    current = now or datetime.now(tz=UTC)
    session = cash_session(current)
    day = session["trading_day"]
    day_rows = _classify_day(facts, day)

    reasons = Counter(str(row.get("reason_code") or "unknown") for row in day_rows)
    dispositions = Counter(str(row.get("disposition") or "unknown") for row in day_rows)
    live_hits = sum(1 for row in day_rows if row.get("live_symbols_crash"))

    last = day_rows[0] if day_rows else None
    last_stamp = _parse_stamp(last.get("created_at_utc")) if last else None
    last_tick_age = _age_s(last_stamp, current)

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
    if live_hits:
        alerts.append(
            f"SID 999431310 live_symbols crash on {live_hits} tick(s) today - not a stand-aside."
        )
    elif dispositions.get("defect"):
        alerts.append(f"{dispositions['defect']} journal defect(s) today.")
    if session_on and not day_rows and units.get("strike-desk") == "active":
        alerts.append("Cash session on and desk unit active, but no Analyzer ticks today.")
    if session_on and isinstance(desk_age, (int, float)) and desk_age > HEARTBEAT_STALE_S:
        alerts.append(f"strike-desk heartbeat stale ({int(desk_age)}s).")
    if session_on and isinstance(coa_age, (int, float)) and coa_age > COA_HEARTBEAT_STALE_S:
        alerts.append(f"strike-desk-coa heartbeat stale ({int(coa_age)}s).")
    if session_on and analyzer_hands_age is None and day_rows:
        alerts.append("No Analyzer Hands pulse (an_ce/an_pe/an_exit) while ticks ran.")
    if session_on and hands_age is None:
        alerts.append("No Hands pulse found for today (Analyzer/CoA/QuadShield).")
    elif session_on and hands_age is not None and hands_age > HANDS_STALE_S:
        alerts.append(f"Last Hands pulse {int(hands_age)}s ago.")

    qs = _quadshield(facts)
    if qs.get("flag"):
        alerts.append(f"QuadShield: {qs['flag']}")

    defect_only = bool(dispositions.get("defect")) and not dispositions.get("stand-aside")
    if live_hits or (session_on and defect_only):
        severity = "critical"
    elif alerts:
        severity = "degraded"
    elif day_rows or not session_on:
        severity = "ok"
    else:
        severity = "unknown"

    if live_hits:
        headline = (
            f"NIFTY Analyzer SID 999431310 is broken today: {live_hits} "
            f"live_symbols crash(es). Not a correct stand-aside."
        )
    elif day_rows and set(dispositions) <= {"stand-aside"}:
        headline = (
            f"NIFTY Analyzer SID 999431310 stood aside correctly "
            f"({day_rows[0]['reason_code']} x {len(day_rows)})."
        )
    elif day_rows and ENTER_REASON in reasons:
        headline = f"NIFTY Analyzer entered today ({reasons[ENTER_REASON]} risk-cleared)."
    elif day_rows:
        headline = f"NIFTY Analyzer {len(day_rows)} tick(s) today: " + ", ".join(
            f"{code}x{count}" for code, count in reasons.most_common()
        )
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
            "last_tick": {
                "at": last.get("created_at_utc") if last else None,
                "age_s": last_tick_age,
                "outcome": last.get("outcome") if last else None,
                "reason_code": last.get("reason_code") if last else None,
                "disposition": last.get("disposition") if last else None,
                "regime_label": last.get("regime_label") if last else None,
                "reason_text": (last.get("reason_text") or "")[:200] if last else None,
            },
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
        "Watched SIDs",
    ]
    for sid in report.sids:
        mapped = "mapped" if sid.get("mapped") else "UNMAPPED"
        lines.append(
            f"  {sid['sid']}  {sid['book_id']:<22}  {sid['kind']:<12}  "
            f"{sid['expected_legs']}leg  {mapped}  {sid['flags']}"
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
        "--write",
        type=Path,
        help="also write the latest report JSON to this path",
    )
    args = parser.parse_args(argv)

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
        return _exit_code(report), text

    if args.loop:
        last_code = 0
        try:
            while True:
                last_code, _ = once()
                time.sleep(max(15, args.loop))
        except KeyboardInterrupt:
            return last_code
        return last_code
    code, _ = once()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
