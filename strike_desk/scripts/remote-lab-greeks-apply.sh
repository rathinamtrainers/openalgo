#!/bin/bash
set -euo pipefail
rm -rf /tmp/sd-lab
mkdir -p /tmp/sd-lab
tar -xzf /tmp/sd-lab-greeks.tgz -C /tmp/sd-lab
python3 - <<'PY'
from pathlib import Path
for p in Path("/tmp/sd-lab").glob("*.py"):
    p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
PY
test -f /tmp/sd-lab/greeks_lab_coa.py
test -d /tmp/sd-lab/dist
python3 /tmp/sd-lab/remote-lab-greeks-install.py
test -f /opt/openalgo/current/services/greeks_lab_coa.py
ls /opt/openalgo/current/frontend/dist/assets/GreeksLab-*.js
systemctl is-active openalgo.service
systemctl is-active strike-desk-coa.service
echo APPLY_OK
