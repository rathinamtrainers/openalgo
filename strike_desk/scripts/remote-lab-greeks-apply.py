#!/usr/bin/env python3
"""Extract the Lab overlay tarball and run the installer. Root only."""

from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path

TGZ = Path("/tmp/sd-lab-greeks.tgz")
SRC = Path("/tmp/sd-lab")


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main() -> None:
    if SRC.exists():
        shutil.rmtree(SRC)
    SRC.mkdir(parents=True)
    with tarfile.open(TGZ, "r:gz") as archive:
        archive.extractall(SRC)
    for path in SRC.glob("*.py"):
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
    if not (SRC / "greeks_lab_coa.py").is_file():
        raise SystemExit("missing greeks_lab_coa.py")
    if not (SRC / "option_chain_service.py").is_file():
        raise SystemExit("missing option_chain_service.py")
    if not (SRC / "dist").is_dir():
        raise SystemExit("missing dist")
    _run(["python3", str(SRC / "remote-lab-greeks-install.py")])
    dest = Path("/opt/openalgo/current/services/greeks_lab_coa.py")
    if not dest.is_file():
        raise SystemExit("install did not copy greeks_lab_coa.py")
    chunks = sorted(Path("/opt/openalgo/current/frontend/dist/assets").glob("GreeksLab-*.js"))
    print("chunks", [p.name for p in chunks])
    _run(["systemctl", "is-active", "openalgo.service"])
    _run(["systemctl", "is-active", "strike-desk-coa.service"])
    print("APPLY_OK")


if __name__ == "__main__":
    main()
