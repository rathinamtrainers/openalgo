#!/usr/bin/env python3
"""Follow-up: GOLDM quote path and nearby journal lines."""

from __future__ import annotations

import subprocess


def run(cmd: str) -> str:
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return ((p.stdout or "") + (p.stderr or "")).strip()


def main() -> None:
    print(
        run(
            "journalctl -u openalgo.service --since '14:23:00' --until '14:26:00' --no-pager "
            "| grep -iE 'GOLDM|Failed to fetch|greeks_lab|option_chain_service|quotes' | tail -n 60"
        )
    )
    print("--- later ---")
    print(
        run(
            "journalctl -u openalgo.service --since '14:23:00' --no-pager "
            "| grep -i GOLDM | tail -n 40"
        )
    )
    print("PROBE2_OK")


if __name__ == "__main__":
    main()
