#!/usr/bin/env python3
"""Overlay CoA blotter high-watermark + Lab daily/cum UI.

Restarts strike-desk-coa only. Does not restart openalgo or strike-desk.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

SRC = Path("/tmp/sd-coa-trades")
COA = Path("/opt/strike-desk/current/strike_desk/src/strike_desk")
OA = Path("/opt/openalgo/current")
TGZ = Path("/tmp/sd-lab-btc-dist.tgz")
MAP = Path("/tmp/sd-lab-map")
FILES = ("ltp_coa_trades.py", "ltp_coa_live.py", "ltp_coa.py")
OA_FILE = "greeks_lab_coa.py"


def _unix(src: Path, dest: Path) -> None:
    dest.write_bytes(src.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
    dest.chmod(0o644)
    print("wrote", dest)


def _dist() -> None:
    if MAP.exists():
        shutil.rmtree(MAP)
    MAP.mkdir(parents=True)
    with tarfile.open(TGZ, "r:gz") as archive:
        archive.extractall(MAP)
    src_dist = MAP / "dist"
    if not (src_dist / "index.html").is_file():
        raise SystemExit("missing dist/index.html")
    blob = "".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in src_dist.glob("assets/GreeksLab-*.js")
    )
    if "Cumulative" not in blob or "Margin" not in blob:
        raise SystemExit("built dist is missing Daily/Cumulative/Margin")
    dest = OA / "frontend" / "dist"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src_dist, dest)
    subprocess.run(["chown", "-R", "openalgo:openalgo", str(dest)], check=True)


def main() -> None:
    dist_only = "--dist-only" in sys.argv
    if dist_only:
        _dist()
        print("COA_DIST_OK")
        return
    if not COA.is_dir():
        raise SystemExit(f"missing {COA}")
    for name in FILES:
        src = SRC / name
        if not src.is_file():
            raise SystemExit(f"missing {src}")
        dest = COA / name
        _unix(src, dest)
        subprocess.run(["chown", "strikedesk:strikedesk", str(dest)], check=True)
    live = (COA / "ltp_coa_live.py").read_text(encoding="utf-8")
    if "record_mark" not in live or "_call_supported" not in live:
        raise SystemExit("ltp_coa_live missing record_mark")
    trades = (COA / "ltp_coa_trades.py").read_text(encoding="utf-8")
    if "def record_mark" not in trades or "margin_used" not in trades:
        raise SystemExit("ltp_coa_trades missing margin_used")
    coa = (COA / "ltp_coa.py").read_text(encoding="utf-8")
    if "def extension_level" not in coa or "def apply_premium_extension" not in coa:
        raise SystemExit("ltp_coa missing premium EOS/EOR")
    oa_src = SRC / OA_FILE
    if oa_src.is_file() and "--python-only" not in sys.argv:
        dest = OA / "services" / OA_FILE
        _unix(oa_src, dest)
        subprocess.run(["chown", "openalgo:openalgo", str(dest)], check=True)
    if "--python-only" not in sys.argv:
        _dist()
    subprocess.run(["systemctl", "restart", "strike-desk-coa.service"], check=True)
    subprocess.run(["systemctl", "is-active", "strike-desk-coa.service"], check=True)
    subprocess.run(["systemctl", "is-active", "openalgo.service"], check=True)
    subprocess.run(["systemctl", "is-active", "strike-desk.service"], check=True)
    print("COA_PNL_OVERLAY_OK")


if __name__ == "__main__":
    main()
