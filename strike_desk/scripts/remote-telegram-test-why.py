#!/usr/bin/env python3
"""Why --test-post failed. Never print tokens or chat ids."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

os.environ.clear()
# systemd already has the unit running; read only flags from config via python as zta-desk
cfg = Path("/opt/zta-desk/config.env")
vals = {}
for line in cfg.read_text(encoding="utf-8").splitlines():
    if not line.strip() or line.strip().startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    vals[k.strip()] = v.strip().strip('"').strip("'")

print("DRY_RUN", vals.get("DRY_RUN"))
print("BOARDS", vals.get("BOARDS"))
token = vals.get("TELEGRAM_BOT_TOKEN") or ""
print("token_set", bool(token) and "REPLACE_ME" not in token, "len", len(token))
print("chat_set", bool(vals.get("TELEGRAM_CHAT_ID")))

print("=== journal ===")
p = subprocess.run(
    "journalctl -u zta-desk-telegram.service -n 20 --no-pager -o cat",
    shell=True,
    capture_output=True,
    text=True,
)
text = (p.stdout or "") + (p.stderr or "")
# strip bot tokens if any leaked
import re

text = re.sub(r"bot[0-9]{6,}:[A-Za-z0-9_-]+", "bot***", text)
print(text[-2500:])

# Probe Telegram without printing the body: getMe + sendMessage status only
import requests

if token and "REPLACE_ME" not in token:
    r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
    print("getMe_http", r.status_code, "ok", r.json().get("ok") if r.headers.get("content-type", "").startswith("application/json") else None)
    if r.ok and r.json().get("ok"):
        print("bot_username", r.json().get("result", {}).get("username"))
    chat = vals.get("TELEGRAM_CHAT_ID") or ""
    body = {"chat_id": chat, "text": "ZeroTouchAlgo desk — channel connected.", "disable_web_page_preview": True}
    s = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=body, timeout=10)
    print("send_http", s.status_code)
    if s.headers.get("content-type", "").startswith("application/json"):
        js = s.json()
        print("send_ok", js.get("ok"))
        if not js.get("ok"):
            print("send_error", js.get("error_code"), js.get("description"))
print("WHY_OK")
