#!/usr/bin/env python3
"""Why Nifty polls look stale. No secrets."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
now = datetime.now(IST)
print("now", now.isoformat(timespec="seconds"))

p = Path("/var/lib/strike-desk/public-brief.json")
st = p.stat()
print("file_mtime", datetime.fromtimestamp(st.st_mtime, IST).isoformat(timespec="seconds"))
data = json.loads(p.read_text(encoding="utf-8"))
for k in ("computed_at", "updated_at", "as_of_ist", "ts", "timestamp", "scenario_label"):
    print(k, data.get(k))

raw = data.get("computed_at")
if raw:
    ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=IST)
    age = (now - ts).total_seconds()
    print("computed_age_sec", round(age, 1))

env = Path("/etc/strike-desk/strike-desk.env")
if env.exists():
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("STRIKE_DESK_COA_INTERVAL"):
            print(line.strip())

# yesterday ledger
ledger_dir = Path("/var/lib/zta-desk/ledger")
print("ledger_files", sorted(p.name for p in ledger_dir.glob("*.jsonl")) if ledger_dir.exists() else "NONE")
for f in sorted(ledger_dir.glob("*.jsonl")) if ledger_dir.exists() else []:
    n = sum(1 for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip())
    print(" ", f.name, "events", n)
