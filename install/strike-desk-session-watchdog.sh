#!/usr/bin/env bash
# Keep OpenAlgo and Strike Desk up during the NSE cash session.
# The 15:35 stop timer remains the end of day — this script exits after 15:25 IST.
set -euo pipefail

python3 - <<'PY'
from datetime import datetime
from zoneinfo import ZoneInfo

now = datetime.now(ZoneInfo("Asia/Kolkata"))
if now.weekday() >= 5:
    raise SystemExit(0)
minutes = now.hour * 60 + now.minute
if minutes < 9 * 60 + 5 or minutes > 15 * 60 + 25:
    raise SystemExit(0)
PY

systemctl start openalgo.service
for _ in 1 2 3 4 5 6; do
  if curl -fsS -o /dev/null --max-time 3 http://127.0.0.1:5000/health/status; then
    break
  fi
  sleep 2
done
systemctl start strike-desk.service
