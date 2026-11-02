#!/usr/bin/env python3
"""Go live: multi-board Telegram posts. Never print tokens. Never restart strike-desk."""

from __future__ import annotations

import grp
import os
import pwd
import shutil
import subprocess
from pathlib import Path

SRC = Path("/tmp/zta-telegram-desk")
BOT = Path("/opt/zta-desk/zta_telegram_bot.py")
UNIT = Path("/etc/systemd/system/zta-desk-telegram.service")
CFG = Path("/opt/zta-desk/config.env")

PATCH = {
    "DRY_RUN": "false",
    "BOARDS": "nifty,banknifty,sensex,crudeoil,natgas,btc-options",
    "ALWAYS_OPEN_BOARDS": "btc-options",
    "BRIEF_DIR": "/var/lib/strike-desk",
    "STALE_SECONDS": "180",
}


def run(cmd: str) -> str:
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    if p.returncode != 0:
        raise SystemExit(f"cmd failed {p.returncode}: {cmd}\n{out[:500]}")
    return out


def patch_config() -> None:
    text = CFG.read_text(encoding="utf-8")
    lines = []
    seen: set[str] = set()
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in line:
            lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in PATCH:
            lines.append(f"{key}={PATCH[key]}")
            seen.add(key)
        else:
            lines.append(line)
    for key, val in PATCH.items():
        if key not in seen:
            lines.append(f"{key}={val}")
    CFG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    uid = pwd.getpwnam("zta-desk").pw_uid
    gid = grp.getgrnam("zta-desk").gr_gid
    os.chown(CFG, uid, gid)
    os.chmod(CFG, 0o600)


def main() -> None:
    src_bot = SRC / "zta_telegram_bot.py"
    src_unit = SRC / "zta-desk-telegram.service"
    if not src_bot.exists() or not src_unit.exists():
        raise SystemExit(f"missing overlay under {SRC}")
    shutil.copyfile(src_bot, BOT)
    shutil.copyfile(src_unit, UNIT)
    os.chmod(BOT, 0o644)
    os.chmod(UNIT, 0o644)
    patch_config()

    keys = {}
    for line in CFG.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.strip().startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        if key.strip() in PATCH or key.strip() in {"LIVE_URL", "TELEGRAM_CHAT_ID"}:
            if "TOKEN" in key.upper() or key.strip() == "OWNER_CHAT_ID":
                continue
            keys[key.strip()] = val.strip()
    print("config", keys)

    run("systemctl daemon-reload")
    # Telegram unit only. CoA / strike-desk stay up.
    run("systemctl restart zta-desk-telegram.service")
    import time

    time.sleep(2)
    print("telegram", run("systemctl is-active zta-desk-telegram.service"))
    print("coa", run("systemctl is-active strike-desk-coa.service"))
    print("strike-desk", run("systemctl is-active strike-desk.service"))

    test = subprocess.run(
        [
            "sudo",
            "-u",
            "zta-desk",
            "bash",
            "-lc",
            "set -a && . /opt/zta-desk/config.env && set +a && python3 /opt/zta-desk/zta_telegram_bot.py --test-post",
        ],
        capture_output=True,
        text=True,
    )
    print("test_post", (test.stdout or test.stderr or "").strip()[:200], "rc", test.returncode)
    print("GO_LIVE_OK")


if __name__ == "__main__":
    main()
