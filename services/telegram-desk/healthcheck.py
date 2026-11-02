#!/usr/bin/env python3
"""Alert the owner privately if the desk mirror has not saved state in 10 minutes.

During NSE cash hours on a trading day only. Never posts to the public channel.
Uses OWNER_CHAT_ID on the same bot token (private chat). That is the runtime
stand-in for OpenAlgo send_telegram_alert, which needs an interactive MCP session.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

IST = ZoneInfo("Asia/Kolkata")
STATE = Path(os.environ.get("DATA_DIR", "/var/lib/zta-desk")) / "state.json"
HOLIDAYS = {d.strip() for d in os.environ.get("HOLIDAYS", "").split(",") if d.strip()}
OWNER = os.environ.get("OWNER_CHAT_ID", "").strip()
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
START = dt.time(9, 15)
END = dt.time(15, 30)
STALE_MIN = 10


def trading_hours(now: dt.datetime) -> bool:
    if now.weekday() >= 5 or now.date().isoformat() in HOLIDAYS:
        return False
    return START <= now.time() <= END


def notify(text: str) -> None:
    if not OWNER or not TOKEN:
        print(text, file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(
        url,
        json={"chat_id": OWNER, "text": text, "disable_web_page_preview": True},
        timeout=10,
    )


def main() -> int:
    now = dt.datetime.now(IST)
    if not trading_hours(now):
        return 0
    if not STATE.exists():
        notify("Desk Telegram mirror: no state file during cash hours.")
        return 1
    age = now.timestamp() - STATE.stat().st_mtime
    if age > STALE_MIN * 60:
        mins = int(age // 60)
        notify(
            f"Desk Telegram mirror: no successful poll in {mins} minutes during cash hours."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
