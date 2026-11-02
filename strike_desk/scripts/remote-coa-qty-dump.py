#!/usr/bin/env python3
from pathlib import Path

for name in ("coa_live_ng.json", "public-brief-natgas.json"):
    path = Path("/var/lib/strike-desk") / name
    print("====", name, path.exists())
    if path.is_file():
        print(path.read_text(encoding="utf-8")[:2500])
