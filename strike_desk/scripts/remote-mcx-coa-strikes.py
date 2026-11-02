#!/usr/bin/env python3
"""Read CRUDEOIL / NATURALGAS option strikes and expiries. No secrets."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path("/opt/openalgo/current/db/openalgo.db")


def main() -> None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()
    for name in ("CRUDEOIL", "NATURALGAS"):
        expiries = cur.execute(
            "SELECT DISTINCT expiry FROM symtoken WHERE exchange='MCX' AND name=? "
            "AND (instrumenttype LIKE '%CE%' OR symbol LIKE '%CE') "
            "ORDER BY expiry LIMIT 12",
            (name,),
        ).fetchall()
        sample = cur.execute(
            "SELECT symbol, expiry, strike, instrumenttype FROM symtoken "
            "WHERE exchange='MCX' AND name=? AND symbol LIKE '%CE' "
            "ORDER BY expiry, strike LIMIT 8",
            (name,),
        ).fetchall()
        strikes = [
            r[0]
            for r in cur.execute(
                "SELECT DISTINCT strike FROM symtoken WHERE exchange='MCX' AND name=? "
                "AND symbol LIKE '%CE' AND expiry=(SELECT MIN(expiry) FROM symtoken "
                "WHERE exchange='MCX' AND name=? AND symbol LIKE '%CE') "
                "ORDER BY strike",
                (name, name),
            ).fetchall()
        ]
        step = None
        nums = [float(s) for s in strikes if s not in (None, "", 0)]
        diffs = sorted({round(nums[i + 1] - nums[i], 4) for i in range(len(nums) - 1) if nums[i + 1] > nums[i]})
        if diffs:
            step = diffs[0]
        futs = cur.execute(
            "SELECT symbol, expiry FROM symtoken WHERE exchange='MCX' AND name=? "
            "AND instrumenttype='FUT' ORDER BY expiry LIMIT 6",
            (name,),
        ).fetchall()
        print(name, "expiries", [e[0] for e in expiries])
        print(name, "fut", futs)
        print(name, "strike_step", step, "n_strikes_front", len(nums), "sample", sample[:4])
    print("STRIKES_OK")


if __name__ == "__main__":
    main()
