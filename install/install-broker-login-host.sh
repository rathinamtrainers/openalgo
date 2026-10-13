#!/usr/bin/env bash
# Install the weekday 08:20 IST Zerodha session refresh on the practice host.
set -euo pipefail

ROOT="${1:-/opt/openalgo/current}"
install -d -m 0750 -o openalgo -g openalgo /etc/openalgo
if [[ ! -f /etc/openalgo/zerodha-login.env ]]; then
  install -m 0640 -o openalgo -g openalgo \
    "$ROOT/install/zerodha-login.env.example" /etc/openalgo/zerodha-login.env
  echo "created empty /etc/openalgo/zerodha-login.env — fill KITE_* before 08:20"
fi

install -m 0644 "$ROOT/install/openalgo-broker-login.service" /etc/systemd/system/openalgo-broker-login.service
install -m 0644 "$ROOT/install/openalgo-broker-login.timer" /etc/systemd/system/openalgo-broker-login.timer

if ! grep -q zerodha_session_refresh /usr/local/bin/strike-desk-session-start; then
  python3 - <<'PY'
from pathlib import Path
p = Path("/usr/local/bin/strike-desk-session-start")
text = p.read_text()
needle = "systemctl start strike-desk.service"
hook = """# Refresh the daily Zerodha token if yesterday's session died at ~03:00 IST.
if [[ -x /opt/openalgo/current/.venv/bin/python ]]; then
  PYTHONPATH=/opt/openalgo/current OPENALGO_ENV=/opt/openalgo/current/.env \\
    ZERODHA_LOGIN_ENV=/etc/openalgo/zerodha-login.env \\
    /opt/openalgo/current/.venv/bin/python /opt/openalgo/current/services/zerodha_session_refresh.py \\
    || echo "broker session refresh failed (desk will still start)"
fi
systemctl start strike-desk.service"""
if needle not in text:
    raise SystemExit("strike-desk-session-start missing start line")
p.write_text(text.replace(needle, hook, 1))
PY
  chmod 0755 /usr/local/bin/strike-desk-session-start
fi

systemctl daemon-reload
systemctl enable --now openalgo-broker-login.timer
systemctl list-timers openalgo-broker-login.timer --no-pager
