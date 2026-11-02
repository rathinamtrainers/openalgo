#!/usr/bin/env python3
"""Install MCX CoA Analyze lot sizes. Restarts strike-desk-coa only."""

from __future__ import annotations

from pathlib import Path
import subprocess

SRC = Path("/tmp/sd-coa-qty")
DEST = Path("/opt/strike-desk/current/strike_desk/src/strike_desk")
FILES = ("coa_boards.py", "ltp_coa_live.py")


def main() -> None:
    if not DEST.is_dir():
        raise SystemExit(f"missing {DEST}")
    for name in FILES:
        src = SRC / name
        if not src.is_file():
            raise SystemExit(f"missing {src}")
        data = src.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        dest = DEST / name
        dest.write_bytes(data)
        dest.chmod(0o644)
        print("wrote", dest)
    text = (DEST / "coa_boards.py").read_text(encoding="utf-8")
    if "contract_qty=1250" not in text or "contract_qty=100" not in text:
        raise SystemExit("contract_qty missing after copy")
    if "def order_quantity" not in text:
        raise SystemExit("order_quantity missing after copy")
    live = (DEST / "ltp_coa_live.py").read_text(encoding="utf-8")
    if "order_quantity" not in live:
        raise SystemExit("ltp_coa_live not using order_quantity")
    subprocess.run(["systemctl", "restart", "strike-desk-coa.service"], check=True)
    subprocess.run(["systemctl", "is-active", "strike-desk-coa.service"], check=True)
    subprocess.run(["systemctl", "is-active", "strike-desk.service"], check=True)
    print("QTY_OK")


if __name__ == "__main__":
    main()
