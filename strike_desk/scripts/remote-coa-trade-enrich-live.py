#!/usr/bin/env python3
import json
from pathlib import Path

log = Path("/var/lib/strike-desk/coa-trades.jsonl")
by_id = {}
for line in log.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    row = json.loads(line)
    if row.get("id"):
        by_id[row["id"]] = row
print("unique", len(by_id), "lines", sum(1 for _ in log.open(encoding="utf-8")))
for row in by_id.values():
    print(
        row.get("ist_span"),
        row.get("title"),
        row.get("strike_label"),
        "lot",
        row.get("lot_size"),
        "qty",
        row.get("qty"),
        "pnl",
        row.get("atm_pnl"),
        "peak",
        row.get("peak_ltp"),
    )
live = Path("/opt/strike-desk/current/strike_desk/src/strike_desk/ltp_coa_live.py").read_text(
    encoding="utf-8"
)
print(
    "hooks",
    "_blotter_contract" in live,
    "_call_supported" in live,
    "session=self.board.session" in live,
)
oa = Path("/opt/openalgo/current/services/greeks_lab_coa.py").read_text(encoding="utf-8")
print("api", "atm_pnl" in oa, "_overlay_open_mark" in oa)
blob = "".join(
    p.read_text(encoding="utf-8", errors="ignore")
    for p in Path("/opt/openalgo/current/frontend/dist/assets").glob("GreeksLab-*.js")
)
print("ui", "ATM-leg" in blob, "gamma-1" in blob)
