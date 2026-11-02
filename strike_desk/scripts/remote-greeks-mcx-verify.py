#!/usr/bin/env python3
"""Confirm GOLDM futures exist and the new resolver would pick one. No secrets."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DBS = [
    Path("/opt/openalgo/current/db/openalgo.db"),
    Path("/opt/openalgo/db/openalgo.db"),
]


def main() -> None:
    db = next((path for path in DBS if path.is_file()), None)
    print("db", db)
    if not db:
        raise SystemExit("no openalgo.db")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cur = con.cursor()
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    print("tables", [t for t in tables if "sym" in t.lower() or "token" in t.lower()][:12])
    table = "symtoken" if "symtoken" in tables else ("SymToken" if "SymToken" in tables else None)
    if not table:
        print("no symtoken table")
        return
    cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]
    print("cols", cols)
    rows = cur.execute(
        f"SELECT symbol, expiry, instrumenttype, name FROM {table} "
        "WHERE exchange='MCX' AND (symbol LIKE 'GOLDM%' OR name='GOLDM') "
        "AND (instrumenttype LIKE 'FUT%' OR symbol LIKE '%FUT') "
        "ORDER BY expiry LIMIT 20"
    ).fetchall()
    print("goldm_futs", rows)
    opts = cur.execute(
        f"SELECT DISTINCT expiry FROM {table} WHERE exchange='MCX' AND "
        "(symbol LIKE 'GOLDM%CE' OR symbol LIKE 'GOLDM%PE' OR name='GOLDM') "
        "AND instrumenttype LIKE '%CE%' OR (exchange='MCX' AND symbol LIKE 'GOLDM%CE') "
        "LIMIT 15"
    ).fetchall()
    print("goldm_opt_expiries_sample", opts[:15])
    print("VERIFY_OK")


if __name__ == "__main__":
    main()
