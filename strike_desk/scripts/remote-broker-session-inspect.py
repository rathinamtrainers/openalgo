#!/usr/bin/env python3
"""Inspect OpenAlgo broker-session state. Never prints tokens."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

DB = Path("/opt/openalgo/current/db/openalgo.db")
ENV = Path("/opt/openalgo/current/.env")


def _env(key: str) -> str:
    if not ENV.is_file():
        return ""
    prefix = f"{key}="
    for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


print("openalgo", subprocess.check_output(["systemctl", "is-active", "openalgo.service"], text=True).strip())
print("db", DB.is_file(), "size", DB.stat().st_size if DB.is_file() else 0)
print("disable_session_expiry", _env("DISABLE_SESSION_EXPIRY") or "unset")
print("session_expiry_time", _env("SESSION_EXPIRY_TIME") or "unset")
print("valid_brokers", _env("VALID_BROKERS") or "unset")

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cols = [r[1] for r in con.execute("PRAGMA table_info(auth)").fetchall()]
print("auth_cols", cols)
for row in con.execute("SELECT * FROM auth"):
    print(
        "auth_row",
        {
            "name": row["name"],
            "broker": row["broker"],
            "is_revoked": bool(row["is_revoked"]),
            "has_auth": bool(row["auth"]),
            "auth_len": len(row["auth"] or ""),
            "has_feed": bool(row["feed_token"]),
            "user_id_set": bool(row["user_id"]),
        },
    )

user_cols = [r[1] for r in con.execute("PRAGMA table_info(users)").fetchall()]
print("user_cols", user_cols)
if "username" in user_cols:
    for row in con.execute("SELECT username FROM users"):
        print("user", row["username"])

# sessions table if present
tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
print("tables", sorted(t for t in tables if "session" in t.lower() or t in {"auth", "users"}))
if "active_sessions" in tables:
    a_cols = [r[1] for r in con.execute("PRAGMA table_info(active_sessions)").fetchall()]
    print("active_session_cols", a_cols)
    for row in con.execute("SELECT * FROM active_sessions"):
        dumped = {k: row[k] for k in row.keys() if k not in {"token", "auth", "cookie"}}
        print("active_session", dumped)
con.close()

log = subprocess.check_output(
    [
        "journalctl",
        "-u",
        "openalgo.service",
        "--since",
        "20 min ago",
        "--no-pager",
        "-o",
        "cat",
    ],
    text=True,
    errors="replace",
)
keys = (
    "revoking tokens",
    "Session expired",
    "broker token",
    "No valid auth token",
    "Resuming existing",
    "BROKER_SESSION",
    "is_revoked",
    "Started OpenAlgo",
    "Stopped",
)
hits = []
for line in log.splitlines():
    if any(k.lower() in line.lower() for k in keys):
        # drop anything that looks like a token
        if "token=" in line.lower() or "auth=" in line.lower():
            continue
        hits.append(line[:240])
print("log_hits", len(hits))
for line in hits[-40:]:
    print("LOG", line)
print("BROKER_INSPECT_OK")
