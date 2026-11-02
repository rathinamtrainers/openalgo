#!/usr/bin/env python3
"""Dump BTC CoA brief + state. No secrets."""

from __future__ import annotations

import json
from pathlib import Path

STATE = Path("/var/lib/strike-desk")
brief = json.loads((STATE / "public-brief-btc-options.json").read_text(encoding="utf-8"))
for key in (
    "action",
    "mood",
    "now",
    "cta",
    "eos",
    "eor",
    "line",
    "building",
    "do_now",
    "spot",
    "open",
    "high",
    "low",
    "last",
    "scenario_label",
    "as_of_ist",
    "change_vs_open",
    "destination",
):
    print(f"{key}={brief.get(key)!r}")
print("opportunities", json.dumps(brief.get("opportunities"), default=str)[:800])
state = json.loads((STATE / "coa_live_btc.json").read_text(encoding="utf-8"))
print("state_keys", sorted(state.keys()))
print("open", state.get("open"))
print("last_exit", state.get("last_exit"))
print("intel", json.dumps(state.get("intel"), default=str)[:600])
tape = state.get("tape")
if isinstance(tape, dict):
    print("tape_keys", sorted(tape.keys()))
    for k in ("open_spot", "high", "low", "tagged_eos", "tagged_eor", "last"):
        print(f"tape_{k}={tape.get(k)!r}")
    events = tape.get("events") or tape.get("rows") or tape.get("prints") or []
    if isinstance(events, list):
        print("tape_n", len(events))
        print("tape_tail", json.dumps(events[-5:], default=str)[:800])
else:
    print("tape", type(tape).__name__, str(tape)[:400] if tape is not None else None)
