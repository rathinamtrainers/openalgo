#!/usr/bin/env python3
from __future__ import annotations

import subprocess
from pathlib import Path

print(
    "coa",
    subprocess.check_output(["systemctl", "is-active", "strike-desk-coa.service"], text=True).strip(),
)
live = Path("/opt/strike-desk/current/strike_desk/src/strike_desk/ltp_coa_live.py").read_text(
    encoding="utf-8"
)
print("shim_update_tape", "_call_supported" in live and "update_tape" in live)
print("has_session_kw", "session=self.board.session" in live)
journal = subprocess.check_output(
    ["journalctl", "-u", "strike-desk-coa.service", "-n", "40", "--no-pager", "-o", "short-iso"],
    text=True,
    errors="replace",
)
print("=== last 40 ===")
print(journal)

buys = subprocess.check_output(
    [
        "journalctl",
        "-u",
        "strike-desk-coa.service",
        "--since",
        "2026-10-30 09:00:00",
        "--until",
        "2026-10-30 16:00:00",
        "--no-pager",
        "-o",
        "cat",
    ],
    text=True,
    errors="replace",
)
keep = []
for line in buys.splitlines():
    if any(
        token in line
        for token in (
            "BUY_CE",
            "BUY_PE",
            "coa analyzer",
            "analyzer",
            "symbol",
            "qty=",
            "exit",
        )
    ):
        keep.append(line[:300])
print("buy_lines", len(keep))
for line in keep:
    print(line)

print("=== lots ===")
print(
    subprocess.check_output(
        [
            "sudo",
            "python3",
            "-c",
            "import sqlite3,glob,os\n"
            "cands=glob.glob('/opt/openalgo/**/openalgo.db', recursive=True)+glob.glob('/var/lib/openalgo/**/*.db', recursive=True)\n"
            "print('cands', cands[:20])\n"
            "for db in cands:\n"
            "  if not db.endswith('openalgo.db'):\n"
            "    continue\n"
            "  try:\n"
            "    con=sqlite3.connect('file:%s?mode=ro'%db, uri=True)\n"
            "    cur=con.cursor()\n"
            "    print('db', db)\n"
            "    for p in ('BANKNIFTY','SENSEX','NIFTY','CRUDEOIL','NATURALGAS'):\n"
            "      row=cur.execute(\"SELECT symbol,lotsize,expiry FROM symtoken WHERE symbol LIKE ? AND (instrumenttype LIKE '%CE%' OR instrumenttype LIKE '%OPT%' OR instrumenttype LIKE '%PE%') LIMIT 1\", (p+'%',)).fetchone()\n"
            "      print(p, row)\n"
            "    con.close()\n"
            "  except Exception as e:\n"
            "    print('err', db, e)\n",
        ],
        text=True,
        errors="replace",
    )
)
