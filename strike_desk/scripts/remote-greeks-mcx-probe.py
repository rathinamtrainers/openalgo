#!/usr/bin/env python3
"""Read recent Greeks Lab MCX errors. No secrets."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def run(cmd: str) -> str:
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return ((p.stdout or "") + (p.stderr or "")).strip()


def main() -> None:
    print("journal")
    print(
        run(
            "journalctl -u openalgo.service --since '30 min ago' --no-pager "
            "| grep -iE 'greeks|GOLDM|option chain|MCX|Failed to' | tail -n 80"
        )
    )
    err = Path("/opt/openalgo/current/log/errors.jsonl")
    if not err.is_file():
        err = Path("/opt/openalgo/log/errors.jsonl")
    print("errors_file", err, err.is_file())
    if err.is_file():
        lines = err.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
        for line in lines:
            low = line.lower()
            if any(token in low for token in ("greeks", "goldm", "option chain", "mcx")):
                try:
                    row = json.loads(line)
                    print("err", row.get("ts"), row.get("logger"), row.get("message", "")[:300])
                except json.JSONDecodeError:
                    print("err_raw", line[:300])
    print("PROBE_OK")


if __name__ == "__main__":
    main()
