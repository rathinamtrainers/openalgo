#!/usr/bin/env python3
"""Overlay Lab frontend dist only. Does not restart openalgo.service."""

from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path

TGZ = Path("/tmp/sd-lab-btc-dist.tgz")
SRC = Path("/tmp/sd-lab-map")
DEST = Path("/opt/openalgo/current/frontend/dist")
MARKER = "Last is inside the wait band"


def main() -> None:
    if SRC.exists():
        shutil.rmtree(SRC)
    SRC.mkdir(parents=True)
    with tarfile.open(TGZ, "r:gz") as archive:
        archive.extractall(SRC)
    src_dist = SRC / "dist"
    if not (src_dist / "index.html").is_file():
        raise SystemExit("missing dist/index.html")
    blob = "".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in src_dist.glob("assets/GreeksLab-*.js")
    )
    if MARKER not in blob:
        raise SystemExit("built dist is missing CoA map copy")
    if "Call-buy" not in blob:
        raise SystemExit("built dist is missing Call-buy")
    if DEST.exists():
        shutil.rmtree(DEST)
    shutil.copytree(src_dist, DEST)
    subprocess.run(["chown", "-R", "openalgo:openalgo", str(DEST)], check=True)
    chunks = sorted((DEST / "assets").glob("GreeksLab-*.js"))
    print("chunks", [path.name for path in chunks])
    subprocess.run(["systemctl", "is-active", "openalgo.service"], check=True)
    print("MAP_DIST_OK")


if __name__ == "__main__":
    main()
