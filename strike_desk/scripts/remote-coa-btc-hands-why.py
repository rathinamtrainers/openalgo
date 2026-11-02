#!/usr/bin/env python3
"""Why BTC Hands is quiet. Run on GCE as root. Never prints tokens."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

STATE = Path("/var/lib/strike-desk")
ENV = Path("/etc/strike-desk/strike-desk.env")


def _env_set(key: str) -> bool:
    if not ENV.is_file():
        return False
    for line in ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith(key + "=") and len(line.split("=", 1)[-1].strip()) > 4:
            return True
    return False


print("unit", subprocess.check_output(["systemctl", "is-active", "strike-desk-coa.service"], text=True).strip())
print("token_btc", _env_set("STRIKE_DESK_TT_AUTH_TOKEN_COA_BTC"))
print("coa_enabled", _env_set("STRIKE_DESK_COA_ENABLED"))

now = datetime.now(timezone.utc)
for name in (
    "public-brief-btc-options.json",
    "coa_live_btc.json",
    "public-brief.json",
    "coa_live.json",
    "public-brief-natgas.json",
    "coa_live_ng.json",
):
    path = STATE / name
    if not path.is_file():
        print(name, "MISSING")
        continue
    age = int(now.timestamp() - path.stat().st_mtime)
    print("file", name, "age_s", age, "bytes", path.stat().st_size)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print("  parse", type(exc).__name__)
        continue
    if name.startswith("public-brief"):
        print(
            "  market",
            data.get("market"),
            "signal",
            data.get("signal") or data.get("scenario") or data.get("scenario_label"),
            "as_of",
            data.get("as_of_ist"),
            "spot",
            data.get("spot"),
            "open",
            data.get("open"),
            "hands_posted",
            data.get("hands_posted"),
            "do_now",
            (data.get("do_now") or "")[:80],
        )
        print(
            "  keys",
            ",".join(sorted(data.keys())[:24]),
        )
    else:
        opened = data.get("open")
        print("  last_exit", data.get("last_exit"))
        if opened is None:
            print("  open", None)
        elif isinstance(opened, dict):
            print(
                "  open",
                opened.get("side"),
                "strike",
                opened.get("strike"),
                "entry",
                opened.get("entry"),
                "ts",
                opened.get("entry_ts"),
                "scenario",
                opened.get("scenario"),
            )
        else:
            print("  open_type", type(opened).__name__)

print("=== journal btc/hands (since 24h) ===")
os.system(
    "journalctl -u strike-desk-coa.service --since '24 hours ago' --no-pager -o cat | "
    "grep -E 'btc-options|BUY_CE|BUY_PE|buy_ce|buy_pe|Hands|hands |Noop|token_btc|sent |skipped: empty|skipped: no expiry|delta india' | tail -n 80"
)
