#!/usr/bin/env python3
"""Install BotFather token into config.env. Never print the token."""

from __future__ import annotations

import grp
import os
import pwd
import subprocess
import time
from pathlib import Path

TOKEN_FILE = Path("/tmp/zta-tg.token")
CFG = Path("/opt/zta-desk/config.env")


def main() -> None:
    token = TOKEN_FILE.read_text(encoding="utf-8").strip().strip('"').strip("'")
    TOKEN_FILE.unlink(missing_ok=True)
    if ":" not in token or len(token) < 40:
        raise SystemExit("token_shape_invalid")

    lines = []
    seen = False
    for line in CFG.read_text(encoding="utf-8").splitlines():
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            lines.append(f"TELEGRAM_BOT_TOKEN={token}")
            seen = True
        else:
            lines.append(line)
    if not seen:
        lines.append(f"TELEGRAM_BOT_TOKEN={token}")
    CFG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    uid = pwd.getpwnam("zta-desk").pw_uid
    gid = grp.getgrnam("zta-desk").gr_gid
    os.chown(CFG, uid, gid)
    os.chmod(CFG, 0o600)

    subprocess.run(["systemctl", "restart", "zta-desk-telegram.service"], check=True)
    time.sleep(2)
    print("telegram", subprocess.check_output(["systemctl", "is-active", "zta-desk-telegram.service"], text=True).strip())
    print("coa", subprocess.check_output(["systemctl", "is-active", "strike-desk-coa.service"], text=True).strip())

    import requests

    r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
    js = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    print("getMe_ok", js.get("ok"), "bot", (js.get("result") or {}).get("username"))

    chat = None
    for line in CFG.read_text(encoding="utf-8").splitlines():
        if line.startswith("TELEGRAM_CHAT_ID="):
            chat = line.split("=", 1)[1].strip()
            break
    print("chat_public", bool(chat and chat.startswith("@")))
    s = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat,
            "text": "ZeroTouchAlgo desk — channel connected.",
            "disable_web_page_preview": True,
        },
        timeout=10,
    )
    out = s.json() if s.headers.get("content-type", "").startswith("application/json") else {}
    print("send_ok", out.get("ok"), "http", s.status_code)
    if not out.get("ok"):
        print("send_error", out.get("error_code"), out.get("description"))
    print("TOKEN_SET_OK")


if __name__ == "__main__":
    main()
