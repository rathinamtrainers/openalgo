#!/usr/bin/env python3
"""See whether the served dist has Lab/LTP profile links."""

from __future__ import annotations

from pathlib import Path

DIST = Path("/opt/openalgo/current/frontend/dist")
assets = DIST / "assets"
print("dist", DIST.is_dir(), "index", (DIST / "index.html").is_file())
hits = []
if assets.is_dir():
    for path in sorted(assets.glob("*.js")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "lab.zerotouchalgo.com" in text or "LTP Board" in text:
            hits.append(path.name)
print("menu_js", hits[:8] if hits else "NONE")
print("greeks_chunks", [p.name for p in sorted(assets.glob("GreeksLab-*.js"))][:6])
