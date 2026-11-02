#!/usr/bin/env python3
"""Overlay the full CoA + Lab setup. Restarts CoA and OpenAlgo only.

Does not restart strike-desk.service. Does not print tokens.
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path

TGZ = Path("/tmp/sd-full-setup.tgz")
SRC = Path("/tmp/sd-full-setup")
SD = Path("/opt/strike-desk/current/strike_desk/src/strike_desk")
OA = Path("/opt/openalgo/current")
STATE = Path("/var/lib/strike-desk")
NGINX = Path("/etc/nginx/sites-available/desk.zerotouchalgo.com")

SD_FILES = (
    "ltp_coa.py",
    "ltp_coa_exits.py",
    "ltp_coa_brief.py",
    "ltp_coa_replay.py",
    "ltp_coa_live.py",
    "ltp_coa_intel.py",
    "ltp_coa_narrate.py",
    "tradetron_hands.py",
    "coa_analyzer.py",
    "coa_main.py",
    "coa_boards.py",
    "delta_india.py",
    "config.py",
    "__main__.py",
    "openalgo_client.py",
    "session.py",
    "errors.py",
)
OA_SERVICES = (
    "greeks_lab_coa.py",
    "greeks_lab_insights.py",
    "greeks_lab_narrate.py",
    "greeks_lab_service.py",
    "option_chain_service.py",
    "option_symbol_service.py",
)


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _unix(src: Path, dest: Path, owner: str) -> None:
    dest.write_bytes(src.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
    dest.chmod(0o644)
    _run(["chown", f"{owner}:{owner}", str(dest)])
    print("wrote", dest)


def _extract() -> None:
    if SRC.exists():
        shutil.rmtree(SRC)
    SRC.mkdir(parents=True)
    with tarfile.open(TGZ, "r:gz") as archive:
        archive.extractall(SRC)


def _copy_strike_desk() -> None:
    for name in SD_FILES:
        src = SRC / "strike_desk" / name
        if not src.is_file():
            raise SystemExit(f"missing {src}")
        _unix(src, SD / name, "strikedesk")
    hands = (SD / "tradetron_hands.py").read_text(encoding="utf-8")
    boards = (SD / "coa_boards.py").read_text(encoding="utf-8")
    main = (SD / "coa_main.py").read_text(encoding="utf-8")
    if "MCX_HANDS_BOARDS" not in hands:
        raise SystemExit("tradetron_hands missing MCX_HANDS_BOARDS")
    if "BTC_HANDS_BOARDS" not in hands:
        raise SystemExit("tradetron_hands missing BTC_HANDS_BOARDS")
    if "def order_quantity" not in boards:
        raise SystemExit("coa_boards missing order_quantity")
    if "DeltaIndiaChain" not in main:
        raise SystemExit("coa_main missing DeltaIndiaChain")
    alert = SRC / "strike_desk" / "coa-alert.html"
    if alert.is_file():
        dest = STATE / "coa-alert.html"
        dest.write_bytes(alert.read_bytes().replace(b"\r\n", b"\n"))
        dest.chmod(0o644)
        _run(["chown", "strikedesk:strikedesk", str(dest)])
        print("wrote", dest)


def _copy_openalgo() -> None:
    for name in OA_SERVICES:
        src = SRC / "openalgo" / name
        if not src.is_file():
            raise SystemExit(f"missing {src}")
        _unix(src, OA / "services" / name, "openalgo")
    bp = SRC / "openalgo" / "greeks_lab.py"
    if not bp.is_file():
        raise SystemExit("missing greeks_lab.py")
    _unix(bp, OA / "blueprints" / "greeks_lab.py", "openalgo")
    app = (OA / "app.py").read_text(encoding="utf-8")
    if "greeks_lab_bp" not in app:
        raise SystemExit("app.py missing greeks_lab_bp — Lab route not registered")
    react = (OA / "blueprints" / "react_app.py").read_text(encoding="utf-8")
    if 'route("/greekslab")' not in react:
        raise SystemExit("react_app.py missing /greekslab")


def _briefs_readable() -> None:
    if STATE.is_dir():
        STATE.chmod(0o755)
    for path in STATE.glob("public-brief*.json"):
        path.chmod(0o644)
        print("brief", path.name)
    ng = STATE / "coa_live_ng.json"
    if ng.is_file():
        _run(["chown", "strikedesk:strikedesk", str(ng)])
        ng.chmod(0o644)


def _patch_nginx() -> None:
    if not NGINX.is_file():
        print("nginx vhost missing; skip")
        return
    text = NGINX.read_text(encoding="utf-8")
    changed = False
    if "lab.zerotouchalgo.com" not in text:
        text = text.replace(
            "server_name desk.zerotouchalgo.com;",
            "server_name desk.zerotouchalgo.com lab.zerotouchalgo.com ltp.zerotouchalgo.com;",
        )
        changed = True
    if "ltp.zerotouchalgo.com" not in text and "lab.zerotouchalgo.com" in text:
        text = text.replace(
            "server_name desk.zerotouchalgo.com lab.zerotouchalgo.com;",
            "server_name desk.zerotouchalgo.com lab.zerotouchalgo.com ltp.zerotouchalgo.com;",
        )
        changed = True
    rewrite = """
    if ($host = lab.zerotouchalgo.com) {
        rewrite ^/$ /greekslab redirect;
    }
    if ($host = ltp.zerotouchalgo.com) {
        rewrite ^/$ /greekslab redirect;
    }
"""
    if "rewrite ^/$ /greekslab" not in text:
        marker = "    location = /ws {"
        if marker not in text:
            raise SystemExit("cannot insert lab rewrite")
        text = text.replace(marker, rewrite + "\n" + marker, 1)
        changed = True
    elif "ltp.zerotouchalgo.com) {" not in text:
        text = text.replace(
            """    if ($host = lab.zerotouchalgo.com) {
        rewrite ^/$ /greekslab redirect;
    }
""",
            rewrite,
            1,
        )
        changed = True
    if "location = /coa-alert" not in text:
        block = """
    location = /coa-alert {
        alias /var/lib/strike-desk/coa-alert.html;
        default_type text/html;
        add_header Cache-Control "no-store";
    }
"""
        ssl_at = text.find("listen 443")
        loc = text.find("    location / {", ssl_at)
        if loc < 0:
            raise SystemExit("cannot insert coa-alert")
        text = text[:loc] + block + "\n" + text[loc:]
        changed = True
    if not changed:
        print("nginx already has lab/ltp")
        return
    NGINX.write_text(text, encoding="utf-8")
    enabled = Path("/etc/nginx/sites-enabled/desk.zerotouchalgo.com")
    if enabled.exists() and enabled.resolve() != NGINX.resolve():
        enabled.write_text(text, encoding="utf-8")
    _run(["nginx", "-t"])
    _run(["systemctl", "reload", "nginx"])
    print("nginx patched")


def main() -> None:
    if not SD.is_dir() or not OA.is_dir():
        raise SystemExit("missing OpenAlgo or Strike Desk tree")
    _extract()
    _copy_strike_desk()
    _copy_openalgo()
    _briefs_readable()
    _patch_nginx()
    _run(["systemctl", "restart", "strike-desk-coa.service"])
    _run(["systemctl", "restart", "openalgo.service"])
    _run(["systemctl", "is-active", "strike-desk-coa.service"])
    _run(["systemctl", "is-active", "openalgo.service"])
    _run(["systemctl", "is-active", "strike-desk.service"])
    print("FULL_SETUP_OK")


if __name__ == "__main__":
    main()
