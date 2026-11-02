#!/usr/bin/env python3
"""Install MCX CoA Hands routing + token. Restarts strike-desk-coa only."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

SRC = Path("/tmp/sd-coa-mcx-hands")
DEST = Path("/opt/strike-desk/current/strike_desk/src/strike_desk")
ENV = Path("/etc/strike-desk/strike-desk.env")
TOKEN_FILE = Path("/tmp/sd-coa-mcx-hands/tt-coa-mcx.token")
FILES = (
    "config.py",
    "tradetron_hands.py",
    "coa_main.py",
    "__main__.py",
)


def _copy_py() -> None:
    for name in FILES:
        src = SRC / name
        if not src.is_file():
            raise SystemExit(f"missing {src}")
        data = src.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        dest = DEST / name
        dest.write_bytes(data)
        dest.chmod(0o644)
        print("wrote", dest)
    hands = (DEST / "tradetron_hands.py").read_text(encoding="utf-8")
    if "MCX_HANDS_BOARDS" not in hands:
        raise SystemExit("tradetron_hands missing MCX_HANDS_BOARDS")
    cfg = (DEST / "config.py").read_text(encoding="utf-8")
    if "tt_auth_token_coa_mcx" not in cfg:
        raise SystemExit("config missing tt_auth_token_coa_mcx")


def _set_token() -> None:
    token = TOKEN_FILE.read_text(encoding="utf-8").replace("\r", "").replace("\n", "").strip()
    if not token:
        raise SystemExit("empty MCX CoA token")
    lines = ENV.read_text(encoding="utf-8").splitlines()
    key = "STRIKE_DESK_TT_AUTH_TOKEN_COA_MCX"
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
        raise SystemExit("refusing: MCX token matches NSE/BSE CoA token")
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("mcx_env_set")


def main() -> None:
    if not DEST.is_dir():
        raise SystemExit(f"missing {DEST}")
    _copy_py()
    _set_token()
    subprocess.run(["systemctl", "restart", "strike-desk-coa.service"], check=True)
    subprocess.run(["systemctl", "is-active", "strike-desk-coa.service"], check=True)
    subprocess.run(["systemctl", "is-active", "strike-desk.service"], check=True)
    print("MCX_HANDS_OK")


if __name__ == "__main__":
    main()
