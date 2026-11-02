#!/usr/bin/env python3
"""Print Natgas CoA state and recent analyzer fills. No secrets."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

STATE = Path("/var/lib/strike-desk/coa_live_ng.json")
DEST = Path("/opt/strike-desk/current/strike_desk/src/strike_desk/coa_boards.py")


def main() -> None:
    text = DEST.read_text(encoding="utf-8")
    print("boards_has_1250", "contract_qty=1250" in text)
    print("boards_has_100", "contract_qty=100" in text)
    if STATE.is_file():
        raw = json.loads(STATE.read_text(encoding="utf-8"))
        keep = {
            "acted_signal": raw.get("acted_signal"),
            "analyzer_symbol": raw.get("analyzer_symbol"),
            "analyzer_qty": raw.get("analyzer_qty"),
            "open_trade": raw.get("open_trade"),
        }
        print("natgas_state", json.dumps(keep, default=str)[:800])
    else:
        print("natgas_state MISSING")
    out = subprocess.check_output(
        [
            "journalctl",
            "-u",
            "strike-desk-coa.service",
            "--since",
            "15:00",
            "--no-pager",
        ],
        text=True,
        errors="replace",
    )
    keys = ("NATURALGAS", "natgas", "coa analyzer", "qty=")
    for line in out.splitlines():
        low = line.lower()
        if any(k.lower() in low for k in keys):
            print(line[-400:])


if __name__ == "__main__":
    main()
