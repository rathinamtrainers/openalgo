#!/usr/bin/env python3
import subprocess

text = subprocess.check_output(
    [
        "journalctl",
        "-u",
        "strike-desk-coa.service",
        "--since",
        "2026-10-30 09:00:00",
        "--until",
        "2026-10-30 16:00:00",
        "--no-pager",
        "-o",
        "cat",
    ],
    text=True,
    errors="replace",
)
needles = ("coa analyzer", "BUY_CE", "BUY_PE", "coa live sensex", "coa live banknifty")
for line in text.splitlines():
    if any(n in line for n in needles):
        print(line[:320])
