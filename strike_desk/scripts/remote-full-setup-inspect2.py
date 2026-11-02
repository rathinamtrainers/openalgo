#!/usr/bin/env python3
"""Full-setup health. Run as root. Never prints secret values."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

OA = Path("/opt/openalgo/current")
SD = Path("/opt/strike-desk/current/strike_desk/src/strike_desk")
ENV = Path("/etc/strike-desk/strike-desk.env")
STATE = Path("/var/lib/strike-desk")
NGINX = Path("/etc/nginx/sites-available/desk.zerotouchalgo.com")


def _env_set(key: str) -> bool:
    for line in ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith(key + "=") and len(line.split("=", 1)[-1].strip()) > 4:
            return True
    return False


def _env_true(key: str) -> bool:
    for line in ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[-1].strip().lower() in {"1", "true", "yes"}
    return False


nginx = NGINX.read_text(encoding="utf-8", errors="ignore") if NGINX.is_file() else ""
print("coa_enabled", _env_true("STRIKE_DESK_COA_ENABLED"))
print("token_coa", _env_set("STRIKE_DESK_TT_AUTH_TOKEN_COA"))
print("token_btc", _env_set("STRIKE_DESK_TT_AUTH_TOKEN_COA_BTC"))
print("token_mcx", _env_set("STRIKE_DESK_TT_AUTH_TOKEN_COA_MCX"))
print("nginx_lab", "lab.zerotouchalgo.com" in nginx)
print("nginx_ltp", "ltp.zerotouchalgo.com" in nginx)
print("nginx_alert", "location = /coa-alert" in nginx)
print("state_mode", oct(STATE.stat().st_mode & 0o777) if STATE.is_dir() else None)
print("coa_alert_html", (STATE / "coa-alert.html").is_file())
now = datetime.now(timezone.utc)
for path in sorted(STATE.glob("public-brief*.json")):
    age = int(now.timestamp() - path.stat().st_mtime)
    print("brief", path.name, "mode", oct(path.stat().st_mode & 0o777), "age_s", age)
gce_boards = (SD / "coa_boards.py").read_text(encoding="utf-8")
print("boards_nifty", "slug='nifty'" in gce_boards or 'slug="nifty"' in gce_boards)
print("boards_bn", "banknifty" in gce_boards)
print("boards_sx", "sensex" in gce_boards)
print("boards_cl", "crudeoil" in gce_boards)
print("boards_ng", "natgas" in gce_boards)
print("boards_btc", "btc-options" in gce_boards)
print("greeks_six", "public-brief-natgas.json" in (OA / "services" / "greeks_lab_coa.py").read_text(encoding="utf-8"))
