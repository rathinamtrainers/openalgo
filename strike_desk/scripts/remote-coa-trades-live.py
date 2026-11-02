#!/usr/bin/env python3
import json
from pathlib import Path

log = Path("/var/lib/strike-desk/coa-trades.jsonl")
rows = []
for line in log.read_text(encoding="utf-8").splitlines():
    if line.strip():
        rows.append(json.loads(line))
print("count", len(rows))
for row in rows:
    print(
        row.get("ist_span"),
        row.get("title"),
        row.get("in_label"),
        row.get("out_label"),
        row.get("hold_label"),
    )
oa = Path("/opt/openalgo/current")
coa = (oa / "services" / "greeks_lab_coa.py").read_text(encoding="utf-8")
bp = (oa / "blueprints" / "greeks_lab.py").read_text(encoding="utf-8")
print("api", "def get_coa_trades" in coa, "/greekslab/api/coa-trades" in bp)
chunks = sorted((oa / "frontend" / "dist" / "assets").glob("GreeksLab-*.js"))
print("chunks", [p.name for p in chunks])
blob = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in chunks)
print("ui", "Hands-confirmed fills only" in blob)
live = Path("/opt/strike-desk/current/strike_desk/src/strike_desk/ltp_coa_live.py").read_text(
    encoding="utf-8"
)
print("hooks", "record_entry" in live, "record_exit" in live, "except ImportError" in live)
