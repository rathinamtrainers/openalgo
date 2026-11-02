#!/usr/bin/env python3
"""Confirm live Lab dist + CoA briefs after BTC isolation overlay."""

from __future__ import annotations

import json
import sys
from pathlib import Path

DIST = Path("/opt/openalgo/current/frontend/dist")
COA = Path("/opt/openalgo/current/services/greeks_lab_coa.py")
OA = Path("/opt/openalgo/current")
BRIEF = Path("/var/lib/strike-desk/public-brief-btc-options.json")

html = (DIST / "index.html").read_text(encoding="utf-8", errors="ignore")
chunks = [p.name for p in sorted((DIST / "assets").glob("GreeksLab-*.js"))]
blob = " ".join(
    p.read_text(encoding="utf-8", errors="ignore")
    for p in (DIST / "assets").glob("GreeksLab-*.js")
)
coa_text = COA.read_text(encoding="utf-8")

sys.path.insert(0, str(OA))
from services.greeks_lab_coa import get_coa_boards  # noqa: E402

payload = get_coa_boards()
boards = {row.get("slug"): row for row in (payload.get("boards") or [])}
btc = boards.get("btc-options") or boards.get("btc") or {}
nifty = boards.get("nifty") or {}

print("html_index", "index-BXU4Uu3O.js" in html)
print("chunks", chunks)
print("dist_marker", "no crypto option chain" in blob)
print("coa_mood", '"mood"' in coa_text and '"disclaimer"' in coa_text)
print("btc_slug", btc.get("slug"), "greeks_exchange", btc.get("greeks_exchange"))
print(
    "btc_keys",
    sorted(
        k
        for k in ("mood", "disclaimer", "support", "eos", "resistance", "eor")
        if btc.get(k) is not None
    ),
)
print("nifty_greeks", nifty.get("greeks_exchange"), nifty.get("greeks_underlying"))
print("board_count", len(boards), "status", payload.get("status"))
print("brief_file", BRIEF.is_file())
if BRIEF.is_file():
    raw = json.loads(BRIEF.read_text(encoding="utf-8"))
    print("brief_spot", raw.get("spot") or raw.get("last"))
print("LAB_BTC_LIVE_OK")
