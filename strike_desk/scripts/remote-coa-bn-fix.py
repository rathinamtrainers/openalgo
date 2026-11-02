"""Overlay Bank Nifty listed-expiry fix. Restarts CoA only, not StrikeShield."""

from pathlib import Path
import shutil
import subprocess

SRC = Path("/tmp/sd-coa")
DEST = Path("/opt/strike-desk/current/strike_desk/src/strike_desk")
FILES = (
    "coa_boards.py",
    "ltp_coa.py",
    "ltp_coa_intel.py",
    "ltp_coa_live.py",
    "__main__.py",
)


def main() -> None:
    for name in FILES:
        src = SRC / name
        if not src.exists():
            raise SystemExit(f"missing {src}")
        dest = DEST / name
        shutil.copyfile(src, dest)
        dest.chmod(0o644)
        print("wrote", dest, dest.stat().st_size)
    subprocess.run(["systemctl", "restart", "strike-desk-coa.service"], check=True)
    subprocess.run(["systemctl", "is-active", "strike-desk-coa.service"], check=True)
    subprocess.run(["systemctl", "is-active", "strike-desk.service"], check=True)
    print("coa restarted; strike-desk left running")


if __name__ == "__main__":
    main()
