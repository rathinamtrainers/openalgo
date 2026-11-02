#!/usr/bin/env python3
"""Patch ltp_coa_live.py onto GCE CoA and restart strike-desk-coa only."""

from __future__ import annotations

import subprocess
from pathlib import Path

SRC = Path("/tmp/sd-coa-trades/ltp_coa_live.py")
DEST = Path("/opt/strike-desk/current/strike_desk/src/strike_desk/ltp_coa_live.py")


def main() -> None:
    if not SRC.is_file():
        raise SystemExit("missing patched ltp_coa_live.py")
    data = SRC.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    DEST.write_bytes(data)
    DEST.chmod(0o644)
    subprocess.run(["chown", "strikedesk:strikedesk", str(DEST)], check=True)
    text = DEST.read_text(encoding="utf-8")
    if "record_exit" not in text:
        raise SystemExit("record_exit missing")
    if "except ImportError" not in text:
        raise SystemExit("crypto_session_day shim missing")
    subprocess.run(["systemctl", "restart", "strike-desk-coa.service"], check=True)
    subprocess.run(["systemctl", "is-active", "strike-desk-coa.service"], check=True)
    subprocess.run(["systemctl", "is-active", "openalgo.service"], check=True)
    subprocess.run(["systemctl", "is-active", "strike-desk.service"], check=True)
    log = Path("/var/lib/strike-desk/coa-trades.jsonl")
    n = sum(1 for _ in log.open(encoding="utf-8")) if log.is_file() else 0
    print("log_lines", n)
    print("COA_TRADES_FIX_OK")


if __name__ == "__main__":
    main()
