#!/usr/bin/env python3
"""Install broker-reconnect loop fix. Restarts openalgo.service only."""

from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path

OA = Path("/opt/openalgo/current")
SRC = Path("/tmp/sd-broker-reconnect")
TGZ = Path("/tmp/sd-lab-btc-dist.tgz")
DIST_SRC = Path("/tmp/sd-lab-btc")
DEST = OA / "frontend" / "dist"
FILES = {
    "brlogin.py": OA / "blueprints" / "brlogin.py",
    "dashboard.py": OA / "blueprints" / "dashboard.py",
}


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _unix(src: Path, dest: Path) -> None:
    dest.write_bytes(src.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
    dest.chmod(0o644)
    _run(["chown", "openalgo:openalgo", str(dest)])
    print("wrote", dest)


def main() -> None:
    for name, dest in FILES.items():
        src = SRC / name
        if not src.is_file():
            raise SystemExit(f"missing {src}")
        text = src.read_text(encoding="utf-8")
        if name == "brlogin.py" and "def reconnect_action" not in text:
            raise SystemExit("brlogin.py missing reconnect_action")
        if name == "dashboard.py" and "broker reconnect" not in text:
            raise SystemExit("dashboard.py missing reconnect redirect")
        _unix(src, dest)
    live = (OA / "blueprints" / "brlogin.py").read_text(encoding="utf-8")
    if "def reconnect_action" not in live:
        raise SystemExit("installed brlogin.py missing helper")
    if TGZ.is_file():
        if DIST_SRC.exists():
            shutil.rmtree(DIST_SRC)
        DIST_SRC.mkdir(parents=True)
        with tarfile.open(TGZ, "r:gz") as archive:
            archive.extractall(DIST_SRC)
        src_dist = DIST_SRC / "dist"
        if not (src_dist / "index.html").is_file():
            raise SystemExit("missing dist/index.html")
        if DEST.exists():
            shutil.rmtree(DEST)
        shutil.copytree(src_dist, DEST)
        _run(["chown", "-R", "openalgo:openalgo", str(DEST)])
        print("dist", sorted((DEST / "assets").glob("Dashboard-*.js")))
    _run(["systemctl", "restart", "openalgo.service"])
    _run(["systemctl", "is-active", "openalgo.service"])
    _run(["systemctl", "is-active", "strike-desk-coa.service"])
    _run(["systemctl", "is-active", "strike-desk.service"])
    print("BROKER_RECONNECT_OK")


if __name__ == "__main__":
    main()
