import json
from pathlib import Path

brief = json.loads(
    Path("/var/lib/strike-desk/public-brief-btc-options.json").read_text(encoding="utf-8")
)
print(
    "brief",
    brief.get("scenario_label"),
    brief.get("as_of_ist"),
    "spot",
    brief.get("spot"),
    "eos",
    brief.get("eos"),
    "eor",
    brief.get("eor"),
    "do_now",
    (brief.get("do_now") or "")[:120],
)
state_path = Path("/var/lib/strike-desk/coa_live_btc.json")
if state_path.exists():
    state = json.loads(state_path.read_text(encoding="utf-8"))
    print("open", state.get("open"))
    print("last_exit", state.get("last_exit"))
else:
    print("state MISSING")
