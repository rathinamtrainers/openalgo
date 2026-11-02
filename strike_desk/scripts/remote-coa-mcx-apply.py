#!/usr/bin/env python3
"""Extract Crude/Natgas CoA overlay and install. Root only."""

from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path

TGZ = Path("/tmp/sd-coa-mcx.tgz")
SRC = Path("/tmp/sd-coa-mcx")


def main() -> None:
    if SRC.exists():
        shutil.rmtree(SRC)
    SRC.mkdir(parents=True)
    with tarfile.open(TGZ, "r:gz") as archive:
        archive.extractall(SRC)
    for path in SRC.rglob("*"):
        if path.suffix in {".py", ".html", ".conf"} and path.is_file():
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
    install = SRC / "remote-coa-mcx-install.py"
    if not install.is_file():
        raise SystemExit("missing remote-coa-mcx-install.py")
    subprocess.run(["python3", str(install)], check=True)
    print("APPLY_OK")


if __name__ == "__main__":
    main()
