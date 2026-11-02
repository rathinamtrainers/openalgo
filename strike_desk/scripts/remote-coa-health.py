import json
import os
from pathlib import Path

root = Path("/var/lib/strike-desk")
print("=== unit ===")
os.system("systemctl is-active strike-desk-coa.service")
os.system("systemctl show strike-desk-coa.service -p ActiveState,SubState,MainPID,NRestarts --no-pager")

print("=== files ===")
for name in (
    "public-brief.json",
    "public-brief-banknifty.json",
    "public-brief-sensex.json",
    "public-brief-btc-options.json",
    "coa_live.json",
    "coa_live_bn.json",
    "coa_live_sx.json",
    "coa_live_btc.json",
):
    path = root / name
    if not path.exists():
        print(name, "MISSING")
        continue
    stat = path.stat()
    print(name, "bytes", stat.st_size, "mtime", stat.st_mtime)
    if name.startswith("public-brief") or name.startswith("coa_live"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print("  parse", type(exc).__name__)
            continue
        if name.startswith("public-brief"):
            print(
                " ",
                data.get("market"),
                data.get("scenario_label"),
                data.get("as_of_ist"),
                "spot",
                data.get("spot"),
                "open",
                data.get("open"),
            )
        else:
            opened = data.get("open")
            print(
                " ",
                "as_of",
                data.get("as_of_ist") or data.get("updated_at") or data.get("computed_at"),
                "open",
                opened,
                "keys",
                ",".join(sorted(data.keys())[:12]),
            )

print("=== env flags (no secrets) ===")
env_path = Path("/etc/strike-desk/strike-desk.env")
if env_path.exists():
    text = env_path.read_text(encoding="utf-8", errors="replace")
    for key in (
        "STRIKE_DESK_COA_ENABLED",
        "STRIKE_DESK_TT_HANDS",
        "STRIKE_DESK_COA_INTERVAL_SECONDS",
    ):
        present = any(line.startswith(key + "=") for line in text.splitlines())
        token_keys = (
            "STRIKE_DESK_TT_AUTH_TOKEN_COA=",
            "STRIKE_DESK_TT_AUTH_TOKEN_COA_BTC=",
            "STRIKE_DESK_COA_PUBLISH_TOKEN=",
        )
    print("COA_ENABLED line present", any(l.startswith("STRIKE_DESK_COA_ENABLED=") for l in text.splitlines()))
    print("TT_HANDS line present", any(l.startswith("STRIKE_DESK_TT_HANDS=") for l in text.splitlines()))
    print("COA token set", any(l.startswith("STRIKE_DESK_TT_AUTH_TOKEN_COA=") and len(l.split("=", 1)[-1].strip()) > 8 for l in text.splitlines()))
    print("BTC token set", any(l.startswith("STRIKE_DESK_TT_AUTH_TOKEN_COA_BTC=") and len(l.split("=", 1)[-1].strip()) > 8 for l in text.splitlines()))
    print("publish token set", any(l.startswith("STRIKE_DESK_COA_PUBLISH_TOKEN=") and len(l.split("=", 1)[-1].strip()) > 8 for l in text.splitlines()))
    for line in text.splitlines():
        if line.startswith("STRIKE_DESK_COA_ENABLED=") or line.startswith("STRIKE_DESK_TT_HANDS=") or line.startswith("STRIKE_DESK_COA_INTERVAL"):
            print(line.strip())

print("=== nginx doors ===")
os.system("sudo grep -n 'public-brief' /etc/nginx/sites-enabled/* /etc/nginx/conf.d/* 2>/dev/null | head -n 80")

print("=== journal ===")
os.system(
    "journalctl -u strike-desk-coa.service -n 80 --no-pager | "
    "grep -E 'CoA live started|boards=|coa live|OpenAlgo|error|Error|Hands|sent |skipped|banknifty|sensex|nifty' | tail -n 60"
)
