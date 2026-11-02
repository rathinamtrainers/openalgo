import json
import subprocess
from pathlib import Path

root = Path("/var/lib/strike-desk")
print("=== files ===")
for name in (
    "public-brief.json",
    "public-brief-banknifty.json",
    "public-brief-sensex.json",
    "coa_live.json",
    "coa_live_bn.json",
    "coa_live_sx.json",
):
    p = root / name
    print(name, "OK" if p.exists() else "MISSING", p.stat().st_size if p.exists() else "")

print("=== journal banknifty / skipped / failed ===")
cmd = [
    "journalctl",
    "-u",
    "strike-desk-coa.service",
    "-n",
    "400",
    "--no-pager",
    "--output=cat",
]
proc = subprocess.run(cmd, capture_output=True, text=True)
lines = (proc.stdout or "").splitlines()
keys = ("banknifty", "BANKNIFTY", "skipped", "read failed", "empty chain", "no expiry")
hit = [ln for ln in lines if any(k.lower() in ln.lower() for k in keys)]
for ln in hit[-80:]:
    print(ln[:400])
print("journal_lines", len(lines), "matched", len(hit))

print("=== started ===")
for ln in lines:
    if "CoA live started" in ln or "boards=" in ln:
        print(ln[:400])
