#!/usr/bin/env python3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import subprocess
import re

today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
p = Path(f"/var/lib/zta-desk/ledger/{today}.jsonl")
print("ledger", p, "exists", p.exists())
if p.exists():
    for ln in p.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        e = json.loads(ln)
        print(e.get("seq"), e.get("event"), e.get("board"), e.get("from"), "->", e.get("to"), e.get("ts"))

j = subprocess.run(
    "journalctl -u zta-desk-telegram.service --since '12:30' --no-pager -o cat",
    shell=True,
    capture_output=True,
    text=True,
)
text = re.sub(r"bot[0-9]{5,}:[A-Za-z0-9_-]+", "bot***", (j.stdout or "") + (j.stderr or ""))
# keep send/dry/error/tag lines
for line in text.splitlines():
    low = line.lower()
    if any(s in low for s in ("send", "dry", "tag", "error", "watch", "failed", "posted", "ledger")):
        print("J", line[-300:])
