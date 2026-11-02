#!/usr/bin/env python3
import glob
import sqlite3
import subprocess

print("coa", subprocess.check_output(["systemctl", "is-active", "strike-desk-coa.service"], text=True).strip())
print(
    subprocess.check_output(
        ["journalctl", "-u", "strike-desk-coa.service", "-n", "25", "--no-pager", "-o", "short-iso"],
        text=True,
        errors="replace",
    )
)
cands = glob.glob("/opt/openalgo/**/openalgo.db", recursive=True)
print("cands", cands)
for db in cands:
    if not db.endswith("openalgo.db"):
        continue
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cur = con.cursor()
        print("db", db)
        for prefix in ("BANKNIFTY", "SENSEX", "NIFTY", "CRUDEOIL", "NATURALGAS"):
            row = cur.execute(
                "SELECT symbol, lotsize, expiry FROM symtoken "
                "WHERE symbol LIKE ? AND (symbol LIKE '%CE' OR symbol LIKE '%PE') LIMIT 1",
                (prefix + "%",),
            ).fetchone()
            print(prefix, row)
        con.close()
    except Exception as exc:
        print("err", db, type(exc).__name__, exc)
