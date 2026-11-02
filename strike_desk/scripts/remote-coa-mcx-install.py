#!/usr/bin/env python3
"""Install Crude/Natgas CoA boards. Restarts strike-desk-coa only, not strike-desk."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

SRC = Path("/tmp/sd-coa-mcx")
DEST = Path("/opt/strike-desk/current/strike_desk/src/strike_desk")
OA = Path("/opt/openalgo/current")
ALERT_DEST = Path("/var/lib/strike-desk/coa-alert.html")
NGINX_PATHS = [
    Path("/etc/nginx/sites-enabled/desk.zerotouchalgo.com"),
    Path("/etc/nginx/sites-available/desk.zerotouchalgo.com"),
]
TELE_BOT = Path("/opt/zta-desk/zta_telegram_bot.py")
TELE_CFG = Path("/opt/zta-desk/config.env")
PY_FILES = (
    "coa_boards.py",
    "coa_main.py",
    "ltp_coa_live.py",
    "ltp_coa_exits.py",
    "tradetron_hands.py",
    "session.py",
    "__main__.py",
)

BRIEF_BLOCK = """
    location = /public-brief-crudeoil.json {
        alias /var/lib/strike-desk/public-brief-crudeoil.json;
        default_type application/json;
        add_header Cache-Control "no-store";
        add_header Access-Control-Allow-Origin "https://zerotouchalgo.com";
        add_header Access-Control-Allow-Methods "GET, OPTIONS";
    }

    location = /public-brief-natgas.json {
        alias /var/lib/strike-desk/public-brief-natgas.json;
        default_type application/json;
        add_header Cache-Control "no-store";
        add_header Access-Control-Allow-Origin "https://zerotouchalgo.com";
        add_header Access-Control-Allow-Methods "GET, OPTIONS";
    }
"""


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _copy_py() -> None:
    for name in PY_FILES:
        src = SRC / name
        if not src.is_file():
            raise SystemExit(f"missing {name}")
        dest = DEST / name
        shutil.copyfile(src, dest)
        dest.chmod(0o644)
        print("wrote", dest)


def _copy_alert() -> None:
    src = SRC / "coa-alert.html"
    if not src.is_file():
        print("no coa-alert.html; skip")
        return
    shutil.copyfile(src, ALERT_DEST)
    ALERT_DEST.chmod(0o644)
    print("wrote", ALERT_DEST)


def _patch_nginx() -> None:
    seen: set[Path] = set()
    for path in NGINX_PATHS:
        if not path.exists():
            continue
        real = path.resolve()
        if real in seen:
            continue
        seen.add(real)
        text = path.read_text(encoding="utf-8")
        if "public-brief-crudeoil.json" in text and "public-brief-natgas.json" in text:
            print("nginx already has crude/natgas briefs", path)
            continue
        ssl_at = text.find("listen 443")
        loc = text.find("    location / {", ssl_at if ssl_at >= 0 else 0)
        if loc < 0:
            loc = text.find("    location = /ws {")
        if loc < 0:
            raise SystemExit("cannot insert nginx briefs")
        path.write_text(text[:loc] + BRIEF_BLOCK + "\n" + text[loc:], encoding="utf-8")
        print("patched nginx", path)
    _run(["nginx", "-t"])
    _run(["systemctl", "reload", "nginx"])


def _patch_telegram() -> None:
    if TELE_CFG.is_file():
        text = TELE_CFG.read_text(encoding="utf-8")
        old = "BOARDS=nifty,banknifty,sensex,btc-options"
        new = "BOARDS=nifty,banknifty,sensex,crudeoil,natgas,btc-options"
        if new not in text:
            if old in text:
                TELE_CFG.write_text(text.replace(old, new, 1), encoding="utf-8")
                print("patched telegram BOARDS")
            elif "BOARDS=" in text:
                lines = []
                for line in text.splitlines():
                    if line.startswith("BOARDS="):
                        lines.append(new)
                    else:
                        lines.append(line)
                TELE_CFG.write_text("\n".join(lines) + "\n", encoding="utf-8")
                print("replaced telegram BOARDS")
        else:
            print("telegram BOARDS already include mcx")
    src_bot = SRC / "zta_telegram_bot.py"
    if src_bot.is_file() and TELE_BOT.parent.is_dir():
        shutil.copyfile(src_bot, TELE_BOT)
        TELE_BOT.chmod(0o644)
        print("wrote", TELE_BOT)


def _copy_lab_coa() -> None:
    src = SRC / "greeks_lab_coa.py"
    if not src.is_file():
        print("no greeks_lab_coa.py; skip openalgo")
        return
    dest = OA / "services" / "greeks_lab_coa.py"
    shutil.copyfile(src, dest)
    dest.chmod(0o644)
    print("wrote", dest)
    _run(["systemctl", "restart", "openalgo.service"])


def main() -> None:
    if not DEST.is_dir():
        raise SystemExit(f"missing {DEST}")
    _copy_py()
    _copy_alert()
    _patch_nginx()
    _patch_telegram()
    _copy_lab_coa()
    _run(["systemctl", "restart", "strike-desk-coa.service"])
    if Path("/etc/systemd/system/zta-desk-telegram.service").exists():
        _run(["systemctl", "restart", "zta-desk-telegram.service"])
    _run(["systemctl", "is-active", "strike-desk-coa.service"])
    _run(["systemctl", "is-active", "strike-desk.service"])
    _run(["systemctl", "is-active", "openalgo.service"])
    print("MCX_COA_OK")


if __name__ == "__main__":
    main()
