#!/usr/bin/env python3
"""Match journal.py to the overlaid errors.py. Restarts CoA only."""

from __future__ import annotations

import subprocess
from pathlib import Path

SRC = Path("/tmp/sd-journal.py")
DEST = Path("/opt/strike-desk/current/strike_desk/src/strike_desk/journal.py")


def main() -> None:
    if not SRC.is_file():
        raise SystemExit("missing /tmp/sd-journal.py")
    DEST.write_bytes(SRC.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
    DEST.chmod(0o644)
    subprocess.run(["chown", "strikedesk:strikedesk", str(DEST)], check=True)
    text = DEST.read_text(encoding="utf-8")
    if "AlreadyJournalled" in text:
        raise SystemExit("journal still imports AlreadyJournalled")
    subprocess.run(["systemctl", "restart", "strike-desk-coa.service"], check=True)


if __name__ == "__main__":
    main()
