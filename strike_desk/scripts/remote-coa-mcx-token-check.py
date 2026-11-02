#!/usr/bin/env python3
from pathlib import Path

env = Path("/etc/strike-desk/strike-desk.env")
text = env.read_text(encoding="utf-8") if env.exists() else ""
hands = Path("/opt/strike-desk/current/strike_desk/src/strike_desk/tradetron_hands.py").read_text(encoding="utf-8")
main = Path("/opt/strike-desk/current/strike_desk/src/strike_desk/coa_main.py").read_text(encoding="utf-8")
print("gce_coa_mcx_env", any(
    line.startswith("STRIKE_DESK_TT_AUTH_TOKEN_COA_MCX=") and len(line.split("=", 1)[-1].strip()) > 8
    for line in text.splitlines()
))
print("gce_has_MCX_HANDS_BOARDS", "MCX_HANDS_BOARDS" in hands)
print("gce_cash_includes_natgas", '"natgas"' in hands.split("CASH_HANDS_BOARDS", 1)[-1][:200])
print("gce_coa_main_mcx_token", "tt_auth_token_coa_mcx" in main)
