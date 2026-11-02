#!/usr/bin/env python3
"""Report whether GCE has the full CoA + Lab setup. Never prints secrets."""

from __future__ import annotations

from pathlib import Path

OA = Path("/opt/openalgo/current")
SD = Path("/opt/strike-desk/current/strike_desk/src/strike_desk")
ENV = Path("/etc/strike-desk/strike-desk.env")
STATE = Path("/var/lib/strike-desk")
NGINX = Path("/etc/nginx/sites-available/desk.zerotouchalgo.com")


def _has(path: Path, needle: str) -> bool:
    return path.is_file() and needle in path.read_text(encoding="utf-8", errors="ignore")


def _env_set(key: str) -> bool:
    if not ENV.is_file():
        return False
    for line in ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith(key + "=") and len(line.split("=", 1)[-1].strip()) > 4:
            return True
    return False


def _env_true(key: str) -> bool:
    if not ENV.is_file():
        return False
    for line in ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[-1].strip().lower() in {"1", "true", "yes"}
    return False


hands = (SD / "tradetron_hands.py").read_text(encoding="utf-8", errors="ignore") if (SD / "tradetron_hands.py").is_file() else ""
boards = (SD / "coa_boards.py").read_text(encoding="utf-8", errors="ignore") if (SD / "coa_boards.py").is_file() else ""
main = (SD / "coa_main.py").read_text(encoding="utf-8", errors="ignore") if (SD / "coa_main.py").is_file() else ""
live = (SD / "ltp_coa_live.py").read_text(encoding="utf-8", errors="ignore") if (SD / "ltp_coa_live.py").is_file() else ""
nginx = NGINX.read_text(encoding="utf-8", errors="ignore") if NGINX.is_file() else ""
index_hits = [
    p.name
    for p in sorted((OA / "frontend" / "dist" / "assets").glob("index-*.js"))
    if "lab.zerotouchalgo.com" in p.read_text(encoding="utf-8", errors="ignore")
]

print("sd", SD.is_dir(), "oa", OA.is_dir())
print("files", {
    "coa_boards": (SD / "coa_boards.py").is_file(),
    "coa_main": (SD / "coa_main.py").is_file(),
    "delta_india": (SD / "delta_india.py").is_file(),
    "greeks_lab_coa": (OA / "services" / "greeks_lab_coa.py").is_file(),
    "greeks_lab_bp": (OA / "blueprints" / "greeks_lab.py").is_file(),
})
print("mcx_hands", "MCX_HANDS_BOARDS" in hands)
print("btc_hands", "BTC_HANDS_BOARDS" in hands)
print("natgas_1250", "contract_qty=1250" in boards or "contract_qty = 1250" in boards)
print("order_quantity", "def order_quantity" in boards)
print("delta_wire", "DeltaIndiaChain" in main)
print("make_mcx", "make_mcx_runners" in live or "make_mcx_runners" in main)
print("coa_enabled", _env_true("STRIKE_DESK_COA_ENABLED"))
print("token_coa", _env_set("STRIKE_DESK_TT_AUTH_TOKEN_COA"))
print("token_btc", _env_set("STRIKE_DESK_TT_AUTH_TOKEN_COA_BTC"))
print("token_mcx", _env_set("STRIKE_DESK_TT_AUTH_TOKEN_COA_MCX"))
print("nginx_lab", "lab.zerotouchalgo.com" in nginx)
print("nginx_ltp", "ltp.zerotouchalgo.com" in nginx)
print("nginx_alert", "location = /coa-alert" in nginx)
print("menu_js", index_hits[:4] or "NONE")
print("greeks_chunks", [p.name for p in sorted((OA / "frontend" / "dist" / "assets").glob("GreeksLab-*.js"))][:4])
print(
    "briefs",
    sorted(p.name for p in STATE.glob("public-brief*.json")),
)
print("coa_alert_html", (STATE / "coa-alert.html").is_file())
