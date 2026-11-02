import json
import urllib.request
from pathlib import Path

print("--- briefs ---")
root = Path("/var/lib/strike-desk")
for name in (
    "public-brief.json",
    "public-brief-banknifty.json",
    "public-brief-sensex.json",
    "public-brief-btc-options.json",
):
    path = root / name
    if not path.exists():
        print(name, "MISSING")
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    print(
        name,
        data.get("market"),
        data.get("index"),
        data.get("scenario_label"),
        data.get("as_of_ist"),
        "spot",
        data.get("spot"),
    )

print("--- curl ---")
url = "https://desk.zerotouchalgo.com/public-brief-btc-options.json"
try:
    with urllib.request.urlopen(url, timeout=10) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
        print(
            "http",
            resp.status,
            payload.get("market"),
            payload.get("scenario_label"),
            payload.get("as_of_ist"),
            payload.get("spot"),
        )
except Exception as exc:
    print("curl failed", type(exc).__name__, str(exc)[:200])
