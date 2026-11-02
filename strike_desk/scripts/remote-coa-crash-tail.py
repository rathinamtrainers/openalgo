#!/usr/bin/env python3
import subprocess

out = subprocess.check_output(
    ["journalctl", "-u", "strike-desk-coa.service", "-n", "80", "--no-pager", "-o", "cat"],
    text=True,
    errors="replace",
)
print(out[-4000:])
