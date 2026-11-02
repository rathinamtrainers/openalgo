#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
from pathlib import Path
p = Path("/tmp/sd-coa/install-coa.sh")
p.write_bytes(p.read_bytes().replace(b"\r", b""))
PY
if [[ -f /tmp/sd-coa/sd-tt-coa.token ]]; then
  mv /tmp/sd-coa/sd-tt-coa.token /tmp/sd-coa/tt-coa.token
fi
if [[ -f /tmp/sd-coa/sd-tt-coa-btc.token ]]; then
  mv /tmp/sd-coa/sd-tt-coa-btc.token /tmp/sd-coa/tt-coa-btc.token
fi
sudo bash /tmp/sd-coa/install-coa.sh
systemctl is-active strike-desk-coa.service
systemctl is-active strike-desk.service
grep -n "make_btc_runner" /opt/strike-desk/current/strike_desk/src/strike_desk/coa_main.py
grep -n "hands_posted" /opt/strike-desk/current/strike_desk/src/strike_desk/ltp_coa_brief.py
