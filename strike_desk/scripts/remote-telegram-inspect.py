#!/usr/bin/env python3
"""Inspect desk Telegram mirror on GCE. Never print tokens or chat secrets."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

SECRET_KEYS = {
    "TELEGRAM_BOT_TOKEN",
    "BOT_TOKEN",
    "OWNER_CHAT_ID",
}
SECRET_RE = re.compile(
    r"(bot)[0-9]{6,}:[A-Za-z0-9_-]{20,}|([0-9]{8,}:[A-Za-z0-9_-]{20,})",
    re.I,
)


def run(cmd: str) -> str:
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    return SECRET_RE.sub("***", out).strip()


def redact_line(line: str) -> str:
    if "=" not in line or line.strip().startswith("#"):
        return line
    key = line.split("=", 1)[0].strip()
    if key in SECRET_KEYS or "TOKEN" in key.upper() or "SECRET" in key.upper():
        val = line.split("=", 1)[1].strip().strip('"').strip("'")
        if not val:
            return f"{key}=EMPTY"
        if "REPLACE_ME" in val or val.startswith("123456789"):
            return f"{key}=PLACEHOLDER"
        return f"{key}=SET len={len(val)}"
    return SECRET_RE.sub("***", line)


print("=== units ===")
for unit in (
    "zta-desk-telegram.service",
    "zta-desk-health.timer",
    "zta-desk-health.service",
    "openalgo.service",
    "strike-desk-coa.service",
):
    active = run(f"systemctl is-active {unit} 2>/dev/null || echo missing")
    enabled = run(f"systemctl is-enabled {unit} 2>/dev/null || echo missing")
    print(unit, "active=", active, "enabled=", enabled)

print("=== telegram unit detail ===")
print(
    run(
        "systemctl show zta-desk-telegram.service "
        "-p ActiveState,SubState,MainPID,NRestarts,ExecMainStatus,Result,User "
        "--no-pager 2>/dev/null || echo UNIT_MISSING"
    )
)

print("=== files ===")
for p in (
    "/opt/zta-desk/zta_telegram_bot.py",
    "/opt/zta-desk/config.env",
    "/opt/zta-desk/healthcheck.py",
    "/var/lib/zta-desk/state.json",
    "/etc/systemd/system/zta-desk-telegram.service",
):
    path = Path(p)
    if path.exists():
        st = path.stat()
        print(p, "bytes", st.st_size, "mtime", int(st.st_mtime))
    else:
        print(p, "MISSING")

cfg = Path("/opt/zta-desk/config.env")
print("=== config (redacted) ===")
if cfg.exists():
    for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        print(redact_line(s))
else:
    print("NO_CONFIG")

print("=== state ===")
state_path = Path("/var/lib/zta-desk/state.json")
if state_path.exists():
    st = json.loads(state_path.read_text(encoding="utf-8"))
    print(json.dumps(st, indent=2, default=str)[:2000])
    age = __import__("time").time() - state_path.stat().st_mtime
    print("state_age_sec", int(age))
else:
    print("NO_STATE")

print("=== today ledger ===")
from datetime import datetime
from zoneinfo import ZoneInfo

today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
ledger = Path(f"/var/lib/zta-desk/ledger/{today}.jsonl")
if ledger.exists():
    lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    print("events", len(lines), "path", ledger)
    for ln in lines[-12:]:
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            print("  bad json")
            continue
        print(
            " ",
            e.get("seq"),
            e.get("event"),
            e.get("board"),
            e.get("from"),
            "->",
            e.get("to"),
            e.get("ts"),
        )
else:
    print("NO_LEDGER_TODAY", today)

print("=== briefs ===")
root = Path("/var/lib/strike-desk")
for name in (
    "public-brief.json",
    "public-brief-banknifty.json",
    "public-brief-sensex.json",
    "public-brief-btc-options.json",
):
    p = root / name
    if not p.exists():
        print(name, "MISSING")
        continue
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        print(name, "parse", type(exc).__name__)
        continue
    print(
        name,
        data.get("market") or data.get("symbol"),
        data.get("scenario_label"),
        data.get("as_of_ist") or data.get("computed_at"),
        "open",
        data.get("open"),
    )

print("=== journal (redacted, last 40) ===")
journal = run(
    "journalctl -u zta-desk-telegram.service -n 40 --no-pager -o cat 2>/dev/null || echo NO_JOURNAL"
)
print(journal[-4000:])

print("=== openalgo telegram bot (no secrets) ===")
# Only whether a bot config row exists; do not print token.
oa = Path("/opt/openalgo/current")
db = Path("/opt/openalgo/current/db/openalgo.db")
if not db.exists():
    db = Path("/var/lib/openalgo/openalgo.db")
if db.exists():
    try:
        import sqlite3

        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cur = con.cursor()
        tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        print("tables_with_telegram", [t for t in tables if "telegram" in t.lower()])
        for t in tables:
            if "telegram" in t.lower() and "config" in t.lower():
                cols = [r[1] for r in cur.execute(f"PRAGMA table_info({t})")]
                safe = [c for c in cols if "token" not in c.lower() and "secret" not in c.lower()]
                if not safe:
                    print(t, "rows", cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
                    continue
                q = f"SELECT {', '.join(safe)} FROM {t} LIMIT 3"
                rows = cur.fetchall() if False else cur.execute(q).fetchall()
                print(t, "cols", safe, "rows", rows)
        con.close()
    except Exception as exc:
        print("sqlite", type(exc).__name__, str(exc)[:120])
else:
    print("OA_DB_MISSING")

print("INSPECT_OK")
