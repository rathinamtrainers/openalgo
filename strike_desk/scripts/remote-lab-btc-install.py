#!/usr/bin/env python3
"""Overlay Lab BTC isolation. Restarts openalgo.service only.

Does not restart strike-desk.service or strike-desk-coa.service.
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path

TGZ = Path("/tmp/sd-lab-btc-dist.tgz")
SRC = Path("/tmp/sd-lab-btc")
OA = Path("/opt/openalgo/current")
DEST = OA / "frontend" / "dist"
COA_SRC = Path("/tmp/sd-lab-btc-coa/greeks_lab_coa.py")
COA_DEST = OA / "services" / "greeks_lab_coa.py"
MARKER = "no crypto option chain"


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _unix(src: Path, dest: Path, owner: str) -> None:
    dest.write_bytes(src.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
    dest.chmod(0o644)
    _run(["chown", f"{owner}:{owner}", str(dest)])
    print("wrote", dest)


def main() -> None:
    if SRC.exists():
        shutil.rmtree(SRC)
    SRC.mkdir(parents=True)
    with tarfile.open(TGZ, "r:gz") as archive:
        archive.extractall(SRC)
    src_dist = SRC / "dist"
    if not src_dist.is_dir():
        raise SystemExit("missing dist in tarball")
    if not (src_dist / "index.html").is_file():
        raise SystemExit("missing dist/index.html")
    blob = " ".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in src_dist.glob("assets/GreeksLab-*.js")
    )
    if MARKER not in blob:
        raise SystemExit("built dist is missing BTC Lab isolation")
    if "lab.zerotouchalgo.com" not in " ".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in src_dist.glob("assets/index-*.js")
    ):
        raise SystemExit("built dist is missing Lab profile links")
    if not COA_SRC.is_file():
        raise SystemExit("missing greeks_lab_coa.py")
    coa_text = COA_SRC.read_text(encoding="utf-8")
    if '"mood"' not in coa_text or '"disclaimer"' not in coa_text:
        raise SystemExit("greeks_lab_coa.py missing mood/disclaimer keys")
    if DEST.exists():
        shutil.rmtree(DEST)
    shutil.copytree(src_dist, DEST)
    _run(["chown", "-R", "openalgo:openalgo", str(DEST)])
    _unix(COA_SRC, COA_DEST, "openalgo")
    _run(["systemctl", "restart", "openalgo.service"])
    _run(["systemctl", "is-active", "openalgo.service"])
    _run(["systemctl", "is-active", "strike-desk-coa.service"])
    _run(["systemctl", "is-active", "strike-desk.service"])
    chunks = sorted((DEST / "assets").glob("GreeksLab-*.js"))
    print("chunks", [p.name for p in chunks])
    print("LAB_BTC_DIST_OK")


if __name__ == "__main__":
    main()
