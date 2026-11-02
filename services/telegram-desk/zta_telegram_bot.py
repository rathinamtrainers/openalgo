#!/usr/bin/env python3
"""
ZeroTouchAlgo desk -> Telegram channel mirror.

Watches the public Strike Desk live JSON, detects chip flips
(WATCHING -> CALL TAGGED / PUT TAGGED and back), writes every event to an
append-only, hash-chained daily ledger, and posts to a Telegram channel.

Posts:
  * Map update    - when a board's chip flips to CALL TAGGED / PUT TAGGED
  * Map closed    - when a tagged board returns to WATCHING (or flips opposite)
  * Daily recap   - once a day at RECAP_TIME with the ledger digest

Design rules (from the ZTA app plan):
  * One house record: the ledger is written BEFORE anything is posted.
  * Never edit or delete a ledger line. Corrections are new events.
  * No post on stale data, outside session hours, or on holidays.
  * Default wording is index-level map state only. The desk's own sentence
    is OFF until counsel signs off (INCLUDE_DESK_NOTE=false).

Only dependency: requests  (pip install requests)
Python 3.9+ (uses zoneinfo).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import logging
import os
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger("zta-telegram")


# --------------------------------------------------------------------------
# Configuration (environment variables; see config.example.env)
# --------------------------------------------------------------------------

def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None:
        sys.exit(f"Missing required setting: {name}")
    return val


def _hhmm(value: str) -> dt.time:
    h, m = value.split(":")
    return dt.time(int(h), int(m))


LIVE_URL = _env("LIVE_URL")
BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
CHAT_ID = _env("TELEGRAM_CHAT_ID")          # e.g. @ZeroTouchAlgoDesk or -100xxxxxxxxxx
BOARDS = [b.strip().lower() for b in _env("BOARDS", "nifty").split(",") if b.strip()]
ALWAYS_OPEN = [
    b.strip().lower()
    for b in _env("ALWAYS_OPEN_BOARDS", "").split(",")
    if b.strip()
]
BRIEF_DIR = Path(_env("BRIEF_DIR", "")) if _env("BRIEF_DIR", "") else None
POLL_SECONDS = float(_env("POLL_SECONDS", "5"))
STALE_SECONDS = float(_env("STALE_SECONDS", "180"))
COOLDOWN_SECONDS = float(_env("COOLDOWN_SECONDS", "300"))
MAX_TAGS_PER_SESSION = int(_env("MAX_TAGS_PER_SESSION", "6"))
SESSION_START = _hhmm(_env("SESSION_START", "09:15"))
SESSION_END = _hhmm(_env("SESSION_END", "15:30"))
LAST_TAG_ALERT = _hhmm(_env("LAST_TAG_ALERT", "15:20"))
RECAP_TIME = _hhmm(_env("RECAP_TIME", "15:35"))
HOLIDAYS = {d.strip() for d in _env("HOLIDAYS", "").split(",") if d.strip()}  # YYYY-MM-DD
INCLUDE_DESK_NOTE = _env("INCLUDE_DESK_NOTE", "false").lower() == "true"
DRY_RUN = _env("DRY_RUN", "false").lower() == "true"
DATA_DIR = Path(_env("DATA_DIR", "./zta_desk_data"))
SITE_URL = _env("SITE_URL", "https://zerotouchalgo.com/ltp")
TRADETRON_URL = _env("TRADETRON_URL", "https://tradetron.tech/user-profiles/824766")

LEDGER_DIR = DATA_DIR / "ledger"
STATE_FILE = DATA_DIR / "state.json"

DISCLAIMER = (
    "Map, not advice. Not a recommendation to buy or sell. "
    "ZeroTouchAlgo is not a SEBI-registered Investment Adviser or Research Analyst. "
    "Practice book only - no orders are placed. "
    "Equity derivatives carry a high risk of loss."
)

TAGGED = {"CALL TAGGED", "PUT TAGGED"}
WATCHING = "WATCHING"


# --------------------------------------------------------------------------
# Feed adapter - the ONLY part that depends on the live JSON's shape.
# Adjust extract_boards() to match the real payload.
# --------------------------------------------------------------------------

def normalise_chip(raw: str | None) -> str:
    s = (raw or "").strip().upper().replace("_", " ")
    if "CALL" in s and "TAG" in s:
        return "CALL TAGGED"
    if "PUT" in s and "TAG" in s:
        return "PUT TAGGED"
    if "WATCH" in s:
        return WATCHING
    return s or "UNKNOWN"


def parse_ts(raw) -> dt.datetime | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):           # epoch seconds or ms
        secs = raw / 1000 if raw > 1e12 else raw
        return dt.datetime.fromtimestamp(secs, tz=IST)
    try:
        ts = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return ts if ts.tzinfo else ts.replace(tzinfo=IST)
    except ValueError:
        return None


def _as_last(raw) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _row(item: dict) -> dict:
    """One board snapshot. Chip comes only from chip-like fields, never from last."""
    chip_raw = (
        item.get("scenario_label")
        or item.get("chip")
        or item.get("state")
        or item.get("status")
    )
    last = item.get("last")
    if last is None:
        last = item.get("ltp")
    updated = parse_ts(
        item.get("computed_at")
        or item.get("updated_at")
        or item.get("ts")
        or item.get("timestamp")
    )
    note = item.get("line") or item.get("coa") or item.get("sentence") or item.get("do_now")
    return {
        "chip": normalise_chip(chip_raw),
        "last": _as_last(last),
        "updated_at": updated,
        "note": note,
    }


BRIEF_FILES = {
    "nifty": "public-brief.json",
    "banknifty": "public-brief-banknifty.json",
    "sensex": "public-brief-sensex.json",
    "crudeoil": "public-brief-crudeoil.json",
    "natgas": "public-brief-natgas.json",
    "btc-options": "public-brief-btc-options.json",
}


def _payload_slug(payload: dict, default_slug: str | None = None) -> str:
    raw = (
        payload.get("slug")
        or payload.get("market")
        or payload.get("symbol")
        or payload.get("name")
        or default_slug
        or "nifty"
    )
    return str(raw).strip().lower()


def extract_boards(payload, default_slug: str | None = None) -> dict[str, dict]:
    """
    Return {board_slug: {"chip", "last", "updated_at", "note"}}.

    Live Strike Desk public-brief*.json is a single-board object:
      market / slug = board, scenario_label = chip (WATCHING / CALL TAGGED /
      PUT TAGGED), last = index last, computed_at = ISO 8601 with +05:30.
    A {"boards": [...]} list or a dict keyed by slug is also accepted.
    """
    if not isinstance(payload, dict):
        return {}

    if "scenario_label" in payload or (
        "last" in payload and "boards" not in payload and "nifty" not in payload
    ):
        return {_payload_slug(payload, default_slug): _row(payload)}

    items = payload.get("boards", payload)
    if isinstance(items, dict):
        items = [dict(v, slug=k) for k, v in items.items() if isinstance(v, dict)]

    out: dict[str, dict] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        slug = _payload_slug(item)
        if slug in {"", "unknown"}:
            continue
        out[slug] = _row(item)
    return out


def _brief_filename(board: str) -> str:
    return BRIEF_FILES.get(board, f"public-brief-{board}.json")


def _brief_url(board: str) -> str:
    if board == "nifty":
        return LIVE_URL
    base = LIVE_URL.rsplit("/", 1)[0]
    return f"{base}/{_brief_filename(board)}"


def _read_brief(board: str) -> dict | None:
    if BRIEF_DIR:
        path = BRIEF_DIR / _brief_filename(board)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("%s: local brief unreadable (%s), trying HTTP", board, exc)
    try:
        resp = requests.get(
            _brief_url(board), timeout=10, headers={"Cache-Control": "no-cache"}
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else None
    except requests.RequestException as exc:
        log.warning("%s: brief fetch failed: %s", board, exc)
        return None


def fetch_boards() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for board in BOARDS:
        data = _read_brief(board)
        if not data:
            continue
        extracted = extract_boards(data, default_slug=board)
        snap = extracted.get(board) or next(iter(extracted.values()), None)
        if snap:
            out[board] = snap
    return out


# --------------------------------------------------------------------------
# Append-only, hash-chained ledger
# --------------------------------------------------------------------------

def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _ledger_path(day: dt.date) -> Path:
    return LEDGER_DIR / f"{day.isoformat()}.jsonl"


def _last_entry() -> dict | None:
    files = sorted(LEDGER_DIR.glob("*.jsonl"))
    for f in reversed(files):
        lines = [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            return json.loads(lines[-1])
    return None


def append_event(event: dict) -> dict:
    """Write one event. Returns it with seq, prev_hash and hash filled in."""
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    prev = _last_entry()
    entry = dict(event)
    entry["seq"] = (prev["seq"] + 1) if prev else 1
    entry["prev_hash"] = prev["hash"] if prev else "GENESIS"
    entry["hash"] = hashlib.sha256(_canonical(entry)).hexdigest()
    day = dt.datetime.fromisoformat(entry["ts"]).date()
    with _ledger_path(day).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return entry


def verify_ledger() -> tuple[bool, str]:
    """Recompute the whole chain. Used by `--verify` and before each recap."""
    prev_hash = "GENESIS"
    count = 0
    for f in sorted(LEDGER_DIR.glob("*.jsonl")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            entry = json.loads(ln)
            claimed = entry.pop("hash")
            if entry["prev_hash"] != prev_hash:
                return False, f"chain break at seq {entry['seq']} in {f.name}"
            if hashlib.sha256(_canonical(entry)).hexdigest() != claimed:
                return False, f"hash mismatch at seq {entry['seq']} in {f.name}"
            prev_hash = claimed
            count += 1
    return True, f"{count} events verified, head {prev_hash[:12]}"


def day_events(day: dt.date) -> list[dict]:
    p = _ledger_path(day)
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


# --------------------------------------------------------------------------
# State (survives restarts so nothing is re-posted)
# --------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"chips": {}, "last_tag_at": {}, "tags_today": {}, "recap_done": None, "day": None}


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------

def send(text: str) -> bool:
    if DRY_RUN:
        log.info("DRY RUN post:\n%s", text)
        return True
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    body = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True}
    for attempt in range(3):
        try:
            r = requests.post(url, json=body, timeout=10)
            if r.status_code == 429:
                wait = r.json().get("parameters", {}).get("retry_after", 5)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return True
        except requests.RequestException as exc:
            log.warning("Telegram send failed (attempt %d): %s", attempt + 1, exc)
            time.sleep(2 * (attempt + 1))
    return False


def _fmt_price(v) -> str:
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _footer(entry: dict) -> str:
    return (f"\n\n<i>{html.escape(DISCLAIMER)}</i>"
            f"\nLedger #{entry['seq']} · {entry['hash'][:12]}"
            f"\n<a href=\"{html.escape(SITE_URL)}\">Live board</a>")


def msg_tag_opened(entry: dict) -> str:
    t = dt.datetime.fromisoformat(entry["ts"]).strftime("%H:%M:%S")
    lines = [
        f"<b>{entry['board'].upper()} map update</b> · {t} IST",
        f"Map state: <b>{entry['to']}</b> (was {entry['from']})",
        f"Last: {_fmt_price(entry.get('last'))}",
        "Practice ticket opened in the house book (PRACTICE, not an order).",
    ]
    if INCLUDE_DESK_NOTE and entry.get("note"):
        lines.append(f"Desk note: {html.escape(str(entry['note']))}")
    return "\n".join(lines) + _footer(entry)


def msg_tag_closed(entry: dict) -> str:
    t = dt.datetime.fromisoformat(entry["ts"]).strftime("%H:%M:%S")
    return (f"<b>{entry['board'].upper()} map closed</b> · {t} IST\n"
            f"Map state: <b>{entry['to']}</b> (was {entry['from']})\n"
            f"Last: {_fmt_price(entry.get('last'))}\n"
            f"Practice ticket closed in the house book." + _footer(entry))


def msg_recap(day: dt.date, events: list[dict], verified: str) -> str:
    opened = [e for e in events if e["event"] == "TAG_OPENED"]
    closed = [e for e in events if e["event"] == "TAG_CLOSED"]
    lines = [f"<b>Desk recap · {day.strftime('%a %d %b %Y')}</b>"]
    if not opened:
        lines.append("No tags today. The desk stayed WATCHING - doing nothing is a decision too.")
    for board in BOARDS:
        b_open = [e for e in opened if e["board"] == board]
        if not b_open:
            continue
        lines.append(f"\n<b>{board.upper()}</b> - {len(b_open)} tag(s)")
        for e in b_open:
            t = dt.datetime.fromisoformat(e["ts"]).strftime("%H:%M")
            lines.append(f"  {t}  {e['to']} at {_fmt_price(e.get('last'))}")
    lines.append(f"\nEvents in ledger today: {len(events)} "
                 f"({len(opened)} opened, {len(closed)} closed)")
    head = events[-1]["hash"] if events else "none"
    lines.append(f"Day digest (head hash): <code>{head}</code>")
    lines.append(f"Chain check: {html.escape(verified)}")
    lines.append(f"\n<i>{html.escape(DISCLAIMER)}</i>")
    lines.append(f"<a href=\"{html.escape(TRADETRON_URL)}\">First month free on Tradetron</a>")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

def is_trading_day(now: dt.datetime) -> bool:
    return now.weekday() < 5 and now.date().isoformat() not in HOLIDAYS


def in_session(now: dt.datetime) -> bool:
    return is_trading_day(now) and SESSION_START <= now.time() <= SESSION_END


def process(state: dict, boards: dict[str, dict], now: dt.datetime) -> None:
    today = now.date().isoformat()
    if state.get("day") != today:
        state["day"] = today
        state["tags_today"] = {}

    for board in BOARDS:
        snap = boards.get(board)
        if not snap:
            continue
        upd = snap["updated_at"] or now
        if (now - upd).total_seconds() > STALE_SECONDS:
            log.info("%s: data stale (%s), skipping", board, upd.isoformat())
            continue

        new, old = snap["chip"], state["chips"].get(board)
        if old is None:                       # first sight: set a baseline, never post
            state["chips"][board] = new
            continue
        if new == old:
            continue

        base = {"ts": now.isoformat(timespec="seconds"), "board": board,
                "from": old, "to": new, "last": snap["last"], "note": snap["note"]}

        # A tagged board moving anywhere else (incl. the opposite tag) closes its practice ticket.
        if old in TAGGED:
            entry = append_event(dict(base, event="TAG_CLOSED"))
            send(msg_tag_closed(entry))

        if new in TAGGED:
            count = state["tags_today"].get(board, 0)
            last_at = state["last_tag_at"].get(board)
            cooling = last_at and (now - dt.datetime.fromisoformat(last_at)).total_seconds() < COOLDOWN_SECONDS
            entry = append_event(dict(base, event="TAG_OPENED"))
            always = board in ALWAYS_OPEN
            postable = (
                (always or (in_session(now) and now.time() <= LAST_TAG_ALERT))
                and count < MAX_TAGS_PER_SESSION
                and not cooling
            )
            if postable and send(msg_tag_opened(entry)):
                state["tags_today"][board] = count + 1
                state["last_tag_at"][board] = entry["ts"]
            elif not postable:
                log.info("%s: tag recorded in ledger, not posted (limits/cooldown/hours)", board)
        elif old not in TAGGED:               # e.g. UNKNOWN -> WATCHING; a close already logged the rest
            append_event(dict(base, event="MAP_STATE"))

        state["chips"][board] = new


def maybe_recap(state: dict, now: dt.datetime) -> None:
    today = now.date().isoformat()
    if not is_trading_day(now) or state.get("recap_done") == today or now.time() < RECAP_TIME:
        return
    ok, detail = verify_ledger()
    if not ok:
        log.error("Ledger verification FAILED: %s - recap withheld", detail)
        send(f"<b>Desk notice</b>\nToday's recap is withheld: ledger check failed ({html.escape(detail)}). "
             "We will publish what happened once it is resolved.")
    else:
        send(msg_recap(now.date(), day_events(now.date()), detail))
    state["recap_done"] = today


def run() -> None:
    state = load_state()
    log.info(
        "Watching %s for boards %s -> %s%s",
        BRIEF_DIR or LIVE_URL,
        BOARDS,
        CHAT_ID,
        " (dry run)" if DRY_RUN else "",
    )
    while True:
        now = dt.datetime.now(IST)
        try:
            session = in_session(now)
            if session or ALWAYS_OPEN:
                boards = fetch_boards()
                if not session:
                    boards = {k: v for k, v in boards.items() if k in ALWAYS_OPEN}
                if boards:
                    process(state, boards, now)
            if is_trading_day(now):
                maybe_recap(state, now)
            save_state(state)
        except Exception:                      # keep the service alive; log and retry
            log.exception("poll failed")
        time.sleep(POLL_SECONDS if in_session(now) or ALWAYS_OPEN else 30)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if "--verify" in sys.argv:
        ok, detail = verify_ledger()
        print(("OK: " if ok else "FAILED: ") + detail)
        sys.exit(0 if ok else 1)
    if "--test-post" in sys.argv:
        print("sent" if send("<b>ZeroTouchAlgo desk</b>\nTest message - channel connected.") else "failed")
        sys.exit(0)
    run()
