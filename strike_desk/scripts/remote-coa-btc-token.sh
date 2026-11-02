#!/usr/bin/env bash
set -euo pipefail
TOKEN_FILE=/tmp/sd-coa/tt-coa-btc.token
ENV=/etc/strike-desk/strike-desk.env
if [[ ! -f $TOKEN_FILE ]]; then
  echo "missing BTC token file" >&2
  exit 1
fi
sudo python3 - <<PY
from pathlib import Path

token = Path("$TOKEN_FILE").read_text(encoding="utf-8").replace("\r", "").replace("\n", "").strip()
if not token:
    raise SystemExit("empty BTC CoA token")
env_path = Path("$ENV")
lines = env_path.read_text(encoding="utf-8").splitlines()
key = "STRIKE_DESK_TT_AUTH_TOKEN_COA_BTC"
nifty = "STRIKE_DESK_TT_AUTH_TOKEN_COA"
seen = False
nifty_val = ""
out = []
for line in lines:
    name = line.split("=", 1)[0] if "=" in line else ""
    if name == nifty:
        nifty_val = line.split("=", 1)[1] if "=" in line else ""
    if name == key:
        out.append(f"{key}={token}")
        seen = True
    else:
        out.append(line)
if not seen:
    out.append(f"{key}={token}")
if nifty_val and nifty_val == token:
    raise SystemExit("refusing: BTC token matches NSE/BSE CoA token")
env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
print("btc_env_set")
PY
rm -f "$TOKEN_FILE"
OLD=$(systemctl show -p MainPID --value strike-desk-coa.service)
sudo systemctl restart strike-desk-coa.service
sleep 3
NEW=$(systemctl show -p MainPID --value strike-desk-coa.service)
systemctl is-active strike-desk-coa.service
echo "coa_pid_before=$OLD coa_pid_after=$NEW"
journalctl -u strike-desk-coa.service -n 30 --no-pager | grep -E "CoA live started|COA_BTC unset|Hands stay idle|boards=" || true
