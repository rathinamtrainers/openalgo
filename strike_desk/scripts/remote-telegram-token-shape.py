#!/usr/bin/env python3
"""Token shape + Telegram getMe. Never print the token or numeric chat ids."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

cfg = Path("/opt/zta-desk/config.env")
vals = {}
for line in cfg.read_text(encoding="utf-8").splitlines():
    if not line.strip() or line.strip().startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    vals[k.strip()] = v.strip().strip('"').strip("'")

token = vals.get("TELEGRAM_BOT_TOKEN") or ""
chat = vals.get("TELEGRAM_CHAT_ID") or ""
print("DRY_RUN", vals.get("DRY_RUN"))
print("BOARDS", vals.get("BOARDS"))
print("token_len", len(token))
print("token_has_colon", ":" in token)
print("token_placeholder", "REPLACE" in token.upper() or token.startswith("123456789"))
if ":" in token:
    left, right = token.split(":", 1)
    print("token_id_len", len(left), "token_secret_len", len(right), "id_isdigit", left.isdigit())
print("chat_looks_public", chat.startswith("@") if chat else False)
print("chat_is_numeric", chat.lstrip("-").isdigit() if chat else False)

if token and "REPLACE" not in token.upper():
    r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
    print("getMe_http", r.status_code)
    try:
        js = r.json()
    except Exception:
        js = {}
    print("getMe_ok", js.get("ok"))
    if js.get("ok"):
        print("bot_username", (js.get("result") or {}).get("username"))
    else:
        desc = str(js.get("description") or "")
        desc = re.sub(r"bot[0-9]{5,}:[A-Za-z0-9_-]+", "bot***", desc)
        print("getMe_error", js.get("error_code"), desc)

state = Path("/var/lib/zta-desk/state.json")
if state.exists():
    print("state", json.dumps(json.loads(state.read_text(encoding="utf-8")), default=str)[:800])
today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
ledger = Path(f"/var/lib/zta-desk/ledger/{today}.jsonl")
print("ledger_today", ledger.exists(), "lines", 0 if not ledger.exists() else sum(1 for _ in ledger.open()))
print("SHAPE_OK")
