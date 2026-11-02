#!/usr/bin/env python3
"""Inspect CoA health, blotter, and any recoverable strike/lot from logs."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

print("coa", subprocess.check_output(["systemctl", "is-active", "strike-desk-coa.service"], text=True).strip())
print("oa", subprocess.check_output(["systemctl", "is-active", "openalgo.service"], text=True).strip())
print("desk", subprocess.check_output(["systemctl", "is-active", "strike-desk.service"], text=True).strip())

log = Path("/var/lib/strike-desk/coa-trades.jsonl")
print("jsonl", log.is_file(), "lines", sum(1 for _ in log.open(encoding="utf-8")) if log.is_file() else 0)
if log.is_file():
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        print(
            "row",
            row.get("id"),
            "strike",
            row.get("strike"),
            "lot",
            row.get("lot_size"),
            "peak",
            row.get("peak_ltp"),
            "pnl",
            row.get("atm_pnl"),
        )

journal = subprocess.check_output(
    [
        "journalctl",
        "-u",
        "strike-desk-coa.service",
        "--since",
        "2026-10-30 00:00:00",
        "--no-pager",
        "-o",
        "cat",
    ],
    text=True,
    errors="replace",
)
keys = ("BUY_CE", "BUY_PE", "exit", "analyzer", "TypeError", "Traceback", "coa live")
hits = [line for line in journal.splitlines() if any(k in line for k in keys)]
print("journal_hits", len(hits))
for line in hits[-80:]:
    print(line[:240])

for db in (
    Path("/opt/openalgo/current/db/openalgo.db"),
    Path("/var/lib/openalgo/openalgo.db"),
    Path("/opt/openalgo/db/openalgo.db"),
):
    if not db.is_file():
        continue
    print("db", db)
    try:
        import sqlite3

        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cur = con.cursor()
        for prefix in ("BANKNIFTY", "SENSEX", "NIFTY", "CRUDEOIL", "NATURALGAS"):
            row = cur.execute(
                "SELECT symbol, lotsize, expiry FROM symtoken WHERE symbol LIKE ? AND instrumenttype LIKE '%OPT%' LIMIT 1",
                (prefix + "%",),
            ).fetchone()
            print("lot", prefix, row)
        con.close()
    except Exception as exc:
        print("db_err", db, exc)
