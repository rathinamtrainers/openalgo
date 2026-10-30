#!/usr/bin/env python3
"""Replace OpenAlgo frontend dist with the Lab/LTP profile menu build.

Restarts openalgo.service only. Does not restart strike-desk or CoA.
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path

TGZ = Path("/tmp/sd-lab-menu-dist.tgz")
SRC = Path("/tmp/sd-lab-menu")
OA = Path("/opt/openalgo/current")
DEST = OA / "frontend" / "dist"


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main() -> None:
    if SRC.exists():
        shutil.rmtree(SRC)
    SRC.mkdir(parents=True)
    with tarfile.open(TGZ, "r:gz") as archive:
        archive.extractall(SRC)
    src_dist = SRC / "dist"
    if not src_dist.is_dir():
        raise SystemExit("missing dist in tarball")
    index = src_dist / "index.html"
    if not index.is_file():
        raise SystemExit("missing dist/index.html")
    blob = " ".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in src_dist.glob("assets/index-*.js")
    )
    if "lab.zerotouchalgo.com" not in blob or "LTP Board" not in blob:
        raise SystemExit("built dist is missing Lab/LTP profile links")
    if DEST.exists():
        shutil.rmtree(DEST)
    shutil.copytree(src_dist, DEST)
    _run(["chown", "-R", "openalgo:openalgo", str(DEST)])
    _run(["systemctl", "restart", "openalgo.service"])
    _run(["systemctl", "is-active", "openalgo.service"])
    _run(["systemctl", "is-active", "strike-desk-coa.service"])
    chunks = sorted((DEST / "assets").glob("GreeksLab-*.js"))
    print("chunks", [p.name for p in chunks])
    print("MENU_DIST_OK")


if __name__ == "__main__":
    main()
